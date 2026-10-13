#
# Copyright 2026 Tabsdata Inc.
#

from __future__ import annotations

import json

import polars as pl
import pytest
from conftest import FakeResponse, load_page
from tabsdatak.api import Secret
from tabsdatak.error import ApiInitException

from tabsdata_github import (
    Commits,
    GitHubSrc,
    GitHubSrcConn,
    IssueComments,
    Issues,
    PullRequests,
    Releases,
)
from tabsdata_github._model import SCHEMAS
from tabsdata_github._plugin import GitHubSrcPlugin
from tabsdata_github.error import GitHubRuntimeException

SINCE = "2026-10-01T00:00:00Z"
ISSUES_KEY = "github.issues.pola-rs/polars"
API = "https://api.github.com/repos/pola-rs/polars"


def read(ctx, *tables, conn=None, max_pages=20):
    src = GitHubSrc(tables=list(tables) or [Issues("pola-rs/polars")], since=SINCE, max_pages=max_pages)
    return GitHubSrcPlugin().read_in(ctx, conn or GitHubSrcConn(), src)


def frame(tables, index=0):
    return pl.read_parquet(tables[index][0].file)


def comment(id_, issue, updated_at):
    return {
        "id": id_,
        "issue_url": f"{API}/issues/{issue}",
        "html_url": f"https://github.com/pola-rs/polars/issues/{issue}#issuecomment-{id_}",
        "body": "a comment",
        "user": {"login": "someone"},
        "author_association": "NONE",
        "created_at": updated_at,
        "updated_at": updated_at,
    }


def commit(sha, date):
    person = {"name": "Someone", "email": "someone@example.com", "date": date}
    return {
        "sha": sha,
        "commit": {"message": f"commit {sha}", "author": person, "committer": person},
        "author": None,
        "parents": [{"sha": "parent"}],
        "html_url": f"https://github.com/pola-rs/polars/commit/{sha}",
    }


def release(id_, tag):
    return {
        "id": id_,
        "tag_name": tag,
        "name": tag,
        "body": "notes",
        "draft": False,
        "prerelease": False,
        "target_commitish": "main",
        "author": {"login": "someone"},
        "created_at": "2026-10-01T00:00:00Z",
        "published_at": "2026-10-02T00:00:00Z",
        "html_url": f"https://github.com/pola-rs/polars/releases/tag/{tag}",
    }


# issues


def test_first_run_reads_every_page_from_since(ctx, github, two_pages):
    received = github(*two_pages)

    issues = frame(read(ctx))

    assert received[0]["url"] == f"{API}/issues"
    assert received[0]["params"]["since"] == SINCE
    assert received[0]["timeout"] == 30.0
    assert received[1]["url"] == "https://api.github.com/page-2"
    assert issues.schema == SCHEMAS["issues"]
    assert issues.height == 7


def test_pull_requests_are_left_out_of_issues(ctx, github, two_pages):
    github(*two_pages)
    pull_requests = [i["number"] for i in load_page(1) if "pull_request" in i]

    issues = frame(read(ctx))

    assert pull_requests
    assert not set(pull_requests) & set(issues["number"])


def test_issue_columns_and_raw_json(ctx, github, two_pages):
    github(*two_pages)
    issue = load_page(2)[0]

    issues = frame(read(ctx))
    row = issues.filter(pl.col("number") == issue["number"]).row(0, named=True)

    assert row["title"] == issue["title"]
    assert row["author"] == issue["user"]["login"]
    assert row["labels"] == [label["name"] for label in issue["labels"]]
    assert json.loads(row["raw"]) == issue


def test_next_run_starts_where_the_last_one_ended(ctx, github, two_pages):
    github(*two_pages)
    read(ctx)
    assert ctx.state[ISSUES_KEY] == load_page(2)[-1]["updated_at"]

    received = github(FakeResponse([]))
    read(ctx)

    assert received[0]["params"]["since"] == load_page(2)[-1]["updated_at"]


