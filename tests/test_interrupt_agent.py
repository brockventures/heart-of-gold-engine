"""
Tests for interrupt_agent() / POST /agents/{name}/interrupt in
bin/agent-server.py (2026-08-30, Ian's ask: a Discord-native "HALT"
command mirroring the Claude CLI's own interrupt, for "I need you to STOP
on this specific thing" without losing the session).

The exact wire protocol was verified live against the real `claude` CLI in
--input-format stream-json / --output-format stream-json mode (not taken
from docs, which don't fully spell it out): a
{"type": "control_request", "request_id": ..., "request": {"subtype":
"interrupt"}} line on stdin gets a {"type": "control_response", ...}
ack, the in-flight turn actually stops (a synthetic "[Request interrupted
by user]" user turn appears, then a `result` event with is_error: true),
and the subprocess and session both survive intact. These tests cover the
half that's ours to get right: writing the right thing to stdin, handling
a missing/broken subprocess without raising, and the HTTP endpoint's
auth/routing — not the CLI's own behavior, which isn't under test here.
"""

import json

import pytest

from conftest import import_script


@pytest.fixture
def agent_server():
    return import_script("agent-server")


class _FakeRequest:
    """Minimal aiohttp.web.Request stand-in — see test_rate_limit_override.py
    for why a full TestClient isn't used here."""

    def __init__(self, headers=None, match_info=None, body=None):
        self.headers = headers or {}
        self.match_info = match_info or {}
        self._body = body
        self.can_read_body = body is not None

    async def json(self):
        return self._body


class _FakeStdin:
    def __init__(self, raise_on_write=None):
        self.written = []
        self._raise_on_write = raise_on_write
        self.drained = 0

    def write(self, data):
        if self._raise_on_write:
            raise self._raise_on_write
        self.written.append(data)

    async def drain(self):
        self.drained += 1


class _FakeProc:
    def __init__(self, stdin):
        self.stdin = stdin


# -- interrupt_agent() ---------------------------------------------------------

@pytest.mark.asyncio
async def test_interrupt_agent_writes_the_verified_control_request_shape(agent_server):
    """Must match the exact envelope captured live: type/request_id/request,
    with request.subtype == "interrupt" — not a guessed variant."""
    stdin = _FakeStdin()
    agent_server.agent_processes["TestAgent"] = _FakeProc(stdin)

    result = await agent_server.interrupt_agent("TestAgent")

    assert result["ok"] is True
    assert "request_id" in result
    assert stdin.drained == 1
    assert len(stdin.written) == 1
    sent = json.loads(stdin.written[0].decode())
    assert sent["type"] == "control_request"
    assert sent["request"] == {"subtype": "interrupt"}
    assert sent["request_id"] == result["request_id"]


@pytest.mark.asyncio
async def test_interrupt_agent_request_id_is_unique_per_call(agent_server):
    """Each interrupt gets its own request_id — the CLI's control_response
    echoes it back, so a caller tracking multiple in-flight halts needs
    them distinguishable."""
    stdin = _FakeStdin()
    agent_server.agent_processes["TestAgent"] = _FakeProc(stdin)

    first = await agent_server.interrupt_agent("TestAgent")
    second = await agent_server.interrupt_agent("TestAgent")

    assert first["request_id"] != second["request_id"]


@pytest.mark.asyncio
async def test_interrupt_agent_no_subprocess(agent_server):
    agent_server.agent_processes.pop("NoSuchAgent", None)
    result = await agent_server.interrupt_agent("NoSuchAgent")
    assert result == {"ok": False, "error": "no subprocess"}


@pytest.mark.asyncio
async def test_interrupt_agent_dead_stdin(agent_server):
    """A subprocess object with no live stdin (e.g. already exited) must
    report failure, not raise."""
    agent_server.agent_processes["TestAgent"] = _FakeProc(None)
    result = await agent_server.interrupt_agent("TestAgent")
    assert result == {"ok": False, "error": "no subprocess"}


