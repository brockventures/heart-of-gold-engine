"""
Tests for task-1788042889 (2026-08-29): process_agent_queue's Banana
turn-claim release must fire even when the turn crashes mid-processing,
not only on the clean-completion path.

Real gap this closes: the release check used to sit unconditionally
*after* send_to_agent/read_agent_response/post_to_discord/the DB calls,
with no try/except anywhere in that span (see banana.py's release_self()
docstring, which already flagged "agent-server.py's turn-end release has
no try/except around this call"). Any exception raised in that span
skipped the release entirely, leaving an externally-held claim stuck
until banana.py's 600s CEILING_SECONDS backstop instead of being handed
back immediately. Fixed by moving the release check into a `finally`
wrapped around that span.
"""

import asyncio
from datetime import datetime

import pytest

from conftest import import_script


@pytest.fixture
def agent_server(tmp_path, monkeypatch):
    mod = import_script("agent-server")
    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "test-agent-server.db")
    return mod


async def _init_db(agent_server, agent="Marvin"):
    await agent_server.init_db()
    agent_server.agent_locks[agent] = asyncio.Lock()
    agent_server.agent_states[agent] = "IDLE"


async def _queue_message(agent_server, agent, channel_id, message_id):
    created = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    await agent_server.db.execute(
        """
        INSERT INTO message_queue
            (agent, channel, channel_id, author, content, message_id, processed, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (agent, "agent-chat", channel_id, "someone", "hi", message_id,
         agent_server.STATUS_QUEUED, created),
    )
    await agent_server.db.commit()


def _wire_common(agent_server, monkeypatch, *, claim_holder="Marvin", claim_active=True):
    """Common plumbing: a resolvable channel, banana scoped-in, a
    dangling claim on record (simulating an earlier turn that claimed
    the floor and hasn't released it yet), Discord/typing calls stubbed
    out since nothing here exercises the real client."""
    monkeypatch.setattr(
        agent_server, "channels_config",
        {"channels": {"agent-chat": {"id": "chan-1"}}, "server_ids": ["guild-main"]},
    )
    monkeypatch.setattr(agent_server.banana, "in_scope", lambda channel_id, cfg: True)
    monkeypatch.setattr(
        agent_server.banana, "get_status",
        lambda channel: {"active": claim_active, "holder": claim_holder},
    )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(agent_server, "start_typing", _noop)
    monkeypatch.setattr(agent_server, "stop_typing", _noop)
    monkeypatch.setattr(agent_server, "send_to_agent", _noop)


@pytest.mark.asyncio
async def test_release_fires_when_read_agent_response_raises(agent_server, monkeypatch):
    """Core regression: a crash inside read_agent_response (well before
    the old unconditional release check) must still release a claim
    that's on record as held by this agent."""
    await _init_db(agent_server)
    await _queue_message(agent_server, "Marvin", "chan-1", "msg-1")
    _wire_common(agent_server, monkeypatch)

    async def _boom(agent, channel_id, message_ids):
        raise RuntimeError("simulated mid-turn crash")

    monkeypatch.setattr(agent_server, "read_agent_response", _boom)

    released = []

    async def _fake_release_self(channel):
        released.append(channel)
        return True

    monkeypatch.setattr(agent_server.banana, "release_self", _fake_release_self)

    with pytest.raises(RuntimeError, match="simulated mid-turn crash"):
        await agent_server.process_agent_queue("Marvin")

    assert released == ["agent-chat"], (
        "release_self() must fire from the finally block even though the "
        "turn crashed before reaching the old unconditional release check"
    )


@pytest.mark.asyncio
async def test_release_not_called_when_agent_does_not_hold_claim(agent_server, monkeypatch):
    """Guard against over-correcting into an unconditional release: if
    this agent never held the claim, cleanup must stay a no-op even on
    a crash."""
    await _init_db(agent_server)
    await _queue_message(agent_server, "Marvin", "chan-1", "msg-1")
    _wire_common(agent_server, monkeypatch, claim_holder="SomeoneElse", claim_active=True)

    async def _boom(agent, channel_id, message_ids):
        raise RuntimeError("simulated mid-turn crash")

    monkeypatch.setattr(agent_server, "read_agent_response", _boom)

    released = []

    async def _fake_release_self(channel):
        released.append(channel)
        return True

    monkeypatch.setattr(agent_server.banana, "release_self", _fake_release_self)

    with pytest.raises(RuntimeError):
        await agent_server.process_agent_queue("Marvin")

    assert released == [], "must not release a claim this agent never held"


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_mask_original_exception(agent_server, monkeypatch):
    """The finally block is itself wrapped in try/except — a broken
    cleanup step (e.g. banana API/local-board hiccup) must not replace
    the real crash with a confusing one."""
    await _init_db(agent_server)
    await _queue_message(agent_server, "Marvin", "chan-1", "msg-1")
    _wire_common(agent_server, monkeypatch)

    async def _boom(agent, channel_id, message_ids):
        raise RuntimeError("simulated mid-turn crash")

    monkeypatch.setattr(agent_server, "read_agent_response", _boom)

    async def _broken_release_self(channel):
        raise ConnectionError("banana API unreachable")

    monkeypatch.setattr(agent_server.banana, "release_self", _broken_release_self)

    with pytest.raises(RuntimeError, match="simulated mid-turn crash"):
        await agent_server.process_agent_queue("Marvin")
