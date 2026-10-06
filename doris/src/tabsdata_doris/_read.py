#
# Copyright 2026 Tabsdata Inc.
#

"""the read side of the doris connector, one function per read method. each one
writes a query's result to parquet under the work dir without holding the whole
result in memory
"""

import logging
import re
from pathlib import Path

from tabsdatak.conn.common.types import _convert, cloud_full_path
from tabsdatak.spi import SrcPluginCtx
from tabsdata_doris import DorisSrc, DorisSrcConn, S3Location
from tabsdata_doris._impl import _kv, s3_properties
from tabsdata_doris.error import DorisErrorCode

logger = logging.getLogger(__name__)

# :name bind parameters in the query text, same syntax as the built in sql
# sources. string literals, quoted identifiers and comments are matched first
# without a group so a :name inside them is left alone, and the lookbehind skips
# a :name glued to a word or another colon
BIND_PATTERN = re.compile(
    r"'(?:[^'\\]|\\.)*'"
    r'|"(?:[^"\\]|\\.)*"'
    r"|`[^`]*`"
    r"|--[^\n]*"
    r"|/\*.*?\*/"
    r"|(?<![:\w]):([A-Za-z_][A-Za-z0-9_]*)",
    re.DOTALL,
)

# rows fetched from the mysql protocol cursor per parquet row group
BATCH_ROWS = 65536


# helper function that returns the bind values for this run, the value the
# publisher stored last run with ctx.set_attr wins over the seed in
# initial_values. checks for None instead of truthiness so a stored 0 or "" isn't
# replaced by the seed
def bind_values(ctx: SrcPluginCtx, src: DorisSrc) -> dict:
    if src.initial_values is None:
        return {}
    values = {}
    for name, seed in src.initial_values.items():
        stored = ctx.get_attr(name)
        values[name] = stored if stored is not None else seed
    return values


# helper function that replaces each :name the values have with the value as an
# escaped sql literal. the binds are rendered into the text instead of sent as
# parameters since arrow flight sql and INTO OUTFILE don't take parameters, so
# all three read methods run the same sql. a :name the values don't have is left
# as is
def render(query: str, values: dict) -> str:
    from pymysql.converters import escape_item

    def literal(match: re.Match) -> str:
        name = match.group(1)
        if name is None or name not in values:
            return match.group(0)
        return escape_item(values[name], "utf8mb4")

    return BIND_PATTERN.sub(literal, query)


# --- mysql --------------------------------------------------------------------
#
# an unbuffered cursor streams the rows from the frontend in batches, the arrow
# types come from the column types doris reports so every batch has the same
# schema even when a batch is all nulls


# helper function that maps a mysql protocol column type to an arrow type,
# decimals get precision 38 since the precision doris reports isn't reliable and
# 38 is the largest doris decimal. booleans arrive as TINY so they become 0 and 1
# integers, anything without a match is read as text
def arrow_type(type_code: int, scale: int):
    import pyarrow as pa
    from pymysql.constants import FIELD_TYPE as T

    if type_code in (T.TINY, T.SHORT, T.LONG, T.INT24, T.LONGLONG, T.YEAR):
        return pa.int64()
    if type_code in (T.FLOAT, T.DOUBLE):
        return pa.float64()
    if type_code in (T.DECIMAL, T.NEWDECIMAL):
        return pa.decimal128(38, scale or 0)
    if type_code == T.DATE:
        return pa.date32()
    if type_code in (T.DATETIME, T.TIMESTAMP):
        return pa.timestamp("us")
    return pa.string()


def text(value):
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


def read_mysql(connection, index: int, query: str, work_dir: Path) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pymysql

    parquet = work_dir / f"doris_{index}.parquet"
    try:
        with connection.cursor(pymysql.cursors.SSCursor) as cursor:
            cursor.execute(query)
            if cursor.description is None:
                raise DorisErrorCode.DORIS_16.exception(index=index, message="the query returned no result set")
            schema = pa.schema([pa.field(d[0], arrow_type(d[1], d[5])) for d in cursor.description])
            with pq.ParquetWriter(parquet, schema) as writer:
                while rows := cursor.fetchmany(BATCH_ROWS):
                    columns = zip(*rows)
                    arrays = [
                        pa.array([text(v) for v in column] if field.type == pa.string() else column, type=field.type)
                        for column, field in zip(columns, schema)
                    ]
                    writer.write_batch(pa.record_batch(arrays, schema=schema))
    except pymysql.err.MySQLError as e:
        raise DorisErrorCode.DORIS_16.exception(cause=e, index=index, message=str(e))
    logger.info("doris: query %d read over mysql into %s", index, parquet)
    return parquet


