#
# Copyright 2026 Tabsdata Inc.
#

import hashlib
import logging
import uuid
from pathlib import Path

from tabsdatak.conn.common.types import _convert, cloud_full_path
from tabsdata_doris import CloudLocation, DorisDestConn, DorisSrcConn, S3Location
from tabsdata_doris.error import DorisErrorCode

logger = logging.getLogger(__name__)

# stream load statuses that mean the rows were committed, publish timeout means
# the load committed but isn't visible to queries yet
LOADED = {"Success", "Publish Timeout"}

# prefix of the system columns tabsdata can add to a table, doris tables never
# have them
SYSTEM_COLUMN_PREFIX = "$td."


# helper function that opens a mysql protocol connection to the doris frontend,
# used for the table lookups and truncates, and by the source for its queries
def connect(conn: DorisSrcConn | DorisDestConn):
    import pymysql

    try:
        return pymysql.connect(
            host=conn._host(),
            port=conn._port(),
            user=conn._user(),
            password=conn._password(),
            database=conn._database(),
            autocommit=True,
            connect_timeout=10,
        )
    except pymysql.err.OperationalError as e:
        raise DorisErrorCode.DORIS_4.exception(cause=e, host=conn._host(), port=conn._port())


# helper function that returns the column names of a doris table in order, an
# empty list when the table doesn't exist
def table_columns(connection, database: str, table: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COLUMN_NAME FROM information_schema.columns "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
            (database, table),
        )
        return [row[0] for row in cursor.fetchall()]


