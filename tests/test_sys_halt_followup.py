"""
Tests for the /sys halt optional follow-up message (2026-08-30, Ian's ask:
"halt, and here's what to do instead" in one command).

interrupt_agent()/POST /agents/{name}/interrupt (bin/agent-server.py,
test_interrupt_agent.py) only stops the in-flight turn — it doesn't touch
message_queue at all. The follow-up half lives entirely in relay.py's
_run_sys_command("halt", ...): after a successful interrupt, an optional
message gets queued through the exact same POST /message path a normal
Discord message takes, so it's picked up by process_agent_queue's own
self-continuation pass once the interrupted turn actually clears (it can't
be sent directly — the interrupted turn still holds agent_locks[agent]
until its result event lands). These tests fake http_session and assert
against that queuing behavior, not against agent-server.py internals.
"""

import json
import os
import sys
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
        spec = importlib.util.spec_from_file_location("relay_under_test_halt", RELAY_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules["relay_under_test_halt"] = module
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
    a = relay.DiscordAdapter()
    a.http_session = None  # set per-test to a fake
    return a


class _FakeResp:
    def __init__(self, status, body=""):
        self.status = status
        self._body = body

    async def json(self):
        return json.loads(self._body) if self._body else {}

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    """Records every POST and answers based on a per-path status map."""

    def __init__(self, statuses):
        self._statuses = statuses  # {"/interrupt": 200, "/message": 202}
        self.calls = []

    def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        for suffix, status in self._statuses.items():
            if url.endswith(suffix):
                return _FakeResp(status)
        return _FakeResp(404)


class _FakeChannel:
    def __init__(self, id_, name):
        self.id = id_
        self.name = name


@pytest.mark.asyncio
async def test_halt_without_message_does_not_queue_anything(relay, adapter):
    """No message argument -> just the interrupt, no POST /message at all."""
    session = _FakeSession({"/interrupt": 200})
    adapter.http_session = session

    reply = await adapter._run_sys_command(
        "halt", "TestAgent", [], "Ian", channel=_FakeChannel("123", "general")
    )

    assert "sent — current turn interrupted, session intact" in reply
    assert "queued" not in reply
    assert len(session.calls) == 1
    assert session.calls[0]["url"].endswith("/interrupt")


@pytest.mark.asyncio
async def test_halt_with_message_queues_followup_on_success(relay, adapter):
    """A message arg + a successful interrupt queues it via POST /message,
    attributed to the caller, in the same channel, with mentions_agent set
    so it isn't gate-skipped."""
    session = _FakeSession({"/interrupt": 200, "/message": 202})
    adapter.http_session = session

    reply = await adapter._run_sys_command(
        "halt", "TestAgent", ["check the logs instead"], "Ian",
        channel=_FakeChannel("999", "signals"),
    )

    assert "sent — current turn interrupted" in reply
    assert "follow-up queued" in reply
    assert len(session.calls) == 2
    followup = session.calls[1]
    assert followup["url"].endswith("/message")
    payload = followup["json"]
    assert payload["agent"] == "TestAgent"
    assert payload["content"] == "check the logs instead"
    assert payload["channel_id"] == "999"
    assert payload["author"] == "Ian"
    assert payload["mentions_agent"] is True
    assert payload["is_bot"] is False


@pytest.mark.asyncio
async def test_halt_message_words_are_rejoined_with_single_spaces(relay, adapter):
    """Text-form /sys halt <agent> <words...> arrives as a whitespace-split
    extra_args list (handle_sys_command does `content.split()`), so the
    follow-up content should be a clean single-spaced rejoin."""
    session = _FakeSession({"/interrupt": 200, "/message": 202})
    adapter.http_session = session

    await adapter._run_sys_command(
        "halt", "TestAgent", ["stop", "that,", "check", "the", "logs"], "Ian",
        channel=_FakeChannel("1", "general"),
    )

    payload = session.calls[1]["json"]
    assert payload["content"] == "stop that, check the logs"


@pytest.mark.asyncio
async def test_halt_does_not_queue_followup_when_interrupt_fails(relay, adapter):
    """If the interrupt itself failed (e.g. no live subprocess), don't queue
    the follow-up at all — there was no turn to interrupt, and queuing
    would deliver a stray message with no context for why."""
    session = _FakeSession({"/interrupt": 500})
    adapter.http_session = session

    reply = await adapter._run_sys_command(
        "halt", "TestAgent", ["do this next"], "Ian",
        channel=_FakeChannel("1", "general"),
    )

    assert "failed (500)" in reply
    assert "queued" not in reply
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_halt_followup_spools_on_queue_failure(relay, adapter, monkeypatch):
    """If POST /message fails despite a successful interrupt, the follow-up
    must be spooled for retry (same never-drop guarantee a live Discord
    message gets via send_to_agent_server), not silently dropped."""
    session = _FakeSession({"/interrupt": 200, "/message": 503})
    adapter.http_session = session

    spooled = []
    monkeypatch.setattr(
        adapter, "_spool_deferred_poke",
        lambda payload, reason: spooled.append((payload, reason)),
    )

    reply = await adapter._run_sys_command(
        "halt", "TestAgent", ["do this next"], "Ian",
        channel=_FakeChannel("1", "general"),
    )

    assert "follow-up spooled for retry" in reply
    assert len(spooled) == 1
    assert spooled[0][0]["content"] == "do this next"


@pytest.mark.asyncio
async def test_halt_blank_message_is_treated_as_no_message(relay, adapter):
    """A whitespace-only follow-up (e.g. slash command param of just
    spaces) shouldn't queue an empty instruction."""
    session = _FakeSession({"/interrupt": 200})
    adapter.http_session = session

    reply = await adapter._run_sys_command(
        "halt", "TestAgent", ["   "], "Ian",
        channel=_FakeChannel("1", "general"),
    )

    assert "queued" not in reply
    assert len(session.calls) == 1
