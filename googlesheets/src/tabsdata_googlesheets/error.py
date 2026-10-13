#
# Copyright 2026 Tabsdata Inc.
#

from tabsdatak.error import ApiInitException, ApiRuntimeException, ErrorCode, ErrorDef


class GoogleSheetsRuntimeException(ApiRuntimeException):
    """raised when a read from or a write to google sheets fails"""


class GoogleSheetsErrorCode(ErrorCode):
    GOOGLESHEETS_1 = ErrorDef(ApiInitException, "GoogleSheetsSrcConn validation failed")
    GOOGLESHEETS_2 = ErrorDef(ApiInitException, "GoogleSheetsSrc validation failed")
    GOOGLESHEETS_3 = ErrorDef(ApiInitException, "GoogleSheetsDestConn validation failed")
    GOOGLESHEETS_4 = ErrorDef(ApiInitException, "GoogleSheetsDest validation failed")
    GOOGLESHEETS_5 = ErrorDef(ApiInitException, "Sheet validation failed")
    GOOGLESHEETS_6 = ErrorDef(
        GoogleSheetsRuntimeException,
        "Google Sheets request for spreadsheet {spreadsheet} failed with status {status}: {message}",
    )
    GOOGLESHEETS_7 = ErrorDef(
        GoogleSheetsRuntimeException,
        "Google Sheets received {slots} table(s) for {sheets} sheet(s)",
    )
    GOOGLESHEETS_8 = ErrorDef(
        GoogleSheetsRuntimeException,
        "Columns {columns} aren't in the header row of worksheet {worksheet}",
    )