def test_no_changes_gives_no_data(ctx, github):
    github(FakeResponse([]))

    assert read(ctx) == [[]]
    assert ISSUES_KEY not in ctx.state


def test_max_pages_leaves_the_rest_for_the_next_run(ctx, github, two_pages):
    received = github(*two_pages)

    read(ctx, max_pages=1)

    assert len(received) == 1
    assert ctx.state[ISSUES_KEY] == load_page(1)[-1]["updated_at"]


def test_each_table_gets_its_own_output(ctx, github):
    github(FakeResponse(load_page(1)), FakeResponse(load_page(2)))

    tables = read(ctx, Issues("pola-rs/polars"), Issues("apache/arrow"))

    assert len(tables) == 2
    assert "github.issues.apache/arrow" in ctx.state


def test_label_columns_hold_the_first_matching_label(ctx, github, two_pages):
    github(*two_pages)
    label_columns = {
        "kind": ["bug", "enhancement", "documentation", "performance"],
        "severity": ["P-low", "P-medium", "P-high", "P-critical"],
    }

    issues = frame(read(ctx, Issues("pola-rs/polars", label_columns=label_columns)))

    kinds = dict(issues.select("number", "kind").iter_rows())
    severities = dict(issues.select("number", "severity").iter_rows())
    assert kinds[13107] == "bug"
    assert kinds[28356] == "enhancement"
    assert kinds[27342] == "performance"
    assert severities[27342] is None
    assert issues.schema["kind"] == pl.String


# pull requests


def test_pull_requests_table_keeps_only_pull_requests(ctx, github, two_pages):
    github(*two_pages)
    expected = next(i for i in load_page(1) if "pull_request" in i)

    pulls = frame(read(ctx, PullRequests("pola-rs/polars")))

    assert pulls.schema == SCHEMAS["pull_requests"]
    row = pulls.row(0, named=True)
    assert pulls.height == 1
    assert row["number"] == expected["number"]
    assert row["merged_at"] is not None
    assert row["draft"] is False


def test_issues_and_pull_requests_keep_separate_watermarks(ctx, github, two_pages):
    github(*two_pages, FakeResponse(load_page(1)))

    read(ctx, Issues("pola-rs/polars"), PullRequests("pola-rs/polars"))

    assert ctx.state[ISSUES_KEY] == load_page(2)[-1]["updated_at"]
    assert ctx.state["github.pull_requests.pola-rs/polars"] == load_page(1)[-1]["updated_at"]


# issue comments


def test_issue_comments(ctx, github):
    received = github(FakeResponse([comment(1, 100, "2026-10-02T00:00:00Z"), comment(2, 200, "2026-10-03T00:00:00Z")]))

    comments = frame(read(ctx, IssueComments("pola-rs/polars")))

    assert received[0]["url"] == f"{API}/issues/comments"
    assert received[0]["params"]["since"] == SINCE
    assert comments.schema == SCHEMAS["issue_comments"]
    assert comments["issue_number"].to_list() == [100, 200]
    assert ctx.state["github.issue_comments.pola-rs/polars"] == "2026-10-03T00:00:00Z"


# commits


def test_commits_on_a_branch_are_stored_oldest_first(ctx, github):
    received = github(FakeResponse([commit("b", "2026-10-03T00:00:00Z"), commit("a", "2026-10-02T00:00:00Z")]))

    commits = frame(read(ctx, Commits("pola-rs/polars", branch="main")))

    assert received[0]["url"] == f"{API}/commits"
    assert received[0]["params"]["sha"] == "main"
    assert received[0]["params"]["since"] == SINCE
    assert commits.schema == SCHEMAS["commits"]
    assert commits["sha"].to_list() == ["a", "b"]
    assert ctx.state["github.commits.pola-rs/polars@main"] == "2026-10-03T00:00:00Z"


