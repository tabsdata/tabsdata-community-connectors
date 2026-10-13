#
# Copyright 2026 Tabsdata Inc.
#

import json
from datetime import date
from urllib.parse import unquote

import polars as pl
import pytest
from conftest import FakeResponse
from tabsdatak.api import Secret
from tabsdatak.conn.common.types import GCPJson
from tabsdatak.error import ApiInitException

from tabsdata_googlesheets import (
    GoogleSheetsDest,
    GoogleSheetsDestConn,
    GoogleSheetsSrc,
    GoogleSheetsSrcConn,
    Sheet,
)
from tabsdata_googlesheets._plugin import GoogleSheetsDestPlugin, GoogleSheetsSrcPlugin
from tabsdata_googlesheets.error import GoogleSheetsRuntimeException

SPREADSHEET = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcd"
CREDENTIALS = GCPJson(json=Secret('{"type": "service_account"}'))


def read(ctx, *sheets, header=True):
    src = GoogleSheetsSrc(sheets=list(sheets) or [Sheet(SPREADSHEET, "Visitors")], header=header)
    return GoogleSheetsSrcPlugin().read_in(ctx, GoogleSheetsSrcConn(credentials=CREDENTIALS), src)


def write(ctx, tmp_path, frames, sheets=None, if_sheet_exists="replace"):
    files = []
    for index, frame in enumerate(frames):
        if frame is None:
            files.append(None)
            continue
        file = tmp_path / f"in_{index}.parquet"
        frame.write_parquet(file)
        files.append(file)
    dest = GoogleSheetsDest(sheets=sheets or [Sheet(SPREADSHEET, "Totals")], if_sheet_exists=if_sheet_exists)
    GoogleSheetsDestPlugin().write_out(ctx, GoogleSheetsDestConn(credentials=CREDENTIALS), dest, files)


def titles(*names):
    return FakeResponse({"sheets": [{"properties": {"title": name}} for name in names]})


# source


def test_header_row_becomes_the_column_names(ctx, sheets_api):
    session = sheets_api(FakeResponse({"values": [["name", "visits"], ["Ana", "3"], ["Bo", "5"]]}))

    tables = read(ctx)

    frame = pl.read_parquet(tables[0][0].file)
    assert unquote(session.received[0]["url"]).endswith(f"{SPREADSHEET}/values/'Visitors'")
    assert session.received[0]["params"]["valueRenderOption"] == "FORMATTED_VALUE"
    assert frame.columns == ["name", "visits"]
    assert frame.schema == pl.Schema({"name": pl.String, "visits": pl.String})
    assert frame["visits"].to_list() == ["3", "5"]


def test_short_rows_and_blank_cells_become_null(ctx, sheets_api):
    sheets_api(FakeResponse({"values": [["a", "b", "c"], ["1"], ["", "2", "3"]]}))

    frame = pl.read_parquet(read(ctx)[0][0].file)

    assert frame.rows() == [("1", None, None), (None, "2", "3")]


def test_blank_and_repeated_header_names(ctx, sheets_api):
    sheets_api(FakeResponse({"values": [["id", "", "id"], ["1", "2", "3", "4"]]}))

    frame = pl.read_parquet(read(ctx)[0][0].file)

    assert frame.columns == ["id", "column_2", "id_2", "column_4"]


def test_without_header_columns_are_numbered(ctx, sheets_api):
    sheets_api(FakeResponse({"values": [["Ana", "3"], ["Bo", "5"]]}))

    frame = pl.read_parquet(read(ctx, header=False)[0][0].file)

    assert frame.columns == ["column_1", "column_2"]
    assert frame.height == 2


def test_range_and_quoted_worksheet_name(ctx, sheets_api):
    session = sheets_api(FakeResponse({"values": [["a"], ["1"]]}))

    read(ctx, Sheet(SPREADSHEET, "Bo's data", range="A1:C10"))

    assert unquote(session.received[0]["url"]).endswith("/values/'Bo''s data'!A1:C10")


def test_empty_worksheet_gives_no_data(ctx, sheets_api):
    sheets_api(FakeResponse({}))

    assert read(ctx) == [[]]


def test_spreadsheet_url_is_accepted():
    sheet = Sheet(f"https://docs.google.com/spreadsheets/d/{SPREADSHEET}/edit#gid=0", "Visitors")

    assert sheet.spreadsheet_id == SPREADSHEET


def test_unshared_spreadsheet_is_explained(ctx, sheets_api):
    sheets_api(FakeResponse({"error": {"message": "The caller does not have permission"}}, status_code=403))

    with pytest.raises(GoogleSheetsRuntimeException, match="share the spreadsheet with the service account"):
        read(ctx)


