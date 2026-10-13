#
# Copyright 2026 Tabsdata Inc.
#

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
from tabsdatak.spi import SrcPluginCtx

RESOURCES = Path(__file__).parent / "resources"


def load_page(number: int) -> list[dict]:
    path = RESOURCES / f"polars-issues-page{number}.json"
    return json.loads(path.read_text())["body"]


class FakeCtx(SrcPluginCtx):
    """in memory plugin context that keeps the function state in a dict"""

    exec_plan_id = "plan"
    trx_id = "trx"
    exec_plan_ts = "2026-10-09T00:00:00Z"
    collection_id = "collection"
    collection_name = "github"
    function_id = "function"
    function_name = "ingest_issues"

    def __init__(self, work_dir: Path):
        self.state = {}
        self.dir = work_dir

    @property
    def work_dir(self) -> Path:
        return self.dir

    def get_attr(self, attr):
        return self.state.get(attr)

    def set_attr(self, attr, val):
        self.state[attr] = val

    def add_metadata(self, attr, val):
        pass


class FakeResponse:
    def __init__(self, body, next_url=None, status_code=200, headers=None, last_url=None):
        self.body = body
        self.links = {}
        if next_url:
            self.links["next"] = {"url": next_url}
        if last_url:
            self.links["last"] = {"url": last_url}
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.body


@pytest.fixture
def ctx(tmp_path):
    return FakeCtx(tmp_path)


@pytest.fixture
def github(monkeypatch):
    """fixture that makes requests.Session.get answer with the given responses in
    order and returns the list of requests it received"""

    def serve(*responses):
        queue = list(responses)
        received = []

        def get(session, url, params=None, timeout=None):
            received.append(
                {
                    "url": url,
                    "params": params,
                    "timeout": timeout,
                    "headers": dict(session.headers),
                }
            )
            return queue.pop(0)

        monkeypatch.setattr(requests.Session, "get", get)
        return received

    return serve


@pytest.fixture
def two_pages():
    return [
        FakeResponse(load_page(1), next_url="https://api.github.com/page-2"),
        FakeResponse(load_page(2)),
    ]