@pytest.mark.asyncio
async def test_interrupt_agent_write_failure_does_not_raise(agent_server):
    """A broken pipe (subprocess died mid-write) must degrade to a clean
    ok:False, matching send_to_agent()'s own error posture — this is a
    best-effort control signal, not something that should crash the
    caller (an HTTP handler, ultimately a Discord command reply)."""
    stdin = _FakeStdin(raise_on_write=BrokenPipeError("pipe closed"))
    agent_server.agent_processes["TestAgent"] = _FakeProc(stdin)

    result = await agent_server.interrupt_agent("TestAgent")

    assert result["ok"] is False
    assert "pipe closed" in result["error"]


@pytest.mark.asyncio
async def test_interrupt_agent_does_not_touch_agent_locks(agent_server):
    """The whole point of /sys halt is to interrupt a turn that's currently
    holding agent_locks[agent] — if interrupt_agent() tried to acquire
    that lock first, it would block for exactly as long as the busy turn
    it's trying to stop, defeating the command. Simulate a held lock and
    confirm the call still completes promptly."""
    import asyncio

    stdin = _FakeStdin()
    agent_server.agent_processes["TestAgent"] = _FakeProc(stdin)
    lock = asyncio.Lock()
    agent_server.agent_locks["TestAgent"] = lock
    await lock.acquire()  # simulate a turn in progress, holding the lock
    try:
        result = await asyncio.wait_for(
            agent_server.interrupt_agent("TestAgent"), timeout=1.0
        )
        assert result["ok"] is True
    finally:
        lock.release()


# -- POST /agents/{name}/interrupt ---------------------------------------------

@pytest.mark.asyncio
async def test_interrupt_endpoint_requires_auth(agent_server):
    agent_server.AGENT_SERVER_TOKEN = "secret-token"
    agent_server.agent_config = {"TestAgent": {}}
    req = _FakeRequest(headers={}, match_info={"name": "TestAgent"})
    resp = await agent_server.handle_agent_interrupt(req)
    assert resp.status == 401


@pytest.mark.asyncio
async def test_interrupt_endpoint_wrong_token(agent_server):
    agent_server.AGENT_SERVER_TOKEN = "secret-token"
    agent_server.agent_config = {"TestAgent": {}}
    req = _FakeRequest(
        headers={"Authorization": "Bearer wrong-token"},
        match_info={"name": "TestAgent"},
    )
    resp = await agent_server.handle_agent_interrupt(req)
    assert resp.status == 401


@pytest.mark.asyncio
async def test_interrupt_endpoint_unknown_agent(agent_server):
    agent_server.AGENT_SERVER_TOKEN = "secret-token"
    agent_server.agent_config = {}
    req = _FakeRequest(
        headers={"Authorization": "Bearer secret-token"},
        match_info={"name": "GhostAgent"},
    )
    resp = await agent_server.handle_agent_interrupt(req)
    assert resp.status == 404


@pytest.mark.asyncio
async def test_interrupt_endpoint_success(agent_server):
    agent_server.AGENT_SERVER_TOKEN = "secret-token"
    agent_server.agent_config = {"TestAgent": {}}
    agent_server.agent_processes["TestAgent"] = _FakeProc(_FakeStdin())
    req = _FakeRequest(
        headers={"Authorization": "Bearer secret-token"},
        match_info={"name": "TestAgent"},
    )

    resp = await agent_server.handle_agent_interrupt(req)

    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["status"] == "interrupted"
    assert "request_id" in data


@pytest.mark.asyncio
async def test_interrupt_endpoint_reports_failure_as_500(agent_server):
    agent_server.AGENT_SERVER_TOKEN = "secret-token"
    agent_server.agent_config = {"TestAgent": {}}
    agent_server.agent_processes.pop("TestAgent", None)  # no live subprocess
    req = _FakeRequest(
        headers={"Authorization": "Bearer secret-token"},
        match_info={"name": "TestAgent"},
    )

    resp = await agent_server.handle_agent_interrupt(req)

    assert resp.status == 500
    data = json.loads(resp.text)
    assert data["status"] == "failed"
