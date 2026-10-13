#
# Copyright 2026 Tabsdata Inc.
#

"""google sheets source and destination connector for tabsdata

the source reads worksheets for a publisher, one output table per sheet. the
service account key is stored in the connection on the publisher's collection,
and each spreadsheet has to be shared with the service account's email:

    @publisher(
        source=GoogleSheetsSrc(sheets=[Sheet("<spreadsheet id or url>", "Visitors")]),
        output_tables=["visitors"],
    )
    def pub_sheets(visitors: TableFrameSpec) -> TableFrameSpec:
        return visitors

the destination writes subscriber tables to worksheets, creating a worksheet
when it doesn't exist:

    @subscriber(
        input_tables=["gold/daily_totals"],
        destination=GoogleSheetsDest(sheets=[Sheet("<spreadsheet id or url>", "Totals")]),
    )
    def sub_sheets(daily_totals: TableFrameSpec) -> TableFrameSpec:
        return daily_totals
"""

import re
from dataclasses import KW_ONLY
from typing import Annotated, Literal

import tabsdatak._api as _api
from tabsdatak.api import Dest, Src
from tabsdatak.conn.common.types import GCPJson
from tabsdatak.spi import Conn

from tabsdata_googlesheets.error import GoogleSheetsErrorCode

# matches the spreadsheet id on its own or inside a spreadsheet url
SPREADSHEET_ID = re.compile(r"^(?:https://docs\.google\.com/spreadsheets/d/)?([A-Za-z0-9_-]+)(?:[/?#].*)?$")


@_api.dataclass(GoogleSheetsErrorCode.GOOGLESHEETS_5)
class Sheet:
    """worksheet of a spreadsheet, the spreadsheet is its id or its url and the
    worksheet is the tab name. range limits a source to part of the worksheet in
    A1 notation, destinations always write the whole worksheet
    """

    spreadsheet: str
    worksheet: str
    _: KW_ONLY
    range: str | None = None

    def __post_init__(self) -> None:
        if SPREADSHEET_ID.match(self.spreadsheet) is None:
            raise ValueError(f"not a spreadsheet id or url: {self.spreadsheet!r}")
        if not self.worksheet:
            raise ValueError("worksheet can't be empty")

    @property
    def spreadsheet_id(self) -> str:
        return SPREADSHEET_ID.match(self.spreadsheet).group(1)


# a source or destination needs at least one sheet since each sheet is one table
Sheets = Annotated[list[Sheet], _api.NON_EMPTY_LIST_FIELD]


@_api.dataclass(GoogleSheetsErrorCode.GOOGLESHEETS_1, kw_only=True)
class GoogleSheetsSrcConn(Conn):
    """connection for GoogleSheetsSrc that stores the service account key, the
    service account only needs viewer access to the spreadsheets
    """

    credentials: GCPJson


@_api.dataclass(GoogleSheetsErrorCode.GOOGLESHEETS_2, kw_only=True)
class GoogleSheetsSrc(Src):
    """source that reads each sheet and publishes it as one table, tables map to
    the publisher's output tables in order. every column is text, header uses the
    first row as column names, otherwise columns are named column_1, column_2 and
    so on. sheets are reloaded in full each run
    """

    sheets: Sheets
    header: bool = True


@_api.dataclass(GoogleSheetsErrorCode.GOOGLESHEETS_3, kw_only=True)
class GoogleSheetsDestConn(Conn):
    """connection for GoogleSheetsDest that stores the service account key, the
    service account needs editor access to the spreadsheets
    """

    credentials: GCPJson


@_api.dataclass(GoogleSheetsErrorCode.GOOGLESHEETS_4, kw_only=True)
class GoogleSheetsDest(Dest):
    """destination that writes subscriber tables to worksheets, tables map to the
    subscriber's input tables in order. if_sheet_exists defaults to replace which
    clears the worksheet and writes the table with a header row, append adds the
    rows under the existing ones matching columns by the header row
    """

    sheets: Sheets
    if_sheet_exists: Literal["append", "replace"] = "replace"

    def __post_init__(self) -> None:
        if any(sheet.range for sheet in self.sheets):
            raise ValueError("range can't be set on a destination sheet")
        keys = [(sheet.spreadsheet_id, sheet.worksheet) for sheet in self.sheets]
        if len(set(keys)) != len(keys):
            raise ValueError("the same worksheet is listed more than once")


__all__ = [
    "GoogleSheetsDest",
    "GoogleSheetsDestConn",
    "GoogleSheetsSrc",
    "GoogleSheetsSrcConn",
    "Sheet",
]
