#
# Copyright 2026 Tabsdata Inc.
#

"""slack source and destination connector for tabsdata

the source reads the messages of slack channels for a publisher, one output
table per channel. the bot token is stored in the connection on the publisher's
collection, each run only reads the messages posted since the previous run:

    @publisher(
        source=SlackSrc(channels=["support"], since="2026-10-01T00:00:00Z"),
        output_tables=["support_messages"],
    )
    def pub_slack(support_messages: TableFrameSpec) -> TableFrameSpec:
        return support_messages

the destination posts a message per subscriber table with its row count and
its first rows:

    @subscriber(
        input_tables=["gold/failed_checks"],
        destination=SlackDest(messages=[Message("alerts", title="Failed checks")]),
    )
    def sub_slack(failed_checks: TableFrameSpec) -> TableFrameSpec:
        return failed_checks
"""

import re
from dataclasses import KW_ONLY
from datetime import datetime
from typing import Annotated

import tabsdatak._api as _api
from pydantic import Field
from tabsdatak.api import Dest, Secret, Src, StrOrSecretSpec
from tabsdatak.spi import Conn

from tabsdata_slack.error import SlackErrorCode

# a channel is its id or its name, with or without the leading #
CHANNEL = re.compile(r"^#?[a-z0-9][a-z0-9_-]{0,79}$|^[CG][A-Z0-9]{6,}$")


# helper function that returns the value of a plain string or a secret
def resolve(spec: StrOrSecretSpec) -> str:
    return spec.value() if isinstance(spec, Secret) else spec


# helper function that checks a channel is an id or a name slack allows
def check_channel(channel: str) -> None:
    if CHANNEL.match(channel) is None:
        raise ValueError(f"not a slack channel id or name: {channel!r}")


ChannelList = Annotated[list[str], _api.NON_EMPTY_LIST_FIELD]


@_api.dataclass(SlackErrorCode.SLACK_1, kw_only=True)
class SlackSrcConn(Conn):
    """connection for SlackSrc that stores the bot token, the slack app needs the
    channels:history scope, and channels:read to look up channels by name
    """

    token: StrOrSecretSpec


@_api.dataclass(SlackErrorCode.SLACK_2, kw_only=True)
class SlackSrc(Src):
    """source that reads the messages of each channel posted since the previous
    run and publishes them as one table per channel, oldest first. tables map to
    the publisher's output tables in order and the first run starts at since.
    thread replies aren't read, only the messages posted in the channel itself
    """

    channels: ChannelList
    since: str

    def __post_init__(self) -> None:
        datetime.fromisoformat(self.since)
        for channel in self.channels:
            check_channel(channel)
        names = [channel.lstrip("#") for channel in self.channels]
        if len(set(names)) != len(names):
            raise ValueError("the same channel is listed more than once")


@_api.dataclass(SlackErrorCode.SLACK_3, kw_only=True)
class SlackDestConn(Conn):
    """connection for SlackDest that stores the bot token, the slack app needs the
    chat:write scope, and channels:read to look up channels by name
    """

    token: StrOrSecretSpec


@_api.dataclass(SlackErrorCode.SLACK_5)
class Message:
    """message posted to a channel for one subscriber table, with the title, the
    row count and the first max_rows rows as a text table. columns limits the
    columns shown, skip_empty doesn't post anything for a table with no rows
    """

    channel: str
    _: KW_ONLY
    title: str | None = None
    max_rows: Annotated[int, Field(ge=0, le=100)] = 10
    columns: list[str] | None = None
    skip_empty: bool = True

    def __post_init__(self) -> None:
        check_channel(self.channel)


Messages = Annotated[list[Message], _api.NON_EMPTY_LIST_FIELD]


@_api.dataclass(SlackErrorCode.SLACK_4, kw_only=True)
class SlackDest(Dest):
    """destination that posts one message per subscriber table, messages map to
    the subscriber's input tables in order
    """

    messages: Messages


__all__ = [
    "Message",
    "SlackDest",
    "SlackDestConn",
    "SlackSrc",
    "SlackSrcConn",
]
