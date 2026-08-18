"""
Tests for the monthly-spend-cap detection/notification added to
bin/agent-server.py 2026-08-18 (CLI_SPEND_LIMIT_SIGNATURE,
_notify_spend_limit, and the result-event handling in
read_agent_response).

Real incident this closes: the Claude CLI subprocess's own monthly
spend-cap hard-stop ("You've hit your monthly spend limit · raise it at
claude.ai/settings/usage?from=cc_cli_limit_message") arrives as a bare
`result`/`error` string with no assistant content at all. Before this
fix, that string went straight through the normal reply path and got
posted to #signals verbatim — once per 30-minute heartbeat, for about
2.5 hours (05:56-08:27 UTC) before anyone caught it, because nothing
distinguished it from a real (if terse) reply.

Like test_rate_limit_pause.py and test_mechanical_status.py, this
imports the real module — read_agent_response has real subprocess I/O
(proc.stdout.readline()), so a minimal fake process with a
stream-json-shaped result event is enough to exercise the actual
detection code without a live Claude CLI.
"""

import json

import pytest

from conftest import import_script


class FakeStdout:
    """Minimal async stand-in for proc.stdout — readline() pops one
    pre-baked line at a time, then signals EOF with b'' like a real
    closed pipe."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)


class FakeProc:
    def __init__(self, lines):
        self.stdout = FakeStdout(lines)


def _line(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode()


def _result_event(**overrides) -> bytes:
    base = {
        "type": "result",
        "usage": {},
        "total_cost_usd": 0.0,
        "duration_ms": 100,
        "is_error": False,
        "session_id": "test-session",
    }
    base.update(overrides)
    return _line(base)


REAL_LEAKED_MESSAGE = (
    "You've hit your monthly spend limit · raise it at "
    "claude.ai/settings/usage?from=cc_cli_limit_message"
)


@pytest.fixture
def agent_server(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    mod = import_script("agent-server")
    mod.agent_config["TestAgent"] = {"tool_streaming": False, "stream_to_channel": False}
    mod.channels_config = {"channels": {"signals": {"id": "999888777"}}}
    return mod


@pytest.mark.asyncio
async def test_spend_limit_message_never_becomes_a_reply(agent_server):
    """The core of the fix: the leaked CLI string must not come back as
    final_text/pending_final, which is what process_agent_queue posts to
    Discord as though it were a real answer."""
    agent_server.agent_processes["TestAgent"] = FakeProc(
        [_result_event(result=REAL_LEAKED_MESSAGE)]
    )
    final_text, metadata, pending_final, _ = await agent_server.read_agent_response(
        "TestAgent", "999888777", []
    )
    assert final_text == ""
    assert pending_final == ""
    assert metadata["spend_limit_blocked"] is True


@pytest.mark.asyncio
async def test_spend_limit_detected_via_error_field_too(agent_server):
    """The fallback in read_agent_response reads `result` OR `error` —
    the signature check has to cover both, since which field the CLI
    populates isn't something this code controls."""
    agent_server.agent_processes["TestAgent"] = FakeProc(
        [_result_event(result="", error=REAL_LEAKED_MESSAGE, is_error=True)]
    )
    final_text, metadata, pending_final, _ = await agent_server.read_agent_response(
        "TestAgent", "999888777", []
    )
    assert final_text == ""
    assert metadata["spend_limit_blocked"] is True


@pytest.mark.asyncio
async def test_ordinary_result_unaffected(agent_server):
    """A normal terse reply (no assistant content, just a flat `result`
    string — the same shape as the leak) must NOT get flagged or
    suppressed. Only the specific signature trips this."""
    agent_server.agent_processes["TestAgent"] = FakeProc(
        [_result_event(result="All done, no changes needed.")]
    )
    final_text, metadata, pending_final, _ = await agent_server.read_agent_response(
        "TestAgent", "999888777", []
    )
    assert final_text == "All done, no changes needed."
    assert pending_final == "All done, no changes needed."
    assert "spend_limit_blocked" not in metadata


@pytest.mark.asyncio
async def test_conversation_mentioning_spend_limits_is_not_a_false_positive(agent_server):
    """Ordinary text that happens to discuss spend/limits (plausible in
    this very codebase's own conversations) must not trip the detector —
    only the distinctive cc_cli_limit_message URL fragment does."""
    text = "We removed the monthly spend limit cap in the cost model migration."
    agent_server.agent_processes["TestAgent"] = FakeProc([_result_event(result=text)])
    final_text, metadata, pending_final, _ = await agent_server.read_agent_response(
        "TestAgent", "999888777", []
    )
    assert final_text == text
    assert "spend_limit_blocked" not in metadata


class TestNotifySpendLimit:
    """_notify_spend_limit — the #signals alert, mirroring
    _notify_rate_limit_pause's style but pinging the owner directly since
    this doesn't self-resolve on a timer."""

    @pytest.mark.asyncio
    async def test_blocked_message_pings_owner_and_names_the_url(self, agent_server, monkeypatch):
        posted = []

        async def fake_post(agent, channel_id, content, reply_to=None):
            posted.append((agent, channel_id, content))
            return "msg-id"

        monkeypatch.setattr(agent_server, "post_to_discord", fake_post)
        agent_server.OWNER_DISCORD_ID = "123456789"

        await agent_server._notify_spend_limit("TestAgent", blocked=True)

        assert len(posted) == 1
        agent, channel_id, content = posted[0]
        assert channel_id == "999888777"
        assert "<@123456789>" in content
        assert "claude.ai/settings/usage" in content

    @pytest.mark.asyncio
    async def test_resumed_message_has_no_ping(self, agent_server, monkeypatch):
        posted = []

        async def fake_post(agent, channel_id, content, reply_to=None):
            posted.append(content)
            return "msg-id"

        monkeypatch.setattr(agent_server, "post_to_discord", fake_post)
        agent_server.OWNER_DISCORD_ID = "123456789"

        await agent_server._notify_spend_limit("TestAgent", blocked=False)

        assert len(posted) == 1
        assert "<@123456789>" not in posted[0]

    @pytest.mark.asyncio
    async def test_noop_when_no_signals_channel_configured(self, agent_server, monkeypatch):
        posted = []

        async def fake_post(agent, channel_id, content, reply_to=None):
            posted.append(content)
            return "msg-id"

        monkeypatch.setattr(agent_server, "post_to_discord", fake_post)
        agent_server.channels_config = {"channels": {}}

        await agent_server._notify_spend_limit("TestAgent", blocked=True)

        assert posted == []

    @pytest.mark.asyncio
    async def test_never_raises_on_post_failure(self, agent_server, monkeypatch):
        async def fake_post(agent, channel_id, content, reply_to=None):
            raise RuntimeError("discord is down")

        monkeypatch.setattr(agent_server, "post_to_discord", fake_post)

        # Must not raise — same fire-and-forget contract as
        # _notify_rate_limit_pause.
        await agent_server._notify_spend_limit("TestAgent", blocked=True)
