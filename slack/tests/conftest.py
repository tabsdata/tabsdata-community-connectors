#
# Copyright 2026 Tabsdata Inc.
#

from pathlib import Path

import pytest
from tabsdatak.spi import SrcPluginCtx

from tabsdata_slack import _slack


class FakeCtx(SrcPluginCtx):
    """in memory plugin context that keeps the function state in a dict"""

    exec_plan_id = "plan"
    trx_id = "trx"
    exec_plan_ts = "2026-10-13T00:00:00Z"
    collection_id = "collection"
    collection_name = "slack"
    function_id = "function"
    function_name = "slack"

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
    def __init__(self, body=None, status_code=200, headers=None):
        self.body = body if body is not None else {"ok": True}
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.body


class FakeSession:
    """session that answers each request with the next queued response and
    records what it received"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.received = []
        self.headers = {}

    def request(self, method, url, timeout=None, params=None, json=None):
        self.received.append({"method": method, "url": url, "params": params, "json": json})
        return self.responses.pop(0)


@pytest.fixture
def ctx(tmp_path):
    return FakeCtx(tmp_path)


@pytest.fixture
def slack_api(monkeypatch):
    """fixture that makes open_session return a fake session answering with the
    given responses, and returns the session to read the requests from"""

    def serve(*responses):
        session = FakeSession(responses)
        monkeypatch.setattr(_slack, "open_session", lambda token: session)
        monkeypatch.setattr(_slack.time, "sleep", lambda seconds: None)
        return session

    return serve
