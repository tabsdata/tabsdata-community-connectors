#
# Copyright 2026 Tabsdata Inc.
#

"""github source connector for tabsdata

the source reads github data for a publisher, one output table per entry in
tables. each entry is a resource of one repository, issues, pull requests,
issue comments, commits or releases. the optional token is stored in the
connection on the publisher's collection, every resource except releases only
reads what changed since the previous run:

    @publisher(
        source=GitHubSrc(
            since="2026-10-01T00:00:00Z",
            tables=[
                Issues("pola-rs/polars", label_columns={"maintainer_kind": ["bug", "enhancement"]}),
                PullRequests("pola-rs/polars"),
                Commits("pola-rs/polars", branch="main"),
            ],
        ),
        output_tables=["issues", "pull_requests", "commits"],
    )
    def pub_github(
        issues: TableFrameSpec, pull_requests: TableFrameSpec, commits: TableFrameSpec
    ) -> tuple[TableFrameSpec, TableFrameSpec, TableFrameSpec]:
        return issues, pull_requests, commits
"""

from dataclasses import KW_ONLY
from datetime import datetime
from typing import Annotated, Literal, Union

import tabsdatak._api as _api
from pydantic import Field, StringConstraints
from tabsdatak.api import Secret, Src, StrOrSecretSpec
from tabsdatak.spi import Conn

from tabsdata_github._model import SCHEMAS
from tabsdata_github.error import GitHubErrorCode

# restricts repositories to the owner/name form used in the api urls
RepositoryName = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
]

# extra text columns filled from the labels, column name to the labels it looks for
LabelColumns = dict[str, list[str]]


# helper function that returns the value of a plain string or a secret
def resolve(spec: StrOrSecretSpec) -> str:
    return spec.value() if isinstance(spec, Secret) else spec


# helper function that rejects label columns reusing the name of a resource column
def check_label_columns(resource: str, label_columns: LabelColumns) -> None:
    taken = set(SCHEMAS[resource]).intersection(label_columns)
    if taken:
        raise ValueError(
            f"label columns clash with {resource} columns: {sorted(taken)}"
        )


@_api.dataclass(GitHubErrorCode.GITHUB_1, kw_only=True)
class GitHubSrcConn(Conn):
    """connection for GitHubSrc that stores the github api settings, the token is
    optional and raises the rate limit from 60 to 5000 requests an hour, a fine
    grained token with no permissions is enough for public repositories
    """

    token: StrOrSecretSpec | None = None
    api_url: StrOrSecretSpec = "https://api.github.com"
    timeout: float = 30.0


@_api.dataclass(GitHubErrorCode.GITHUB_5)
class Issues:
    """table of the issues of a repository updated since the previous run, pull
    requests are left out. label_columns adds a text column per entry holding the
    first of its labels the issue has
    """

    repository: RepositoryName
    _: KW_ONLY
    label_columns: LabelColumns = Field(default_factory=dict)
    resource: Literal["issues"] = "issues"

    def __post_init__(self) -> None:
        check_label_columns(self.resource, self.label_columns)


@_api.dataclass(GitHubErrorCode.GITHUB_5)
class PullRequests:
    """table of the pull requests of a repository updated since the previous run,
    read from the issues endpoint so it supports since, label_columns works the
    same as on Issues
    """

    repository: RepositoryName
    _: KW_ONLY
    label_columns: LabelColumns = Field(default_factory=dict)
    resource: Literal["pull_requests"] = "pull_requests"

    def __post_init__(self) -> None:
        check_label_columns(self.resource, self.label_columns)


@_api.dataclass(GitHubErrorCode.GITHUB_5)
class IssueComments:
    """table of the conversation comments on the issues and pull requests of a
    repository updated since the previous run, code review comments aren't
    included
    """

    repository: RepositoryName
    _: KW_ONLY
    resource: Literal["issue_comments"] = "issue_comments"


@_api.dataclass(GitHubErrorCode.GITHUB_5)
class Commits:
    """table of the commits of a repository committed since the previous run, on
    branch or the default branch when it's not set
    """

    repository: RepositoryName
    _: KW_ONLY
    branch: str | None = None
    resource: Literal["commits"] = "commits"


@_api.dataclass(GitHubErrorCode.GITHUB_5)
class Releases:
    """table of the releases of a repository, newest first, reloaded in full each
    run since a repository has few of them and they change after publishing
    """

    repository: RepositoryName
    _: KW_ONLY
    resource: Literal["releases"] = "releases"


# a table entry, resource tells pydantic which class to load it back into
Table = Annotated[
    Union[Issues, PullRequests, IssueComments, Commits, Releases],
    Field(discriminator="resource"),
]

# a source needs at least one table since each table is one output table
Tables = Annotated[list[Table], _api.NON_EMPTY_LIST_FIELD]


# helper function that returns the key a table's watermark is stored under, so
# reordering tables keeps each watermark
def table_key(table: Table) -> str:
    branch = getattr(table, "branch", None)
    return f"github.{table.resource}.{table.repository}" + (
        f"@{branch}" if branch else ""
    )


@_api.dataclass(GitHubErrorCode.GITHUB_2, kw_only=True)
class GitHubSrc(Src):
    """source that reads each table from github and publishes it as one table,
    tables map to the publisher's output tables in order. the first run starts
    at since, max_pages caps the pages of 100 items read per table and run so a
    long backfill continues on the next run
    """

    tables: Tables
    since: str
    max_pages: Annotated[int, Field(ge=1)] = 20

    def __post_init__(self) -> None:
        datetime.fromisoformat(self.since)
        keys = [table_key(table) for table in self.tables]
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        if repeated:
            raise ValueError(f"tables read the same resource twice: {repeated}")


__all__ = [
    "Commits",
    "GitHubSrc",
    "GitHubSrcConn",
    "IssueComments",
    "Issues",
    "PullRequests",
    "Releases",
]
