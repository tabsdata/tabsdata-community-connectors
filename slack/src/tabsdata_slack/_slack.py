#
# Copyright 2026 Tabsdata Inc.
#

import json
import time
from datetime import UTC, datetime

import polars as pl
import requests

from tabsdata_slack import resolve
from tabsdata_slack.error import SlackErrorCode

API = "https://slack.com/api"
TIMEOUT = 30
PAGE_SIZE = 200
RETRIES = 5
# slack cuts a message's text at 40,000 characters, the table stops well before
MAX_TEXT = 39000
MAX_CELL = 40
TIMESTAMP = pl.Datetime("us", "UTC")

SCHEMA = pl.Schema(
    {
        "ts": pl.String,
        "channel": pl.String,
        "user_id": pl.String,
        "bot_id": pl.String,
        "subtype": pl.String,
        "text": pl.String,
        "thread_ts": pl.String,
        "reply_count": pl.Int64,
        "posted_at": TIMESTAMP,
        "edited_at": TIMESTAMP,
        "raw": pl.String,
    }
)

# what to tell the user for the errors that come from how the slack app is set up
HINTS = {
    "not_in_channel": "invite the slack app to the channel",
    "invalid_auth": "check the bot token on the connection",
    "missing_scope": "add the missing scope to the slack app and reinstall it",
}


def open_session(token) -> requests.Session:
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {resolve(token)}"
    return session


# helper function that calls a slack api method and returns its body. slack
# answers most errors with a 200 and ok false, a 429 means the rate limit was hit
# and is retried after the wait slack asks for
def call(session, method: str, params: dict | None = None, body: dict | None = None) -> dict:
    for _ in range(RETRIES):
        if body is None:
            response = session.request("GET", f"{API}/{method}", params=params, timeout=TIMEOUT)
        else:
            response = session.request("POST", f"{API}/{method}", json=body, timeout=TIMEOUT)
        if response.status_code == 429:
            time.sleep(int(response.headers.get("Retry-After", "1")))
            continue
        if response.status_code >= 400:
            raise SlackErrorCode.SLACK_6.exception(method=method, error=f"status {response.status_code}")
        data = response.json()
        if not data.get("ok"):
            error = data.get("error", "unknown error")
            if error == "missing_scope" and data.get("needed"):
                error += f" {data['needed']}"
            hint = HINTS.get(data.get("error"))
            raise SlackErrorCode.SLACK_6.exception(method=method, error=f"{error}, {hint}" if hint else error)
        return data
    raise SlackErrorCode.SLACK_6.exception(method=method, error="rate limited")


def is_channel_id(channel: str) -> bool:
    return channel[0] in "CG" and channel.isupper() and channel.isalnum()


# helper function that returns the id of a channel given by id or name, names are
# looked up in the public and private channels the app can see
def channel_id(session, channel: str, cache: dict) -> str:
    if is_channel_id(channel):
        return channel
    name = channel.lstrip("#")
    if not cache:
        params = {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": 1000}
        while True:
            data = call(session, "conversations.list", params)
            cache.update({entry["name"]: entry["id"] for entry in data["channels"]})
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
            params = {**params, "cursor": cursor}
    if name not in cache:
        raise SlackErrorCode.SLACK_7.exception(channel=channel)
    return cache[name]


def to_epoch(timestamp: str) -> str:
    return f"{datetime.fromisoformat(timestamp).timestamp():.6f}"


# helper function that reads the messages of a channel posted after oldest,
# slack returns them newest first so they're sorted oldest first by ts
def fetch_messages(session, channel: str, oldest: str) -> list[dict]:
    params = {"channel": channel, "oldest": oldest, "limit": PAGE_SIZE}
    messages = []
    while True:
        data = call(session, "conversations.history", params)
        messages.extend(data["messages"])
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not data.get("has_more") or not cursor:
            break
        params = {**params, "cursor": cursor}
    return sorted(messages, key=lambda message: float(message["ts"]))


def from_ts(ts: str | None) -> datetime | None:
    return None if ts is None else datetime.fromtimestamp(float(ts), UTC)


def to_frame(messages: list[dict], channel: str) -> pl.DataFrame:
    rows = [
        {
            "ts": message["ts"],
            "channel": channel,
            "user_id": message.get("user"),
            "bot_id": message.get("bot_id"),
            "subtype": message.get("subtype"),
            "text": message.get("text"),
            "thread_ts": message.get("thread_ts"),
            "reply_count": message.get("reply_count", 0),
            "posted_at": from_ts(message["ts"]),
            "edited_at": from_ts((message.get("edited") or {}).get("ts")),
            "raw": json.dumps(message),
        }
        for message in messages
    ]
    return pl.DataFrame(rows, schema=SCHEMA)


def cell(value) -> str:
    text = "" if value is None else str(value).replace("\n", " ")
    return text if len(text) <= MAX_CELL else text[: MAX_CELL - 1] + "…"


# helper function that renders rows as a fixed width text table, every column as
# wide as its longest value so it lines up inside a slack code block
def text_table(frame: pl.DataFrame) -> str:
    rows = [[cell(value) for value in row] for row in frame.iter_rows()]
    widths = [max(len(name), *(len(row[i]) for row in rows)) for i, name in enumerate(frame.columns)]
    lines = [
        "  ".join(name.ljust(width) for name, width in zip(frame.columns, widths)),
        "  ".join("-" * width for width in widths),
        *("  ".join(value.ljust(width) for value, width in zip(row, widths)) for row in rows),
    ]
    return "\n".join(line.rstrip() for line in lines)


# helper function that builds the message text, the title and row count and then
# the first rows as a code block, cut short when it would pass slack's limit
def message_text(frame: pl.DataFrame, title: str | None, max_rows: int) -> str:
    count = f"{frame.height} row" + ("" if frame.height == 1 else "s")
    text = f"*{title}*\n{count}" if title else count
    if max_rows and frame.height:
        table = text_table(frame.head(max_rows))
        if len(text) + len(table) > MAX_TEXT:
            table = table[: MAX_TEXT - len(text)].rsplit("\n", 1)[0]
        text += f"\n```\n{table}\n```"
        if frame.height > max_rows:
            text += f"\n_first {max_rows} rows shown_"
    return text


def post_message(session, channel: str, text: str) -> None:
    call(session, "chat.postMessage", body={"channel": channel, "text": text, "mrkdwn": True})
