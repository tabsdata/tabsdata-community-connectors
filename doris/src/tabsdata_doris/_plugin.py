#
# Copyright 2026 Tabsdata Inc.
#

import logging
import uuid

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
from tabsdata_doris import DorisDest, DorisDestConn, DorisSrc, DorisSrcConn
from tabsdata_doris.error import DorisErrorCode

logger = logging.getLogger(__name__)


class DorisSrcPlugin(SrcPlugin[DorisSrcConn, DorisSrc]):
    def read_in(
        self,
        ctx: SrcPluginCtx,
        conn: DorisSrcConn,
        src: DorisSrc,
    ) -> list[list[TableFileInput]]:
        # imported here so registry scans don't pay for pymysql, pyarrow and the
        # arrow flight driver, same as the destination
        from tabsdata_doris import _impl, _read

        if src.read_method == "arrow_flight" and conn._arrow_flight_port() is None:
            raise DorisErrorCode.DORIS_17.exception()
        if src.read_method == "s3" and conn.staging is None:
            raise DorisErrorCode.DORIS_19.exception()

        values = _read.bind_values(ctx, src)
        queries = [_read.render(query, values) for query in src.queries]
        # exports go under a per run prefix so concurrent runs on the same bucket
        # don't collide, same as the destination's s3 load method
        upload_id = uuid.uuid4().hex
        if src.read_method == "arrow_flight":
            connection = _read.connect_arrow_flight(conn)
        else:
            connection = _impl.connect(conn)
        files = []
        try:
            for index, query in enumerate(queries):
                if src.read_method == "mysql":
                    files.append([_read.read_mysql(connection, index, query, ctx.work_dir)])
                elif src.read_method == "arrow_flight":
                    files.append([_read.read_arrow_flight(connection, index, query, ctx.work_dir)])
                else:
                    files.append(_read.read_s3(connection, conn, index, query, ctx.work_dir, upload_id))
        finally:
            connection.close()
        return to_table_file_inputs(files)


class DorisDestPlugin(DestPlugin[DorisDestConn, DorisDest]):
    def write_out(
        self,
        ctx: DestContext,
        conn: DorisDestConn,
        dest: DorisDest,
        tables: list[TableFileSpec],
    ) -> None:
        # imported here so registry scans don't pay for pymysql, requests and the
        # transporter, same as the starrocks connector
        from tabsdata_doris import _impl

        if len(tables) != len(dest.tables):
            raise DorisErrorCode.DORIS_3.exception(slots=len(tables), tables=len(dest.tables))
        if dest.load_method == "s3" and conn.staging is None:
            raise DorisErrorCode.DORIS_10.exception()

        # each table loads on its own since both load methods commit one table
        # per load, a failure after some tables loaded leaves those tables loaded
        database = conn._database()
        # staged files go under a per run prefix so concurrent runs on the same
        # bucket don't collide, same as the starrocks connector
        upload_id = uuid.uuid4().hex
        connection = _impl.connect(conn)
        try:
            for table, parquet in zip(dest.tables, tables):
                if parquet is None:
                    # skips tables the subscriber returned None for
                    continue
                columns = _impl.load_columns(connection, database, table, parquet)
                if dest.if_table_exists == "replace":
                    _impl.truncate(connection, database, table)
                if _impl.row_count(parquet) == 0:
                    # doris rejects a load with no rows
                    logger.info("doris: no rows for %s.%s, skipping the load", database, table)
                    continue
                load_label = _impl.label(table, parquet, dest.if_table_exists)
                if dest.load_method == "s3":
                    _impl.s3_load(connection, conn, table, parquet, columns, load_label, ctx.work_dir, upload_id)
                else:
                    _impl.stream_load(conn, table, parquet, columns, load_label)
        finally:
            connection.close()


DORIS_SRC = SrcDef(
    conn=DorisSrcConn,
    type_="doris-bulk-in",
    system="Apache Doris",
    src_version="v1",
    src=DorisSrc,
    # ARRAY since an s3 export can come back as several files for one query
    table_mode=TableMode.ARRAY,
    cardinality=lambda src: len(src.queries),
    plugin=DorisSrcPlugin,
    explorer=None,
    icon=None,
)

DORIS_DEST = DestDef(
    conn=DorisDestConn,
    type_="doris-bulk-out",
    system="Apache Doris",
    dest_version="v1",
    dest=DorisDest,
    cardinality=lambda dest: len(dest.tables),
    plugin=DorisDestPlugin,
    explorer=None,
    icon=None,
)
