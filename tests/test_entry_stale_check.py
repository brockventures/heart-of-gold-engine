"""
Tests for mid-turn steering, entry-side check (2026-08-30, Ian's design —
#general 2026-08-29/30, 30s threshold agreed live).

Companion to the exit-side snapshot in test_mid_turn_stale_gate.py: that
one asks "did the channel move while I was thinking," this one asks "is
the message I'm about to answer already old by the time I've picked it
up" (busy in another channel, a rate-limit pause, a restart). When the
triggering message is older than ENTRY_STALE_THRESHOLD_SEC, this fetches
whatever's actually landed in the channel since — live, via the Discord
REST API, not just what made it into this agent's own message_queue —
and folds it into the prompt before the turn ever starts drafting.
"""

import asyncio
from datetime import datetime, timedelta

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


async def _queue_message(agent_server, agent, channel_id, message_id, age_seconds=0):
    created = (datetime.utcnow() - timedelta(seconds=age_seconds)).strftime("%Y-%m-%d %H:%M:%S")
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


def _wire_happy_path(agent_server, monkeypatch, *, recent_messages=None):
    """A turn that completes cleanly, with send_to_agent's prompt captured
    so tests can inspect whether the entry-stale-check injected anything."""
    monkeypatch.setattr(
        agent_server, "channels_config",
        {"channels": {"agent-chat": {"id": "chan-1"}}, "server_ids": ["guild-main"]},
    )
    monkeypatch.setattr(agent_server.banana, "in_scope", lambda channel_id, cfg: False)

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(agent_server, "start_typing", _noop)
    monkeypatch.setattr(agent_server, "stop_typing", _noop)

    sent = {}

    async def _fake_send_to_agent(agent, content, message_ids, activity=None):
        sent["content"] = content
        return None

    monkeypatch.setattr(agent_server, "send_to_agent", _fake_send_to_agent)

    async def _fake_read_agent_response(agent, channel_id, message_ids):
        return "hello there", {"input_tokens": 1}, "hello there", None

    monkeypatch.setattr(agent_server, "read_agent_response", _fake_read_agent_response)

    async def _fake_post_to_discord(agent, channel_id, content, reply_to=None):
        return "discord-msg-id"

    monkeypatch.setattr(agent_server, "post_to_discord", _fake_post_to_discord)
    monkeypatch.setattr(agent_server, "post_cost_update", _noop)
    monkeypatch.setattr(agent_server, "update_session_tokens", _noop)

    # Exit-side snapshot (already-landed feature) — hold it inert so these
    # tests isolate the entry-side behavior only.
    async def _fake_latest_id(agent, channel_id):
        return None

    monkeypatch.setattr(agent_server, "get_latest_channel_message_id", _fake_latest_id)

    fetch_calls = []

    async def _fake_recent(agent, channel_id, after_id=None, limit=10):
        fetch_calls.append(after_id)
        return recent_messages or []

    monkeypatch.setattr(agent_server, "get_recent_channel_messages", _fake_recent)

    return sent, fetch_calls


@pytest.mark.asyncio
async def test_fresh_message_skips_live_fetch_entirely(agent_server, monkeypatch):
    """The ordinary case: a message picked up promptly must not trigger a
    live Discord fetch at all — this has to stay silent overhead on every
    normal turn, not just silent output."""
    await _init_db(agent_server)
    await _queue_message(agent_server, "Marvin", "chan-1", "msg-1", age_seconds=1)
    sent, fetch_calls = _wire_happy_path(agent_server, monkeypatch)

    await agent_server.process_agent_queue("Marvin")

    assert fetch_calls == [], "a fresh trigger message must never fetch live context"
    assert "Entry stale-check" not in sent["content"]