# --- arrow flight sql ---------------------------------------------------------
#
# the frontend plans the query and hands back one endpoint per backend, the
# client fetches the arrow batches from the backends directly so nothing is
# converted row by row. the backends have to be reachable from the tabsdata
# server at the address the frontend reports for them


def connect_arrow_flight(conn: DorisSrcConn):
    try:
        import adbc_driver_flightsql.dbapi as flight_sql
    except ImportError as e:
        raise DorisErrorCode.DORIS_18.exception(cause=e)

    port = conn._arrow_flight_port()
    try:
        connection = flight_sql.connect(
            uri=f"grpc://{conn._host()}:{port}",
            db_kwargs={"username": conn._user(), "password": conn._password()},
            autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute(f"USE `{conn._database()}`")
    except flight_sql.Error as e:
        raise DorisErrorCode.DORIS_4.exception(cause=e, host=conn._host(), port=port)
    return connection


def read_arrow_flight(connection, index: int, query: str, work_dir: Path) -> Path:
    import adbc_driver_flightsql.dbapi as flight_sql
    import pyarrow.parquet as pq

    parquet = work_dir / f"doris_{index}.parquet"
    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            reader = cursor.fetch_record_batch()
            with pq.ParquetWriter(parquet, reader.schema) as writer:
                for batch in reader:
                    writer.write_batch(batch)
    except flight_sql.Error as e:
        raise DorisErrorCode.DORIS_16.exception(cause=e, index=index, message=str(e))
    logger.info("doris: query %d read over arrow flight sql into %s", index, parquet)
    return parquet


# --- s3 export ----------------------------------------------------------------
#
# the reverse of the s3 load method, SELECT ... INTO OUTFILE has the backends
# write the result as parquet under <base_path>/<upload_id>/query_<index>/ and
# the plugin downloads every file under that prefix. a large result can come back
# as several files, which all go to the same output table. exported files are
# left in the bucket like the staged files of the s3 load method, so a bucket
# lifecycle rule should expire them


def s3_filesystem(staging: S3Location):
    from pyarrow import fs

    creds = staging.credentials
    return fs.S3FileSystem(
        access_key=_convert(creds.access_key_id, str),
        secret_key=_convert(creds.secret_access_key, str),
        region=staging._region(),
    )


def read_s3(connection, conn: DorisSrcConn, index: int, query: str, work_dir: Path, upload_id: str) -> list[Path]:
    import pymysql
    from pyarrow import fs

    staging = conn.staging
    if not isinstance(staging, S3Location):
        raise DorisErrorCode.DORIS_13.exception(kind=type(staging).__name__)
    prefix = cloud_full_path(staging._base_path(), f"{upload_id}/query_{index}")
    uri = f"s3://{staging._bucket()}/{prefix}/"
    # INTO OUTFILE goes after the whole select, so a trailing semicolon has to go,
    # and it starts on a new line so a trailing -- comment can't swallow it
    select = query.strip().rstrip(";").rstrip()
    export = f'{select}\nINTO OUTFILE "{uri}part_" FORMAT AS PARQUET PROPERTIES ({_kv(s3_properties(staging))})'
    try:
        with connection.cursor() as cursor:
            cursor.execute(export)
    except pymysql.err.MySQLError as e:
        raise DorisErrorCode.DORIS_16.exception(cause=e, index=index, message=str(e))

    local = work_dir / f"doris_{index}"
    local.mkdir(parents=True, exist_ok=True)
    s3 = s3_filesystem(staging)
    try:
        exported = [
            info.path for info in s3.get_file_info(fs.FileSelector(f"{staging._bucket()}/{prefix}", recursive=True))
            if info.type == fs.FileType.File and info.path.endswith(".parquet")
        ]
        files = []
        for path in sorted(exported):
            target = local / Path(path).name
            fs.copy_files(path, str(target), source_filesystem=s3, destination_filesystem=fs.LocalFileSystem())
            files.append(target)
    except OSError as e:
        raise DorisErrorCode.DORIS_20.exception(cause=e, uri=uri)

    if not files:
        # an empty result may export no file, so the empty table is read over
        # mysql instead to keep the columns
        logger.info("doris: query %d exported no files, reading its columns over mysql", index)
        return [read_mysql(connection, index, f"SELECT * FROM ({select}\n) AS t LIMIT 0", work_dir)]
    logger.info("doris: query %d exported %d file(s) to %s", index, len(files), uri)
    return files
