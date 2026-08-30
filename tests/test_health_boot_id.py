"""
Test for SERVER_BOOT_ID / GET /health's boot_id+pid fields (bin/agent-
server.py, 2026-08-30).

Part of the /sys restart-server "done in 1s" fix — see
tests/test_restart_server_boot_id.py for the relay-side behavior this
enables. This test covers only the agent-server half: /health must expose
a value that's stable within one process and distinguishable from another
process's, which is what the restart-server poll relies on to tell a real
restart apart from the old process still answering mid-shutdown.
"""

import json

import pytest

from conftest import import_script


@pytest.fixture
def agent_server():
    return import_script("agent-server")


class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeDB:
    def execute(self, query, params=None):
        return _FakeCursor({"count": 0})


@pytest.mark.asyncio
async def test_health_includes_boot_id_and_pid(agent_server, monkeypatch):
    agent_server.AGENT_SERVER_TOKEN = "secret-token"
    agent_server.agent_config = {"TestAgent": {}}
    agent_server.agent_processes = {}
    agent_server.agent_states = {"TestAgent": "IDLE"}
    agent_server.agent_sessions = {"TestAgent": "sessionid123"}
    monkeypatch.setattr(agent_server, "db", _FakeDB())

    req = _FakeRequest(headers={"Authorization": "Bearer secret-token"})
    resp = await agent_server.handle_health(req)

    data = json.loads(resp.text)
    assert data["boot_id"] == agent_server.SERVER_BOOT_ID
    assert isinstance(data["boot_id"], str) and data["boot_id"]
    assert data["pid"] == agent_server.os.getpid()


@pytest.mark.asyncio
async def test_boot_id_is_stable_across_calls_within_one_process(agent_server, monkeypatch):
    """The whole fix depends on this being a per-process constant, not
    regenerated per-request — otherwise every poll would look like a new
    process and the restart-server logic would falsely fire immediately,
    or never settle."""
    agent_server.AGENT_SERVER_TOKEN = "secret-token"
    agent_server.agent_config = {}
    monkeypatch.setattr(agent_server, "db", _FakeDB())

    req = _FakeRequest(headers={"Authorization": "Bearer secret-token"})
    first = json.loads((await agent_server.handle_health(req)).text)
    second = json.loads((await agent_server.handle_health(req)).text)

    assert first["boot_id"] == second["boot_id"]
