#
# Copyright 2026 Tabsdata Inc.
#

import logging
import re

from tabsdatak.spi import (
    DestContext,
    DestDef,
    DestPlugin,
    SrcDef,
    SrcPlugin,
    SrcPluginCtx,
    TableFileInput,
    TableFileSpec,
    TableMode,
    to_table_file_inputs,
)
from tabsdata_motherduck import MotherDuckDest, MotherDuckDestConn, MotherDuckSrc, MotherDuckSrcConn, resolve
from tabsdata_motherduck.error import MotherDuckErrorCode

logger = logging.getLogger(__name__)

# duckdb's named bind parameters, $name in the query text
BIND_PATTERN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


# helper function that connects to motherduck and switches to the database, the
# destination creates it if it doesn't exist and the source expects it to exist
def connect(conn: MotherDuckSrcConn | MotherDuckDestConn, create: bool):
    import duckdb

    database = resolve(conn.database)
    try:
        con = duckdb.connect("md:", config={"motherduck_token": resolve(conn.token)})
        if create:
            con.execute(f'CREATE DATABASE IF NOT EXISTS "{database}"')
        con.execute(f'USE "{database}"')
    except duckdb.Error as e:
        raise MotherDuckErrorCode.MOTHERDUCK_7.exception(cause=e, database=database)
    return con


# helper function that returns the bind values for this run, the value the
# publisher stored last run with ctx.set_attr wins over the seed in
# initial_values. checks for None instead of truthiness so a stored 0 or "" isn't
# replaced by the seed
def bind_values(ctx: SrcPluginCtx, src: MotherDuckSrc) -> dict:
    if src.initial_values is None:
        return {}
    values = {}
    for name, seed in src.initial_values.items():
        stored = ctx.get_attr(name)
        values[name] = stored if stored is not None else seed
    return values


# helper function that picks the bind values a query uses, duckdb fails a query
# that's passed a parameter it doesn't reference so every query only gets its own
def query_params(query: str, values: dict) -> dict | None:
    params = {name: values[name] for name in BIND_PATTERN.findall(query) if name in values}
    return params or None


class MotherDuckSrcPlugin(SrcPlugin[MotherDuckSrcConn, MotherDuckSrc]):
    def read_in(
        self,
        ctx: SrcPluginCtx,
        conn: MotherDuckSrcConn,
        src: MotherDuckSrc,
    ) -> list[list[TableFileInput]]:
        import duckdb

        values = bind_values(ctx, src)
        # duckdb streams each result from motherduck straight into a local parquet
        # file under the work dir instead of pulling it into memory first
        con = connect(conn, create=False)
        files = []
        try:
            for index, query in enumerate(src.queries):
                parquet = ctx.work_dir / f"motherduck_{index}.parquet"
                try:
                    con.sql(query, params=query_params(query, values)).write_parquet(str(parquet))
                except duckdb.Error as e:
                    raise MotherDuckErrorCode.MOTHERDUCK_8.exception(cause=e, index=index, query=query)
                logger.info("motherduck: query %d written to %s", index, parquet)
                files.append([parquet])
        finally:
            con.close()
        return to_table_file_inputs(files)


class MotherDuckDestPlugin(DestPlugin[MotherDuckDestConn, MotherDuckDest]):
    def write_out(
        self,
        ctx: DestContext,
        conn: MotherDuckDestConn,
        dest: MotherDuckDest,
        tables: list[TableFileSpec],
    ) -> None:
        if len(tables) != len(dest.tables):
            raise MotherDuckErrorCode.MOTHERDUCK_3.exception(slots=len(tables), tables=len(dest.tables))

        # duckdb reads the parquet files passed in by tabsdata and bulk loads them
        # into motherduck instead of sending insert statements like the postgres
        # connector
        con = connect(conn, create=True)
        try:
            con.begin()
            for table, parquet in zip(dest.tables, tables):
                if parquet is None:
                    # skip tables the subscriber returned None for
                    continue
                try:
                    source = "read_parquet('{}')".format(str(parquet).replace("'", "''"))
                    if dest.if_table_exists == "replace":
                        con.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM {source}')
                    else:
                        con.execute(f'CREATE TABLE IF NOT EXISTS "{table}" AS SELECT * FROM {source} LIMIT 0')
                        con.execute(f'INSERT INTO "{table}" BY NAME SELECT * FROM {source}')
                except Exception as e:
                    raise MotherDuckErrorCode.MOTHERDUCK_4.exception(cause=e, table=table)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


MOTHERDUCK_SRC = SrcDef(
    conn=MotherDuckSrcConn,
    type_="motherduck-in",
    system="MotherDuck",
    src_version="v1",
    src=MotherDuckSrc,
    table_mode=TableMode.SINGLE,
    cardinality=lambda src: len(src.queries),
    plugin=MotherDuckSrcPlugin,
    explorer=None,
    icon=None,
)

MOTHERDUCK_DEST = DestDef(
    conn=MotherDuckDestConn,
    type_="motherduck-out",
    system="MotherDuck",
    dest_version="v1",
    dest=MotherDuckDest,
    cardinality=lambda dest: len(dest.tables),
    plugin=MotherDuckDestPlugin,
    explorer=None,
    icon=None,
)
