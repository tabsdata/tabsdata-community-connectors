#
# Copyright 2026 Tabsdata Inc.
#

import logging

import polars as pl
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
)

from tabsdata_googlesheets import (
    GoogleSheetsDest,
    GoogleSheetsDestConn,
    GoogleSheetsSrc,
    GoogleSheetsSrcConn,
    _sheets,
)
from tabsdata_googlesheets.error import GoogleSheetsErrorCode

logger = logging.getLogger(__name__)


class GoogleSheetsSrcPlugin(SrcPlugin[GoogleSheetsSrcConn, GoogleSheetsSrc]):
    def read_in(
        self,
        ctx: SrcPluginCtx,
        conn: GoogleSheetsSrcConn,
        src: GoogleSheetsSrc,
    ) -> list[list[TableFileInput]]:
        session = _sheets.open_session(conn.credentials, _sheets.READ_SCOPE)
        tables = []
        for index, sheet in enumerate(src.sheets):
            values = _sheets.read_values(session, sheet)
            if not values:
                # an empty worksheet has no columns to publish, so the table gets
                # no new data
                logger.info("google sheets: %s is empty", sheet.worksheet)
                tables.append([])
                continue
            file = ctx.work_dir / f"googlesheets_{index}.parquet"
            frame = _sheets.to_frame(values, src.header)
            frame.write_parquet(file)
            logger.info("google sheets: %d rows read from %s", frame.height, sheet.worksheet)
            tables.append([TableFileInput(file=file)])
        return tables


class GoogleSheetsDestPlugin(DestPlugin[GoogleSheetsDestConn, GoogleSheetsDest]):
    def write_out(
        self,
        ctx: DestContext,
        conn: GoogleSheetsDestConn,
        dest: GoogleSheetsDest,
        tables: list[TableFileSpec],
    ) -> None:
        if len(tables) != len(dest.sheets):
            raise GoogleSheetsErrorCode.GOOGLESHEETS_7.exception(slots=len(tables), sheets=len(dest.sheets))
        session = _sheets.open_session(conn.credentials, _sheets.WRITE_SCOPE)
        for sheet, parquet in zip(dest.sheets, tables):
            if parquet is None:
                # skip tables the subscriber returned None for
                continue
            frame = pl.read_parquet(parquet)
            _sheets.write_frame(session, sheet, frame, dest.if_sheet_exists)
            logger.info("google sheets: %d rows written to %s", frame.height, sheet.worksheet)


GOOGLE_SHEETS_SRC = SrcDef(
    conn=GoogleSheetsSrcConn,
    type_="googlesheets-in",
    system="Google Sheets",
    src_version="v1",
    src=GoogleSheetsSrc,
    table_mode=TableMode.SINGLE,
    cardinality=lambda src: len(src.sheets),
    plugin=GoogleSheetsSrcPlugin,
    explorer=None,
    icon=None,
)

GOOGLE_SHEETS_DEST = DestDef(
    conn=GoogleSheetsDestConn,
    type_="googlesheets-out",
    system="Google Sheets",
    dest_version="v1",
    dest=GoogleSheetsDest,
    cardinality=lambda dest: len(dest.sheets),
    plugin=GoogleSheetsDestPlugin,
    explorer=None,
    icon=None,
)