def truncate(connection, database: str, table: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE TABLE `{database}`.`{table}`")


def row_count(parquet: Path) -> int:
    import pyarrow.parquet as pq

    return pq.ParquetFile(parquet).metadata.num_rows


# helper function that matches the parquet columns to the doris table columns by
# name, skips the tabsdata system columns and fails on any other column the
# table doesn't have instead of dropping it
def load_columns(connection, database: str, table: str, parquet: Path) -> list[str]:
    import pyarrow.parquet as pq

    existing = {name.lower() for name in table_columns(connection, database, table)}
    if not existing:
        raise DorisErrorCode.DORIS_6.exception(database=database, table=table)
    columns = [name for name in pq.read_schema(parquet).names if not name.startswith(SYSTEM_COLUMN_PREFIX)]
    missing = [name for name in columns if name.lower() not in existing]
    if missing:
        raise DorisErrorCode.DORIS_7.exception(database=database, table=table, columns=", ".join(missing))
    return columns


# helper function that builds the stream load label, doris rejects a second load
# with the same label so appends are labeled with a hash of the file to make a
# retried load of the same file a no op, replaces get a unique label since the
# table was just truncated
def label(table: str, parquet: Path, if_table_exists: str) -> str:
    if if_table_exists == "replace":
        return f"tabsdata_{table}_{uuid.uuid4().hex}"
    digest = hashlib.sha256()
    with open(parquet, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return f"tabsdata_{table}_{digest.hexdigest()[:40]}"


# helper function that sends the parquet file to the doris stream load http api,
# the frontend redirects the load to a backend so the credentials are kept on
# the redirect like curl --location-trusted, strict mode fails the load on any
# row that doesn't fit the table instead of loading it as nulls
def stream_load(conn: DorisDestConn, table: str, parquet: Path, columns: list[str], load_label: str) -> dict:
    import requests

    database = conn._database()
    url = f"http://{conn._host()}:{conn._http_port()}/api/{database}/{table}/_stream_load"
    headers = {
        "Expect": "100-continue",
        "format": "parquet",
        "label": load_label,
        "columns": ",".join(columns),
        "strict_mode": "true",
    }
    session = requests.Session()
    session.should_strip_auth = lambda old_url, new_url: False
    try:
        with open(parquet, "rb") as body:
            response = session.put(url, data=body, headers=headers, auth=(conn._user(), conn._password()),
                                   timeout=(10, 600))
    except requests.exceptions.ConnectionError as e:
        raise DorisErrorCode.DORIS_4.exception(cause=e, host=conn._host(), port=conn._http_port())
    finally:
        session.close()

    try:
        result = response.json()
    except ValueError:
        raise DorisErrorCode.DORIS_5.exception(
            table=table, status=f"HTTP {response.status_code}", message=response.text[:500], error_url="",
        )
    status = result.get("Status")
    if status in LOADED:
        logger.info("doris: loaded %s rows into %s.%s (label %s)", result.get("NumberLoadedRows"), database, table,
                    load_label)
        return result
    if status == "Label Already Exists" and result.get("ExistingJobStatus") == "FINISHED":
        logger.info("doris: %s.%s already has load %s, skipping", database, table, load_label)
        return result
    raise DorisErrorCode.DORIS_5.exception(
        table=f"{database}.{table}",
        status=status,
        message=result.get("Message", ""),
        error_url=result.get("ErrorURL", ""),
    )


# --- s3 staging ---------------------------------------------------------------
#
# same flow as the starrocks connector, the tabsdata transporter uploads each
# parquet file under <base_path>/<upload_id>/<table>/ and a labeled insert reads
# it back through the S3() table function, doris' counterpart of FILES(), staged
# files are left in place like starrocks does so a bucket lifecycle rule should
# expire them


def _kv(items: dict[str, str]) -> str:
    return ", ".join(f'"{k}" = "{v}"' for k, v in items.items())


# helper function that returns the properties doris needs to reach the staging
# location, shared by the S3() table function and SELECT ... INTO OUTFILE. doris
# needs the endpoint so it's derived from the region
def s3_properties(staging: CloudLocation) -> dict[str, str]:
    if isinstance(staging, S3Location):
        creds = staging.credentials
        region = staging._region()
        return {
            "s3.endpoint": f"https://s3.{region}.amazonaws.com",
            "s3.region": region,
            "s3.access_key": _convert(creds.access_key_id, str),
            "s3.secret_key": _convert(creds.secret_access_key, str),
        }
    raise DorisErrorCode.DORIS_13.exception(kind=type(staging).__name__)


# helper function that builds the S3() table function call for a staged file
def build_s3_function(staging: CloudLocation, staged: str) -> str:
    return "S3(" + _kv({"uri": staged, "format": "parquet", **s3_properties(staging)}) + ")"


# helper function that renders the uri of a key under the staging location in
# the form the S3() table function reads
def staged_uri(staging: CloudLocation, key: str) -> str:
    rel = cloud_full_path(staging._base_path(), key)
    if isinstance(staging, S3Location):
        return f"s3://{staging._bucket()}/{rel}"
    raise DorisErrorCode.DORIS_13.exception(kind=type(staging).__name__)


# helper function that builds the transporter destination for an upload under
# the staging location, the credentials are passed to the transporter binary
# through env vars
def transporter_target(staging: CloudLocation, key: str, handler):
    rel = cloud_full_path(staging._base_path(), key)
    if isinstance(staging, S3Location):
        return handler.s3_location(
            uri=f"s3://{staging._bucket()}/{rel}",
            access_key_env=handler.aws_access_key_id_env,
            secret_key_env=handler.aws_secret_access_key_env,
            region_env=handler.aws_region_env,
        )
    raise DorisErrorCode.DORIS_13.exception(kind=type(staging).__name__)


# helper function that resolves the staging credentials into the env vars the
# transporter binary reads
def transporter_env(staging: CloudLocation, handler) -> dict[str, str]:
    if isinstance(staging, S3Location):
        creds = staging.credentials
        return {
            handler.aws_access_key_id_env: _convert(creds.access_key_id, str),
            handler.aws_secret_access_key_env: _convert(creds.secret_access_key, str),
            handler.aws_region_env: staging._region(),
        }
    raise DorisErrorCode.DORIS_13.exception(kind=type(staging).__name__)


# helper function that uploads one parquet file to the staging location under
# <upload_id>/<table_key>/<file name> with the tabsdata transporter and returns
# the staged uri
def upload_parquet(staging: CloudLocation, work_dir: Path, upload_id: str, parquet: Path, table_key: str) -> str:
    from tabsdatak.common._fileformat.taxonomy import DestFileFormat
    from tabsdatak.common._transporter.facade import make_handler
    from tabsdatak.conn.common.file import _output

    handler = make_handler()
    rel = f"{upload_id}/{table_key}/{parquet.name}"
    try:
        _output.copy_one(
            handler=handler,
            source_target_pairs=[(handler.local_path_location(parquet), transporter_target(staging, rel, handler))],
            work_dir=work_dir,
            file_format=DestFileFormat.PARQUET,
            paths=[rel],
            env=transporter_env(staging, handler),
        )
    except Exception as e:
        raise DorisErrorCode.DORIS_11.exception(cause=e, uri=staged_uri(staging, rel))
    return staged_uri(staging, rel)


# helper function that stages the parquet file and runs the insert that reads it
# back, the insert is one doris transaction like a stream load and carries the
# same label so a retried load of the same file is a no op, the columns are named
# on both sides so the tabsdata system columns in the file are left out
def s3_load(connection, conn: DorisDestConn, table: str, parquet: Path, columns: list[str], load_label: str,
            work_dir: Path, upload_id: str) -> None:
    import pymysql

    database = conn._database()
    staged = upload_parquet(conn.staging, work_dir, upload_id, parquet, table)
    names = ", ".join(f"`{name}`" for name in columns)
    insert = (f"INSERT INTO `{database}`.`{table}` WITH LABEL {load_label} ({names}) "
              f"SELECT {names} FROM {build_s3_function(conn.staging, staged)}")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET enable_insert_strict = true")
            cursor.execute(insert)
    except pymysql.err.MySQLError as e:
        message = str(e)
        # doris reports a reused label with the state of the load that used it
        if "has already been used" in message and ("VISIBLE" in message or "COMMITTED" in message):
            logger.info("doris: %s.%s already has load %s, skipping", database, table, load_label)
            return
        raise DorisErrorCode.DORIS_12.exception(cause=e, table=f"{database}.{table}", message=message)
    logger.info("doris: loaded %s into %s.%s (label %s)", staged, database, table, load_label)