@pytest.mark.asyncio
async def test_stale_message_fetches_and_injects_newer_context(agent_server, monkeypatch):
    """Core case: the trigger message is well past the 30s threshold, and
    something new really did land — must fetch with the right after_id
    and fold the result into what send_to_agent actually receives."""
    await _init_db(agent_server)
    await _queue_message(agent_server, "Marvin", "chan-1", "msg-1", age_seconds=90)
    recent = [
        {
            "id": "msg-2",
            "timestamp": "2026-08-30T02:59:00Z",
            "author": {"username": "Amos"},
            "content": "already answered this while you were away",
        }
    ]
    sent, fetch_calls = _wire_happy_path(agent_server, monkeypatch, recent_messages=recent)

    await agent_server.process_agent_queue("Marvin")

    assert fetch_calls == ["msg-1"], "must fetch strictly after this batch's own newest message_id"
    assert "Entry stale-check" in sent["content"]
    assert "90s old" in sent["content"] or "9" in sent["content"]
    assert "Amos: already answered this while you were away" in sent["content"]


@pytest.mark.asyncio
async def test_stale_message_but_nothing_new_injects_nothing(agent_server, monkeypatch):
    """Stale enough to check, but the live fetch comes back empty (no one
    actually said anything new) — must not add a hollow "nothing changed"
    block just because the check ran."""
    await _init_db(agent_server)
    await _queue_message(agent_server, "Marvin", "chan-1", "msg-1", age_seconds=90)
    sent, fetch_calls = _wire_happy_path(agent_server, monkeypatch, recent_messages=[])

    await agent_server.process_agent_queue("Marvin")

    assert fetch_calls == ["msg-1"]
    assert "Entry stale-check" not in sent["content"]


@pytest.mark.asyncio
async def test_silent_channel_never_triggers_the_check(agent_server, monkeypatch):
    """channel_id '0' (the dashboard's non-Discord silent chat) has no
    real channel to query and no real snowflake message_ids — must be
    skipped outright regardless of age."""
    await _init_db(agent_server)
    monkeypatch.setattr(
        agent_server, "channels_config",
        {"channels": {}, "server_ids": ["guild-main"]},
    )
    created = (datetime.utcnow() - timedelta(seconds=90)).strftime("%Y-%m-%d %H:%M:%S")
    await agent_server.db.execute(
        """
        INSERT INTO message_queue
            (agent, channel, channel_id, server, author, content, message_id, processed, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("Marvin", "dashboard", "0", "dashboard", "someone", "hi", "msg-1",
         agent_server.STATUS_QUEUED, created),
    )
    await agent_server.db.commit()
    sent, fetch_calls = _wire_happy_path(agent_server, monkeypatch)

    await agent_server.process_agent_queue("Marvin")

    assert fetch_calls == []
    assert "Entry stale-check" not in sent["content"]


@pytest.mark.asyncio
async def test_get_recent_channel_messages_returns_oldest_first(agent_server, monkeypatch):
    """Discord's API always returns newest-first regardless of before/
    after/around; callers here expect reading order (oldest-first)."""
    agent_server.AGENT_TOKENS = {"Marvin": "test-token"}

    class FakeResp:
        status = 200

        async def json(self):
            return [{"id": "3"}, {"id": "2"}]  # Discord's own newest-first order

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeSession:
        def get(self, url, headers=None, params=None):
            assert params.get("after") == "1"
            return FakeResp()

    monkeypatch.setattr(agent_server, "http_session", FakeSession())

    result = await agent_server.get_recent_channel_messages("Marvin", "chan-1", after_id="1")
    assert [m["id"] for m in result] == ["2", "3"]


@pytest.mark.asyncio
async def test_get_recent_channel_messages_skips_silent_channel(agent_server, monkeypatch):
    class FakeSession:
        def get(self, *a, **k):
            raise AssertionError("must not call Discord for channel_id '0'")

    monkeypatch.setattr(agent_server, "http_session", FakeSession())

    result = await agent_server.get_recent_channel_messages("Marvin", "0")
    assert result == []


@pytest.mark.asyncio
async def test_get_recent_channel_messages_never_raises(agent_server, monkeypatch):
    agent_server.AGENT_TOKENS = {"Marvin": "test-token"}

    class FakeSession:
        def get(self, *a, **k):
            raise ConnectionError("boom")

    monkeypatch.setattr(agent_server, "http_session", FakeSession())

    result = await agent_server.get_recent_channel_messages("Marvin", "chan-1")
    assert result == []
