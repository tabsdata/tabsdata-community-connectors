#
# Copyright 2026 Tabsdata Inc.
#

import json

import polars as pl
import pytest
from conftest import FakeResponse
from tabsdatak.api import Secret
from tabsdatak.error import ApiInitException

from tabsdata_slack import Message, SlackDest, SlackDestConn, SlackSrc, SlackSrcConn, _slack
from tabsdata_slack._plugin import SlackDestPlugin, SlackSrcPlugin
from tabsdata_slack.error import SlackRuntimeException

SINCE = "2026-10-01T00:00:00Z"
CHANNEL = "C0123ABCDE"


def message(ts, text="hello", **extra):
    return {"type": "message", "ts": ts, "user": "U1", "text": text, **extra}


def history(*messages, cursor=None):
    body = {"ok": True, "messages": list(messages), "has_more": bool(cursor)}
    if cursor:
        body["response_metadata"] = {"next_cursor": cursor}
    return FakeResponse(body)


def read(ctx, *channels):
    src = SlackSrc(channels=list(channels) or [CHANNEL], since=SINCE)
    return SlackSrcPlugin().read_in(ctx, SlackSrcConn(token=Secret("xoxb-test")), src)


def post(ctx, tmp_path, frames, messages):
    files = []
    for index, frame in enumerate(frames):
        if frame is None:
            files.append(None)
            continue
        file = tmp_path / f"in_{index}.parquet"
        frame.write_parquet(file)
        files.append(file)
    SlackDestPlugin().write_out(ctx, SlackDestConn(token=Secret("xoxb-test")), SlackDest(messages=messages), files)


# source


def test_first_run_reads_every_page_from_since(ctx, slack_api):
    session = slack_api(
        history(message("1791000300.000200"), message("1791000200.000100"), cursor="page2"),
        history(message("1791000100.000100", thread_ts="1791000100.000100", reply_count=2)),
    )

    frame = pl.read_parquet(read(ctx)[0][0].file)

    first, second = session.received
    assert first["url"].endswith("/conversations.history")
    assert first["params"]["oldest"] == _slack.to_epoch(SINCE)
    assert second["params"]["cursor"] == "page2"
    assert frame.schema == _slack.SCHEMA
    assert frame["ts"].to_list() == ["1791000100.000100", "1791000200.000100", "1791000300.000200"]
    assert frame["reply_count"].to_list() == [2, 0, 0]


def test_next_run_starts_after_the_newest_message(ctx, slack_api):
    slack_api(history(message("1791000200.000100"), message("1791000100.000100")))
    read(ctx)
    assert ctx.state[f"slack.{CHANNEL}"] == "1791000200.000100"

    session = slack_api(history())
    assert read(ctx) == [[]]

    assert session.received[0]["params"]["oldest"] == "1791000200.000100"


def test_message_columns_and_raw_json(ctx, slack_api):
    original = message("1791000100.000100", "fixed it", edited={"ts": "1791000150.000000"}, subtype="bot_message")
    slack_api(history(original))

    row = pl.read_parquet(read(ctx)[0][0].file).row(0, named=True)

    assert row["text"] == "fixed it"
    assert row["channel"] == CHANNEL
    assert row["subtype"] == "bot_message"
    assert row["posted_at"].timestamp() == pytest.approx(1791000100.0001)
    assert row["edited_at"] is not None
    assert json.loads(row["raw"]) == original


def test_channel_names_are_looked_up(ctx, slack_api):
    session = slack_api(
        FakeResponse({"ok": True, "channels": [{"name": "support", "id": "C0SUPPORT1"}]}),
        history(message("1791000100.000100")),
    )

    read(ctx, "#support")

    assert session.received[0]["url"].endswith("/conversations.list")
    assert session.received[1]["params"]["channel"] == "C0SUPPORT1"
    assert "slack.C0SUPPORT1" in ctx.state


def test_unknown_channel_name(ctx, slack_api):
    slack_api(FakeResponse({"ok": True, "channels": []}))

    with pytest.raises(SlackRuntimeException, match="support not found"):
        read(ctx, "support")


