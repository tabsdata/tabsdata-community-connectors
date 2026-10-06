#
# Copyright 2026 Tabsdata Inc.
#

import logging
import uuid

from tabsdatak.spi import DestContext, DestDef, DestPlugin, TableFileSpec
from tabsdata_doris import DorisDest, DorisDestConn
from tabsdata_doris.error import DorisErrorCode

logger = logging.getLogger(__name__)


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
