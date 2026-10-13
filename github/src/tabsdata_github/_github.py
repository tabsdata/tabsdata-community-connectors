#
# Copyright 2026 Tabsdata Inc.
#

from collections.abc import Iterator
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests

from tabsdata_github import GitHubSrcConn, resolve
from tabsdata_github.error import GitHubErrorCode

PAGE_SIZE = 100


def open_session(conn: GitHubSrcConn) -> requests.Session:
    session = requests.Session()
    session.headers["Accept"] = "application/vnd.github+json"
    session.headers["X-GitHub-Api-Version"] = "2022-11-28"
    if conn.token is not None:
        session.headers["Authorization"] = f"Bearer {resolve(conn.token)}"
    return session


# helper function that raises on a failed request, a 403 or 429 with no requests
# left is reported as the rate limit since that's fixed by setting a token
def check_response(response: requests.Response, repository: str) -> None:
    limited = response.status_code in (403, 429)
    if limited and response.headers.get("x-ratelimit-remaining") == "0":
        reset = datetime.fromtimestamp(int(response.headers["x-ratelimit-reset"]))
        raise GitHubErrorCode.GITHUB_3.exception(reset=f"{reset:%H:%M}")
    if response.status_code >= 400:
        raise GitHubErrorCode.GITHUB_4.exception(repository=repository, status=response.status_code)


def get(session: requests.Session, conn: GitHubSrcConn, repository: str, url: str, params: dict | None):
    response = session.get(url, params=params, timeout=conn.timeout)
    check_response(response, repository)
    return response


# helper function that yields the responses of a paginated GET, following the
# next link of the Link header which already carries the query parameters
def get_pages(
    session: requests.Session,
    conn: GitHubSrcConn,
    repository: str,
    path: str,
    params: dict,
    max_pages: int,
) -> Iterator[requests.Response]:
    url, query = f"{resolve(conn.api_url)}/repos/{repository}/{path}", {**params, "per_page": PAGE_SIZE}
    for _ in range(max_pages):
        response = get(session, conn, repository, url, query)
        yield response
        next_page = response.links.get("next")
        if next_page is None:
            return
        url, query = next_page["url"], None


# helper function that returns the url of another page of a paginated response
def page_url(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
    return urlunsplit(parts._replace(query=urlencode({**query, "page": page})))


def is_pull_request(item: dict) -> bool:
    return "pull_request" in item


# helper function that reads the issues and pull requests updated since the
# watermark, oldest update first. the watermark covers both kinds since the
# endpoint returns both, the caller keeps the kind it reads
def fetch_issue_items(session, conn, table, since, max_pages) -> tuple[list[dict], str | None]:
    params = {"state": "all", "sort": "updated", "direction": "asc", "since": since}
    items = [item for page in get_pages(session, conn, table.repository, "issues", params, max_pages)
             for item in page.json()]
    watermark = items[-1]["updated_at"] if items else None
    wanted = table.resource == "pull_requests"
    return [item for item in items if is_pull_request(item) == wanted], watermark


def fetch_issue_comments(session, conn, table, since, max_pages) -> tuple[list[dict], str | None]:
    params = {"sort": "updated", "direction": "asc", "since": since}
    items = [item for page in get_pages(session, conn, table.repository, "issues/comments", params, max_pages)
             for item in page.json()]
    return items, items[-1]["updated_at"] if items else None


# helper function that reads the commits committed since the watermark. the
# endpoint returns newest first with no way to flip it, so when there are more
# pages than max_pages it jumps to the last page through the Link header and
# reads backwards, that way the oldest commits are read first and the rest
# continue on the next run
def fetch_commits(session, conn, table, since, max_pages) -> tuple[list[dict], str | None]:
    url = f"{resolve(conn.api_url)}/repos/{table.repository}/commits"
    params = {"since": since, "per_page": PAGE_SIZE}
    if table.branch:
        params["sha"] = table.branch
    first = get(session, conn, table.repository, url, params)
    last = first.links.get("last")
    if last is None:
        items = first.json()
    else:
        last_page = int(parse_qs(urlsplit(last["url"]).query)["page"][-1])
        pages = range(max(1, last_page - max_pages + 1), last_page + 1)
        items = [item for page in pages
                 for item in (first if page == 1 else get(session, conn, table.repository,
                                                          page_url(last["url"], page), None)).json()]
    items.sort(key=lambda item: item["commit"]["committer"]["date"])
    return items, items[-1]["commit"]["committer"]["date"] if items else None


def fetch_releases(session, conn, table, since, max_pages) -> tuple[list[dict], str | None]:
    items = [item for page in get_pages(session, conn, table.repository, "releases", {}, max_pages)
             for item in page.json()]
    return items, None


FETCHERS = {
    "issues": fetch_issue_items,
    "pull_requests": fetch_issue_items,
    "issue_comments": fetch_issue_comments,
    "commits": fetch_commits,
    "releases": fetch_releases,
}
