#
# Copyright 2026 Tabsdata Inc.
#

import logging

import requests

from tabsdatak.spi import (
    SrcDef,
    SrcPlugin,
    SrcPluginCtx,
    TableFileInput,
    TableMode,
)
from tabsdata_github import GitHubSrc, GitHubSrcConn, Table, table_key
from tabsdata_github._github import FETCHERS, open_session
from tabsdata_github._model import to_frame

logger = logging.getLogger(__name__)


# helper function that reads one table into a parquet file under the work dir,
# the watermark is stored per table with ctx.set_attr so the next run starts at
# the newest change of this one. github returns items changed at or after the
# watermark so the last item of a run comes again in the next one
def read_table(
    ctx: SrcPluginCtx,
    session: requests.Session,
    conn: GitHubSrcConn,
    src: GitHubSrc,
    table: Table,
    index: int,
) -> list[TableFileInput]:
    key = table_key(table)
    since = str(ctx.get_attr(key) or src.since)
    items, watermark = FETCHERS[table.resource](session, conn, table, since, src.max_pages)
    if watermark is not None:
        ctx.set_attr(key, watermark)
    logger.info("github: %d %s read from %s since %s", len(items), table.resource, table.repository, since)
    if not items:
        # no new data for this table when nothing changed
        return []
    file = ctx.work_dir / f"github_{index}.parquet"
    to_frame(table.resource, items, getattr(table, "label_columns", {})).write_parquet(file)
    return [TableFileInput(file=file)]


class GitHubSrcPlugin(SrcPlugin[GitHubSrcConn, GitHubSrc]):
    def read_in(
        self,
        ctx: SrcPluginCtx,
        conn: GitHubSrcConn,
        src: GitHubSrc,
    ) -> list[list[TableFileInput]]:
        with open_session(conn) as session:
            return [read_table(ctx, session, conn, src, table, index) for index, table in enumerate(src.tables)]


GITHUB_SRC = SrcDef(
    conn=GitHubSrcConn,
    type_="github-in",
    system="GitHub",
    src_version="v1",
    src=GitHubSrc,
    table_mode=TableMode.SINGLE,
    cardinality=lambda src: len(src.tables),
    plugin=GitHubSrcPlugin,
    explorer=None,
    icon=None,
)
