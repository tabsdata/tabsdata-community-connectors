#
# Copyright 2026 Tabsdata Inc.
#

import json
from datetime import datetime

import polars as pl

TIMESTAMP = pl.Datetime("us", "UTC")

# columns of each resource table, label columns are added after these. every
# table ends with raw, the full item json, so fields that aren't columns can
# still be read downstream
SCHEMAS = {
    "issues": pl.Schema(
        {
            "number": pl.Int64,
            "title": pl.String,
            "body": pl.String,
            "state": pl.String,
            "state_reason": pl.String,
            "author": pl.String,
            "author_association": pl.String,
            "labels": pl.List(pl.String),
            "comments": pl.Int64,
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
            "closed_at": TIMESTAMP,
            "url": pl.String,
            "raw": pl.String,
        }
    ),
    "pull_requests": pl.Schema(
        {
            "number": pl.Int64,
            "title": pl.String,
            "body": pl.String,
            "state": pl.String,
            "draft": pl.Boolean,
            "author": pl.String,
            "author_association": pl.String,
            "labels": pl.List(pl.String),
            "comments": pl.Int64,
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
            "closed_at": TIMESTAMP,
            "merged_at": TIMESTAMP,
            "url": pl.String,
            "raw": pl.String,
        }
    ),
    "issue_comments": pl.Schema(
        {
            "id": pl.Int64,
            "issue_number": pl.Int64,
            "body": pl.String,
            "author": pl.String,
            "author_association": pl.String,
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
            "url": pl.String,
            "raw": pl.String,
        }
    ),
    "commits": pl.Schema(
        {
            "sha": pl.String,
            "message": pl.String,
            "author": pl.String,
            "author_name": pl.String,
            "authored_at": TIMESTAMP,
            "committer_name": pl.String,
            "committed_at": TIMESTAMP,
            "parents": pl.List(pl.String),
            "url": pl.String,
            "raw": pl.String,
        }
    ),
    "releases": pl.Schema(
        {
            "id": pl.Int64,
            "tag_name": pl.String,
            "name": pl.String,
            "body": pl.String,
            "draft": pl.Boolean,
            "prerelease": pl.Boolean,
            "target": pl.String,
            "author": pl.String,
            "created_at": TIMESTAMP,
            "published_at": TIMESTAMP,
            "url": pl.String,
            "raw": pl.String,
        }
    ),
}


def to_timestamp(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


# helper function that returns the login of a user object, github sends null for
# some deleted or anonymous users
def login(user: dict | None) -> str | None:
    return None if user is None else user["login"]


def issue_row(item: dict) -> dict:
    return {
        "number": item["number"],
        "title": item["title"],
        "body": item["body"],
        "state": item["state"],
        "state_reason": item["state_reason"],
        "author": login(item["user"]),
        "author_association": item["author_association"],
        "labels": [label["name"] for label in item["labels"]],
        "comments": item["comments"],
        "created_at": to_timestamp(item["created_at"]),
        "updated_at": to_timestamp(item["updated_at"]),
        "closed_at": to_timestamp(item["closed_at"]),
        "url": item["html_url"],
    }


def pull_request_row(item: dict) -> dict:
    row = issue_row(item)
    del row["state_reason"]
    row["draft"] = item.get("draft", False)
    row["merged_at"] = to_timestamp(item["pull_request"].get("merged_at"))
    return row


def comment_row(item: dict) -> dict:
    return {
        "id": item["id"],
        "issue_number": int(item["issue_url"].rsplit("/", 1)[-1]),
        "body": item["body"],
        "author": login(item["user"]),
        "author_association": item["author_association"],
        "created_at": to_timestamp(item["created_at"]),
        "updated_at": to_timestamp(item["updated_at"]),
        "url": item["html_url"],
    }


# author is the github account the commit is linked to, author_name and
# committer_name come from git itself and are set even without an account
def commit_row(item: dict) -> dict:
    commit = item["commit"]
    return {
        "sha": item["sha"],
        "message": commit["message"],
        "author": login(item["author"]),
        "author_name": commit["author"]["name"],
        "authored_at": to_timestamp(commit["author"]["date"]),
        "committer_name": commit["committer"]["name"],
        "committed_at": to_timestamp(commit["committer"]["date"]),
        "parents": [parent["sha"] for parent in item["parents"]],
        "url": item["html_url"],
    }


def release_row(item: dict) -> dict:
    return {
        "id": item["id"],
        "tag_name": item["tag_name"],
        "name": item["name"],
        "body": item["body"],
        "draft": item["draft"],
        "prerelease": item["prerelease"],
        "target": item["target_commitish"],
        "author": login(item["author"]),
        "created_at": to_timestamp(item["created_at"]),
        "published_at": to_timestamp(item["published_at"]),
        "url": item["html_url"],
    }


ROWS = {
    "issues": issue_row,
    "pull_requests": pull_request_row,
    "issue_comments": comment_row,
    "commits": commit_row,
    "releases": release_row,
}


# helper function that builds a resource table with a fixed schema so every run
# publishes the same column types, even when a page has only null values. each
# label column holds the first of its labels the item has, or null
def to_frame(resource: str, items: list[dict], label_columns: dict[str, list[str]]) -> pl.DataFrame:
    rows = []
    for item in items:
        row = ROWS[resource](item)
        row["raw"] = json.dumps(item)
        for name, wanted in label_columns.items():
            row[name] = next((label for label in wanted if label in row["labels"]), None)
        rows.append(row)
    schema = pl.Schema([*SCHEMAS[resource].items(), *((name, pl.String) for name in label_columns)])
    return pl.DataFrame(rows, schema=schema)