def test_app_not_in_channel_is_explained(ctx, slack_api):
    slack_api(FakeResponse({"ok": False, "error": "not_in_channel"}))

    with pytest.raises(SlackRuntimeException, match="invite the slack app to the channel"):
        read(ctx)


def test_missing_scope_names_the_scope(ctx, slack_api):
    slack_api(FakeResponse({"ok": False, "error": "missing_scope", "needed": "channels:history"}))

    with pytest.raises(SlackRuntimeException, match="missing_scope channels:history"):
        read(ctx)


def test_rate_limit_is_retried(ctx, slack_api):
    session = slack_api(FakeResponse(status_code=429, headers={"Retry-After": "1"}), history(message("1791000100.1")))

    read(ctx)

    assert len(session.received) == 2


# destination


def test_message_has_title_count_and_table(ctx, tmp_path, slack_api):
    session = slack_api(FakeResponse())
    frame = pl.DataFrame({"check": ["nulls", "dupes"], "failed": [3, None]})

    post(ctx, tmp_path, [frame], [Message(CHANNEL, title="Failed checks")])

    body = session.received[0]["json"]
    assert session.received[0]["url"].endswith("/chat.postMessage")
    assert body["channel"] == CHANNEL
    assert body["text"] == "*Failed checks*\n2 rows\n```\ncheck  failed\n-----  ------\nnulls  3\ndupes\n```"


def test_long_tables_show_the_first_rows(ctx, tmp_path, slack_api):
    session = slack_api(FakeResponse())

    post(ctx, tmp_path, [pl.DataFrame({"n": list(range(30))})], [Message(CHANNEL, max_rows=2)])

    text = session.received[0]["json"]["text"]
    assert text.startswith("30 rows\n")
    assert text.endswith("_first 2 rows shown_")
    # header, separator and the two rows
    assert len(text.split("```")[1].strip().split("\n")) == 4


def test_columns_limit_what_is_shown(ctx, tmp_path, slack_api):
    session = slack_api(FakeResponse())

    post(ctx, tmp_path, [pl.DataFrame({"a": [1], "b": [2]})], [Message(CHANNEL, columns=["b"])])

    assert "a" not in session.received[0]["json"]["text"].split("```")[1]


def test_unknown_columns_are_rejected(ctx, tmp_path, slack_api):
    slack_api()

    with pytest.raises(SlackRuntimeException, match=r"\['c'\]"):
        post(ctx, tmp_path, [pl.DataFrame({"a": [1]})], [Message(CHANNEL, columns=["c"])])


def test_empty_and_none_tables_are_skipped(ctx, tmp_path, slack_api):
    session = slack_api(FakeResponse())

    post(
        ctx,
        tmp_path,
        [pl.DataFrame({"a": []}), None, pl.DataFrame({"a": []})],
        [Message(CHANNEL), Message(CHANNEL), Message(CHANNEL, skip_empty=False, max_rows=0)],
    )

    assert [r["json"]["text"] for r in session.received] == ["0 rows"]


def test_long_cells_are_cut(ctx, tmp_path, slack_api):
    session = slack_api(FakeResponse())

    post(ctx, tmp_path, [pl.DataFrame({"a": ["x" * 100]})], [Message(CHANNEL)])

    assert "x" * _slack.MAX_CELL not in session.received[0]["json"]["text"]


# validation


@pytest.mark.parametrize(
    "make",
    [
        lambda: SlackSrc(channels=[], since=SINCE),
        lambda: SlackSrc(channels=["Not A Channel"], since=SINCE),
        lambda: SlackSrc(channels=["support", "#support"], since=SINCE),
        lambda: SlackSrc(channels=["support"], since="yesterday"),
        lambda: Message("support", max_rows=500),
        lambda: SlackDest(messages=[]),
    ],
)
def test_invalid_config_is_rejected(make):
    with pytest.raises(ApiInitException):
        make()


def test_token_is_not_serialized():
    assert "xoxb-secret" not in SlackSrcConn(token=Secret("xoxb-secret")).to_json()


def test_destination_survives_serialization():
    dest = SlackDest(messages=[Message("alerts", title="t", max_rows=3, columns=["a"], skip_empty=False)])

    assert SlackDest.from_json(dest.to_json()) == dest