def test_commits_backfill_reads_the_oldest_pages_first(ctx, github):
    last = f"{API}/commits?since={SINCE}&per_page=100&page=5"
    received = github(
        FakeResponse([commit("newest", "2026-10-09T00:00:00Z")], last_url=last),
        FakeResponse([commit("d", "2026-10-04T00:00:00Z")]),
        FakeResponse([commit("e", "2026-10-03T00:00:00Z")]),
    )

    commits = frame(read(ctx, Commits("pola-rs/polars"), max_pages=2))

    assert [r["url"].rsplit("page=", 1)[-1] for r in received[1:]] == ["4", "5"]
    assert commits["sha"].to_list() == ["e", "d"]
    assert ctx.state["github.commits.pola-rs/polars"] == "2026-10-04T00:00:00Z"


# releases


def test_releases_reload_in_full_without_a_watermark(ctx, github):
    received = github(FakeResponse([release(2, "v2")], next_url=f"{API}/releases?page=2"), FakeResponse([release(1, "v1")]))

    releases = frame(read(ctx, Releases("pola-rs/polars")))

    assert "since" not in received[0]["params"]
    assert releases.schema == SCHEMAS["releases"]
    assert releases["tag_name"].to_list() == ["v2", "v1"]
    assert ctx.state == {}


# connection, errors and validation


def test_token_is_sent_only_when_set(ctx, github):
    received = github(FakeResponse([]), FakeResponse([]))

    read(ctx)
    read(ctx, conn=GitHubSrcConn(token=Secret("ghp_example")))

    assert "Authorization" not in received[0]["headers"]
    assert received[1]["headers"]["Authorization"] == "Bearer ghp_example"


def test_token_is_not_serialized():
    conn = GitHubSrcConn(token=Secret("ghp_example"))

    assert "ghp_example" not in conn.to_json()


def test_tagged_connection_document_is_decoded(ctx, github):
    received = github(FakeResponse([]))
    document = {
        "kind": "connectionDef",
        "apiVersion": "1.0",
        "type": "tabsdata_github:GitHubSrcConn",
        "spec": {"api_url": "str:https://github.example.com/api/v3", "timeout": 30.0},
    }

    read(ctx, conn=GitHubSrcConn.from_json(json.dumps(document)))

    assert received[0]["url"] == "https://github.example.com/api/v3/repos/pola-rs/polars/issues"


def test_rate_limit_is_explained(ctx, github):
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1791000000"}
    github(FakeResponse({}, status_code=403, headers=headers))

    with pytest.raises(GitHubRuntimeException, match="rate limit"):
        read(ctx)


def test_failed_request_names_the_repository(ctx, github):
    github(FakeResponse({}, status_code=404))

    with pytest.raises(GitHubRuntimeException, match="pola-rs/polars failed with status 404"):
        read(ctx)


def test_mixed_tables_survive_serialization():
    src = GitHubSrc(
        since=SINCE,
        tables=[
            Issues("pola-rs/polars", label_columns={"kind": ["bug"]}),
            PullRequests("pola-rs/polars"),
            IssueComments("pola-rs/polars"),
            Commits("pola-rs/polars", branch="main"),
            Releases("pola-rs/polars"),
        ],
    )

    assert GitHubSrc.from_json(src.to_json()) == src


@pytest.mark.parametrize(
    "tables, since",
    [
        ([], SINCE),
        ([Issues("pola-rs/polars"), Issues("pola-rs/polars")], SINCE),
        ([Issues("pola-rs/polars")], "last week"),
    ],
)
def test_invalid_sources_are_rejected(tables, since):
    with pytest.raises(ApiInitException):
        GitHubSrc(tables=tables, since=since)


@pytest.mark.parametrize(
    "make",
    [
        lambda: Issues("polars"),
        lambda: Issues("pola-rs/polars", label_columns={"title": ["bug"]}),
        lambda: PullRequests("pola-rs/polars", label_columns={"merged_at": ["bug"]}),
        lambda: Commits("pola-rs/polars", "main"),
    ],
)
def test_invalid_tables_are_rejected(make):
    with pytest.raises(ApiInitException):
        make()
