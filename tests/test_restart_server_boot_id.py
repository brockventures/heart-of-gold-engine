"""
Tests for the /sys restart-server "done in 1s" bug (2026-08-30, Ian flagged
live: the command always reported finishing almost instantly, when real
recovery takes 1-2 minutes).

Root cause: graceful_shutdown() in bin/agent-server.py keeps the aiohttp
server (and /health) answering all the way through its own cleanup — idle
wait, per-agent session summaries, subprocess kills — right up until
sys.exit(0). _sys_restart_server()'s post-restart poll used to accept the
very first health response as proof the restart had happened, which almost
always meant it was still talking to the dying OLD process reporting its
own (obviously matching) session state back at itself. Fixed by adding a
per-process SERVER_BOOT_ID to /health (fresh on import, i.e. once per real
OS process) and requiring it to actually change before the poll accepts a
response as evidence of a completed restart.

These tests fake http_session and asyncio.create_subprocess_exec (same
pattern as test_reply_gate_scorer_fallback.py) — no real subprocess or
network call happens.
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent
RELAY_PATH = PACKAGE_ROOT / "bin" / "relay.py"

discord = pytest.importorskip("discord", reason="relay.py imports discord.py")


@pytest.fixture(scope="module")
def relay(tmp_path_factory):
    """Import bin/relay.py standalone, same pattern as
    test_reply_gate_scorer_fallback.py / test_relay_server_ids.py."""
    import importlib.util

    workspace = tmp_path_factory.mktemp("workspace")
    (workspace / "logs").mkdir()

    prev = os.environ.get("WORKSPACE_ROOT")
    os.environ["WORKSPACE_ROOT"] = str(workspace)
    bin_dir = str(PACKAGE_ROOT / "bin")
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    try:
        spec = importlib.util.spec_from_file_location("relay_under_test_restart", RELAY_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules["relay_under_test_restart"] = module
        spec.loader.exec_module(module)
    finally:
        if prev is None:
            os.environ.pop("WORKSPACE_ROOT", None)
        else:
            os.environ["WORKSPACE_ROOT"] = prev
    return module


@pytest.fixture
def adapter(relay):
    """A real DiscordAdapter instance. discord.Client.__init__ does no I/O
    (no login/connect), so this is safe to construct outside an event loop
    and without network access."""
    return relay.DiscordAdapter()


class _FakeHealthResp:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeHealthSession:
    """Answers GET /health with successive payloads from a queue, then
    repeats the last one. Nothing else (POST etc.) is exercised here."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.get_calls = 0

    def get(self, url, headers=None):
        self.get_calls += 1
        payload = self._payloads.pop(0) if len(self._payloads) > 1 else self._payloads[0]
        return _FakeHealthResp(payload)


def _idle_health(boot_id, session_id="abc12345"):
    return {
        "status": "healthy",
        "agents": {"TestAgent": {"state": "IDLE", "queue_depth": 0, "session_id": session_id}},
        "boot_id": boot_id,
        "pid": 111,
    }


async def _fake_exec_ok(*args, **kwargs):
    class _P:
        returncode = 0

        async def communicate(self):
            return b"", b""

    return _P()


@pytest.mark.asyncio
async def test_restart_server_waits_for_boot_id_to_change(relay, adapter, monkeypatch):
    """The old bug: a health response with the SAME boot_id as before the
    SIGTERM (i.e. still the dying old process) must NOT be accepted as
    'done' — only a genuinely different boot_id proves a real restart."""
    session = _FakeHealthSession([
        _idle_health("old-boot"),   # idle-wait check, pre-SIGTERM
        _idle_health("old-boot"),   # post-SIGTERM poll #1 — still the dying old process
        _idle_health("old-boot"),   # poll #2 — still dying
        _idle_health("new-boot"),   # poll #3 — actually restarted
    ])
    adapter.http_session = session
    monkeypatch.setattr(relay.asyncio, "create_subprocess_exec", _fake_exec_ok)

    # First call arms the confirmation.
    first = await adapter._run_sys_command("restart-server", None, author="Ian", channel=None)
    assert "confirm" in first.lower()

    # Second call within the window actually executes.
    reply = await adapter._run_sys_command("restart-server", None, author="Ian", channel=None)

    assert "done in" in reply
    assert "sessions preserved" in reply
    # Must have polled past the two stale-boot_id responses, not accepted
    # the first one it saw after SIGTERM.
    assert session.get_calls >= 4


@pytest.mark.asyncio
async def test_restart_server_accepts_immediate_new_boot_id(relay, adapter, monkeypatch):
    """If the new process is already up by the first post-SIGTERM poll
    (boot_id already different), it's a real restart — no artificial
    delay required."""
    session = _FakeHealthSession([
        _idle_health("old-boot"),
        _idle_health("new-boot"),
    ])
    adapter.http_session = session
    monkeypatch.setattr(relay.asyncio, "create_subprocess_exec", _fake_exec_ok)

    await adapter._run_sys_command("restart-server", None, author="Ian", channel=None)
    reply = await adapter._run_sys_command("restart-server", None, author="Ian", channel=None)

    assert "done in" in reply
    assert "sessions preserved" in reply


@pytest.mark.asyncio
async def test_restart_server_flags_unexpected_session_change(relay, adapter, monkeypatch):
    """A changed session_id post-restart is a real anomaly (crash-recovery
    started a fresh session instead of resuming) and must say so plainly,
    not report a clean 'done'."""
    session = _FakeHealthSession([
        _idle_health("old-boot", session_id="abc12345"),
        _idle_health("new-boot", session_id="DIFFERENT"),
    ])
    adapter.http_session = session
    monkeypatch.setattr(relay.asyncio, "create_subprocess_exec", _fake_exec_ok)

    await adapter._run_sys_command("restart-server", None, author="Ian", channel=None)
    reply = await adapter._run_sys_command("restart-server", None, author="Ian", channel=None)

    assert "CHANGED" in reply


@pytest.mark.asyncio
async def test_restart_server_times_out_if_boot_id_never_changes(relay, adapter, monkeypatch):
    """If every poll keeps seeing the same boot_id for the whole timeout
    window, that's a real stuck/crash-loop case — must report it as such,
    not fall back to declaring victory against the stale process."""
    session = _FakeHealthSession([_idle_health("old-boot")])  # never changes
    adapter.http_session = session
    monkeypatch.setattr(relay.asyncio, "create_subprocess_exec", _fake_exec_ok)
    relay.RESTART_SERVER_POST_RESTART_TIMEOUT_SEC = 2  # keep the test fast

    await adapter._run_sys_command("restart-server", None, author="Ian", channel=None)
    reply = await adapter._run_sys_command("restart-server", None, author="Ian", channel=None)

    assert "crash-loop" in reply
