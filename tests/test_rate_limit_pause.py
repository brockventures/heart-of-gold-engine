"""
Tests for the rate-limit pause/compaction logic in bin/agent-server.py
(is_rate_limit_paused, maybe_rate_limit_compact).

Unlike test_agent_server_routes.py, these import the real module rather
than parsing source — is_rate_limit_paused()/maybe_rate_limit_compact()
are pure enough (no event loop, no sqlite, no subprocess of their own)
that importing is safe and lets us simulate actual Anthropic
rate_limit_info payloads instead of just checking the source shape.
Nothing in this module starts the server at import time — main()/startup()
are only called from `if __name__ == "__main__"` / app.on_startup, neither
of which fires on import.
"""

import pytest

from conftest import import_script


@pytest.fixture
def agent_server():
    return import_script("agent-server")


# Simulated Anthropic rate_limit_info payloads, matching the real shape
# observed live (see memory fact ratelimit-freeze-2026-08-07: status,
# utilization, overageInUse, surpassedThreshold, isUsingOverage, resetsAt).
#
# 2026-08-08 update: status=="allowed_warning" only hard-pauses when
# rateLimitType=="five_hour" — that window self-heals within hours. The
# weekly (seven_day) window also reports allowed_warning but its resetsAt
# can be days out; treating it the same held Marvin+relay's real queues
# shut for ~2 days (see facts/ around 2026-08-08, commit 3fac226). The
# utilization backstop is unaffected — it's about actual spend, not which
# window reported the warning.
@pytest.mark.parametrize("label,info,expected", [
    ("healthy", {"status": "allowed", "utilization": 0.42}, False),
    ("five_hour warning", {"status": "allowed_warning", "rateLimitType": "five_hour", "utilization": 0.91}, True),
    ("seven_day warning does NOT hard-pause",
     {"status": "allowed_warning", "rateLimitType": "seven_day", "utilization": 0.77}, False),
    ("warning with no rateLimitType at all does NOT hard-pause",
     {"status": "allowed_warning", "utilization": 0.91}, False),
    ("utilization only, status still allowed",
     {"status": "allowed", "utilization": 0.985, "overageInUse": True}, True),
    ("exactly at the utilization threshold", {"status": "allowed", "utilization": 0.97}, True),
    ("just under the utilization threshold", {"status": "allowed", "utilization": 0.969999}, False),
    ("seven_day warning past utilization threshold still pauses on utilization backstop",
     {"status": "allowed_warning", "rateLimitType": "seven_day", "utilization": 0.99}, True),
    ("empty/missing info (e.g. right after startup)", {}, False),
])
def test_is_rate_limit_paused(agent_server, label, info, expected):
    agent_server.agent_rate_limits["TestAgent"] = info
    assert agent_server.is_rate_limit_paused("TestAgent") is expected, label


def test_status_checked_before_utilization(agent_server):
    """Ian's ask (2026-08-08): check status first since it's the cheap
    common-case short-circuit. Can't observe order directly on a dict
    lookup, but we can confirm a five_hour warning alone is sufficient to
    pause even when utilization is missing/low — proving the utilization
    branch isn't a hard requirement, i.e. it's an OR, and status is
    evaluated without needing utilization to be present at all."""
    agent_server.agent_rate_limits["TestAgent"] = {"status": "allowed_warning", "rateLimitType": "five_hour"}
    assert agent_server.is_rate_limit_paused("TestAgent") is True


@pytest.mark.asyncio
async def test_maybe_rate_limit_compact_fires_at_threshold(agent_server, monkeypatch):
    calls = []

    async def fake_compact_session(agent, reason):
        calls.append((agent, reason))
        return True

    monkeypatch.setattr(agent_server, "compact_session", fake_compact_session)

    agent_server.agent_rate_limits["TestAgent"] = {"status": "allowed", "utilization": 0.42}
    assert await agent_server.maybe_rate_limit_compact("TestAgent", already_compacted=False) is False
    assert calls == []

    agent_server.agent_rate_limits["TestAgent"] = {"status": "allowed", "utilization": 0.98}
    assert await agent_server.maybe_rate_limit_compact("TestAgent", already_compacted=False) is True
    assert calls == [("TestAgent", "rate-limit utilization")]


@pytest.mark.asyncio
async def test_maybe_rate_limit_compact_skips_if_already_compacted(agent_server, monkeypatch):
    """No point paying for a second finalize+restart in the same turn if
    the token-target trigger already compacted this session."""
    calls = []

    async def fake_compact_session(agent, reason):
        calls.append((agent, reason))
        return True

    monkeypatch.setattr(agent_server, "compact_session", fake_compact_session)

    agent_server.agent_rate_limits["TestAgent"] = {"status": "allowed", "utilization": 0.99}
    result = await agent_server.maybe_rate_limit_compact("TestAgent", already_compacted=True)
    assert result is False
    assert calls == []