# destination


def test_replace_clears_and_writes_header_and_rows(ctx, tmp_path, sheets_api):
    session = sheets_api(titles("Totals"), FakeResponse(), FakeResponse())
    frame = pl.DataFrame({"day": [date(2026, 10, 1)], "total": [12.5], "note": [None]})

    write(ctx, tmp_path, [frame])

    clear, append = session.received[1], session.received[2]
    assert unquote(clear["url"]).endswith("/values/'Totals':clear")
    assert unquote(append["url"]).endswith("/values/'Totals'!A1:append")
    assert append["params"]["valueInputOption"] == "RAW"
    assert append["json"]["values"] == [["day", "total", "note"], ["2026-10-01", 12.5, ""]]


def test_missing_worksheet_is_created(ctx, tmp_path, sheets_api):
    session = sheets_api(titles("Sheet1"), FakeResponse(), FakeResponse())

    write(ctx, tmp_path, [pl.DataFrame({"a": [1]})])

    assert session.received[1]["url"].endswith(f"{SPREADSHEET}:batchUpdate")
    assert session.received[1]["json"]["requests"][0]["addSheet"]["properties"]["title"] == "Totals"
    assert session.received[2]["json"]["values"] == [["a"], [1]]


def test_append_follows_the_header_order(ctx, tmp_path, sheets_api):
    session = sheets_api(titles("Totals"), FakeResponse({"values": [["total", "day", "extra"]]}), FakeResponse())

    write(ctx, tmp_path, [pl.DataFrame({"day": ["d1"], "total": [3]})], if_sheet_exists="append")

    assert session.received[2]["json"]["values"] == [[3, "d1", ""]]


def test_append_to_an_empty_worksheet_writes_the_header(ctx, tmp_path, sheets_api):
    session = sheets_api(titles("Totals"), FakeResponse({}), FakeResponse())

    write(ctx, tmp_path, [pl.DataFrame({"a": [1]})], if_sheet_exists="append")

    assert session.received[2]["json"]["values"] == [["a"], [1]]


def test_append_rejects_columns_the_header_doesnt_have(ctx, tmp_path, sheets_api):
    sheets_api(titles("Totals"), FakeResponse({"values": [["a"]]}))

    with pytest.raises(GoogleSheetsRuntimeException, match=r"\['b'\]"):
        write(ctx, tmp_path, [pl.DataFrame({"a": [1], "b": [2]})], if_sheet_exists="append")


def test_large_tables_are_sent_in_chunks(ctx, tmp_path, sheets_api, monkeypatch):
    from tabsdata_googlesheets import _sheets

    monkeypatch.setattr(_sheets, "CHUNK_ROWS", 2)
    session = sheets_api(titles("Totals"), FakeResponse(), FakeResponse(), FakeResponse())

    write(ctx, tmp_path, [pl.DataFrame({"a": [1, 2, 3]})])

    assert [r["json"]["values"] for r in session.received[2:]] == [[["a"], [1]], [[2], [3]]]


def test_text_is_written_raw_and_lists_as_json(ctx, tmp_path, sheets_api):
    session = sheets_api(titles("Totals"), FakeResponse(), FakeResponse())

    write(ctx, tmp_path, [pl.DataFrame({"f": ["=1+1"], "tags": [["x", "y"]]})])

    assert session.received[2]["json"]["values"][1] == ["=1+1", json.dumps(["x", "y"])]


def test_none_tables_are_skipped(ctx, tmp_path, sheets_api):
    session = sheets_api()

    write(ctx, tmp_path, [None])

    assert session.received == []


# validation


@pytest.mark.parametrize(
    "make",
    [
        lambda: Sheet("not a spreadsheet!", "Visitors"),
        lambda: Sheet(SPREADSHEET, ""),
        lambda: GoogleSheetsSrc(sheets=[]),
        lambda: GoogleSheetsDest(sheets=[Sheet(SPREADSHEET, "Totals", range="A1:B2")]),
        lambda: GoogleSheetsDest(sheets=[Sheet(SPREADSHEET, "Totals"), Sheet(SPREADSHEET, "Totals")]),
    ],
)
def test_invalid_config_is_rejected(make):
    with pytest.raises(ApiInitException):
        make()


def test_key_is_not_serialized():
    conn = GoogleSheetsSrcConn(credentials=GCPJson(json=Secret('{"private_key": "very secret"}')))

    assert "very secret" not in conn.to_json()


def test_source_survives_serialization():
    src = GoogleSheetsSrc(sheets=[Sheet(SPREADSHEET, "Visitors", range="A1:B9")], header=False)

    assert GoogleSheetsSrc.from_json(src.to_json()) == src
