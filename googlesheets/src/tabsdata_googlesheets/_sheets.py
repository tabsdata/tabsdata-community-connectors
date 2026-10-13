#
# Copyright 2026 Tabsdata Inc.
#

import json
import math
from datetime import date, datetime, time
from urllib.parse import quote

import polars as pl
from tabsdatak.conn.common.types import GCPJson

from tabsdata_googlesheets import Sheet
from tabsdata_googlesheets.error import GoogleSheetsErrorCode

API = "https://sheets.googleapis.com/v4/spreadsheets"
READ_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/spreadsheets"
TIMEOUT = 60
# rows sent per append request, keeps each request well under the api's size limit
CHUNK_ROWS = 5000
# most characters google sheets allows in one cell
MAX_CELL = 50000


# helper function that opens an authorized session for the service account key,
# google-auth refreshes the access token on its own when it expires
def open_session(credentials: GCPJson, scope: str):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    info = json.loads(credentials.json.value())
    return AuthorizedSession(service_account.Credentials.from_service_account_info(info, scopes=[scope]))


# helper function that sends a request and raises on a failure with the message
# google returns, a 403 usually means the spreadsheet isn't shared with the
# service account
def call(session, method: str, url: str, sheet: Sheet, **kwargs) -> dict:
    response = session.request(method, url, timeout=TIMEOUT, **kwargs)
    if response.status_code >= 400:
        try:
            message = response.json()["error"]["message"]
        except Exception:
            message = response.text
        if response.status_code == 403:
            message += ", share the spreadsheet with the service account's email"
        raise GoogleSheetsErrorCode.GOOGLESHEETS_6.exception(
            spreadsheet=sheet.spreadsheet_id, status=response.status_code, message=message
        )
    return response.json() if response.content else {}


# helper function that returns a worksheet name in A1 notation, quoted so names
# with spaces or symbols work
def a1(worksheet: str, cells: str | None = None) -> str:
    name = "'" + worksheet.replace("'", "''") + "'"
    return f"{name}!{cells}" if cells else name


def values_url(sheet: Sheet, cells: str | None = None, action: str = "") -> str:
    return f"{API}/{sheet.spreadsheet_id}/values/{quote(a1(sheet.worksheet, cells), safe='')}{action}"


def read_values(session, sheet: Sheet) -> list[list[str]]:
    params = {"valueRenderOption": "FORMATTED_VALUE", "majorDimension": "ROWS"}
    body = call(session, "GET", values_url(sheet, sheet.range), sheet, params=params)
    return body.get("values", [])


# helper function that builds the column names from the header row, blank names
# become column_<n> and repeated names get a _<n> suffix since a table can't have
# two columns with the same name
def column_names(header: list[str], width: int) -> list[str]:
    names, seen = [], {}
    for index in range(width):
        name = (header[index] if index < len(header) else "").strip() or f"column_{index + 1}"
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return names


# helper function that turns the worksheet values into a table of text columns,
# the api leaves out empty cells at the end of a row so short rows are padded
# with nulls, and blank cells become null
def to_frame(values: list[list[str]], header: bool) -> pl.DataFrame:
    width = max(len(row) for row in values)
    names = column_names(values[0] if header else [], width)
    rows = values[1:] if header else values
    columns = {name: [] for name in names}
    for row in rows:
        for index, name in enumerate(names):
            cell = row[index] if index < len(row) else None
            columns[name].append(cell if cell != "" else None)
    return pl.DataFrame(columns, schema={name: pl.String for name in names})


# helper function that converts a table value into one the api accepts, numbers
# and booleans stay as they are so the sheet treats them as numbers, nulls become
# blank cells and everything else is written as text, cut at the cell limit
def to_cell(value):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    text = json.dumps(value, default=str) if isinstance(value, list | dict) else str(value)
    return text[:MAX_CELL]


def to_rows(frame: pl.DataFrame) -> list[list]:
    return [[to_cell(value) for value in row] for row in frame.iter_rows()]


def worksheet_titles(session, sheet: Sheet) -> list[str]:
    body = call(session, "GET", f"{API}/{sheet.spreadsheet_id}", sheet, params={"fields": "sheets.properties.title"})
    return [entry["properties"]["title"] for entry in body.get("sheets", [])]


def add_worksheet(session, sheet: Sheet) -> None:
    request = {"requests": [{"addSheet": {"properties": {"title": sheet.worksheet}}}]}
    call(session, "POST", f"{API}/{sheet.spreadsheet_id}:batchUpdate", sheet, json=request)


def clear_worksheet(session, sheet: Sheet) -> None:
    call(session, "POST", values_url(sheet, action=":clear"), sheet, json={})


def header_row(session, sheet: Sheet) -> list[str]:
    body = call(session, "GET", values_url(sheet, "1:1"), sheet)
    return (body.get("values") or [[]])[0]


# helper function that appends rows under the last row of the worksheet in
# chunks, RAW keeps values as they are so text starting with = isn't run as a
# formula
def append_rows(session, sheet: Sheet, rows: list[list]) -> None:
    params = {"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}
    for start in range(0, len(rows), CHUNK_ROWS):
        chunk = rows[start : start + CHUNK_ROWS]
        call(session, "POST", values_url(sheet, "A1", ":append"), sheet, params=params, json={"values": chunk})


# helper function that writes a table to a worksheet, creating the worksheet when
# it doesn't exist. replace clears it and writes the header and rows, append
# writes under the existing rows with the columns put in the header row's order
def write_frame(session, sheet: Sheet, frame: pl.DataFrame, if_sheet_exists: str) -> None:
    if sheet.worksheet not in worksheet_titles(session, sheet):
        add_worksheet(session, sheet)
    elif if_sheet_exists == "replace":
        clear_worksheet(session, sheet)
    header = header_row(session, sheet) if if_sheet_exists == "append" else []
    if not header:
        append_rows(session, sheet, [frame.columns, *to_rows(frame)])
        return
    extra = [column for column in frame.columns if column not in header]
    if extra:
        raise GoogleSheetsErrorCode.GOOGLESHEETS_8.exception(columns=extra, worksheet=sheet.worksheet)
    ordered = frame.select([pl.col(name) if name in frame.columns else pl.lit(None).alias(name) for name in header])
    append_rows(session, sheet, to_rows(ordered))
