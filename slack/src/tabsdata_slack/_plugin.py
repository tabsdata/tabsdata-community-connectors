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

from tabsdata_slack import SlackDest, SlackDestConn, SlackSrc, SlackSrcConn, _slack
from tabsdata_slack.error import SlackErrorCode

logger = logging.getLogger(__name__)


class SlackSrcPlugin(SrcPlugin[SlackSrcConn, SlackSrc]):
    def read_in(
        self,
        ctx: SrcPluginCtx,
        conn: SlackSrcConn,
        src: SlackSrc,
    ) -> list[list[TableFileInput]]:
        session = _slack.open_session(conn.token)
        cache = {}
        tables = []
        for index, channel in enumerate(src.channels):
            channel_id = _slack.channel_id(session, channel, cache)
            # the watermark is the ts of the newest message read, stored per
            # channel. slack reads after oldest, so no message is read twice
            key = f"slack.{channel_id}"
            oldest = str(ctx.get_attr(key) or _slack.to_epoch(src.since))
            messages = _slack.fetch_messages(session, channel_id, oldest)
            logger.info("slack: %d messages read from %s", len(messages), channel)
            if not messages:
                tables.append([])
                continue
            ctx.set_attr(key, messages[-1]["ts"])
            file = ctx.work_dir / f"slack_{index}.parquet"
            _slack.to_frame(messages, channel_id).write_parquet(file)
            tables.append([TableFileInput(file=file)])
        return tables


class SlackDestPlugin(DestPlugin[SlackDestConn, SlackDest]):
    def write_out(
        self,
        ctx: DestContext,
        conn: SlackDestConn,
        dest: SlackDest,
        tables: list[TableFileSpec],
    ) -> None:
        if len(tables) != len(dest.messages):
            raise SlackErrorCode.SLACK_8.exception(slots=len(tables), messages=len(dest.messages))
        session = _slack.open_session(conn.token)
        cache = {}
        for message, parquet in zip(dest.messages, tables):
            if parquet is None:
                # skip tables the subscriber returned None for
                continue
            frame = pl.read_parquet(parquet)
            if message.skip_empty and frame.height == 0:
                continue
            if message.columns:
                missing = [column for column in message.columns if column not in frame.columns]
                if missing:
                    raise SlackErrorCode.SLACK_9.exception(columns=missing, channel=message.channel)
                frame = frame.select(message.columns)
            channel_id = _slack.channel_id(session, message.channel, cache)
            _slack.post_message(session, channel_id, _slack.message_text(frame, message.title, message.max_rows))
            logger.info("slack: posted %d rows to %s", frame.height, message.channel)


SLACK_SRC = SrcDef(
    conn=SlackSrcConn,
    type_="slack-in",
    system="Slack",
    src_version="v1",
    src=SlackSrc,
    table_mode=TableMode.SINGLE,
    cardinality=lambda src: len(src.channels),
    plugin=SlackSrcPlugin,
    explorer=None,
    icon=None,
)

SLACK_DEST = DestDef(
    conn=SlackDestConn,
    type_="slack-out",
    system="Slack",
    dest_version="v1",
    dest=SlackDest,
    cardinality=lambda dest: len(dest.messages),
    plugin=SlackDestPlugin,
    explorer=None,
    icon=None,
)
