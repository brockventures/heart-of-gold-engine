"""
Tests for task-1788046725 (2026-08-29): bin/speaking_banana.py (renamed
from banana.py the same night, precisely to make this possible — see its
module docstring) now delegates claim_self()/release_self()'s actual
network calls to AsyncBananaClient from the `banana-protocol` pip
package instead of a hand-rolled aiohttp POST.

The refactor is meant to be invisible to every existing caller: same
function names, same signatures, same return shapes, same fallback-to-
local-on-any-failure posture. The one real risk is the exception
translation — the external package's BananaBlockedError carries the
rejected holder as `.current_holder`, not `.state`/`.holder` like this
module's old, now-removed BananaBlocked did — a naming slip there would
silently turn a real 409 into an AttributeError instead of the intended
clean "blocked" dict. These tests exercise exactly that translation,
plus the unreachable/degrade-to-local path, against a mocked client
rather than the real hosted API (already sanity-checked live once
against the real API before writing these).
"""

import asyncio

import pytest

from conftest import import_script
from banana.client import BananaBlockedError


@pytest.fixture
def sb(tmp_path, monkeypatch):
    mod = import_script("speaking_banana")
    monkeypatch.setattr(mod, "BOARD_PATH", tmp_path / "banana_claims.json")
    monkeypatch.setattr(mod, "_api_token_cache", "fake-token-for-test")
    return mod


class _FakeClient:
    """Stands in for AsyncBananaClient — only claim()/release() are ever
    called by speaking_banana.py, so that's all this needs to fake."""

    def __init__(self, claim_result=None, claim_exc=None, release_result=None, release_exc=None):
        self._claim_result = claim_result
        self._claim_exc = claim_exc
        self._release_result = release_result
        self._release_exc = release_exc
        self.claim_calls = []
        self.release_calls = 0

    async def claim(self, subject="", preflight=True):
        self.claim_calls.append((subject, preflight))
        if self._claim_exc:
            raise self._claim_exc
        return self._claim_result

    async def release(self):
        self.release_calls += 1
        if self._release_exc:
            raise self._release_exc
        return self._release_result


@pytest.mark.asyncio
async def test_claim_self_success_writes_board_and_returns_dict(sb, monkeypatch):
    now_ts = sb._now().timestamp()
    fake = _FakeClient(claim_result={
        "state": {"claimed_at": now_ts, "last_active_ts": now_ts, "released": False},
        "conflict": None,
    })
    monkeypatch.setattr(sb, "_get_client", lambda: fake)

    result = await sb.claim_self("agent-chat", subject="test-subject")

    assert result["holder"] == "Marvin"
    assert result["collision_with"] is None
    assert fake.claim_calls == [("test-subject", False)], (
        "preflight must stay False — the server's own compare-and-swap "
        "on the POST is the real authority, not a separate client-side "
        "GET/status check"
    )
    status = sb.get_status("agent-chat")
    assert status["holder"] == "Marvin"
    assert status["active"] is True


@pytest.mark.asyncio
async def test_claim_self_blocked_translates_current_holder_cleanly(sb, monkeypatch):
    """The core regression this refactor could have introduced: the
    external exception's rejected-holder field is named `current_holder`,
    not `holder`. Using the wrong attribute name here would raise
    AttributeError instead of returning the clean blocked dict every
    caller (agent-server.py, tests) expects."""
    exc = BananaBlockedError("zero", {"holder": "zero", "claimed_at": 500.0, "released": False})
    fake = _FakeClient(claim_exc=exc)
    monkeypatch.setattr(sb, "_get_client", lambda: fake)

    result = await sb.claim_self("agent-chat", subject="test-subject")

    assert result == {
        "holder": "zero", "blocked": True, "collision_with": "zero", "via_api": True,
    }
    # Local board should now agree with the API's real holder instead of
    # silently claiming to be Marvin.
    status = sb.get_status("agent-chat")
    assert status["holder"] == "zero"


@pytest.mark.asyncio
async def test_claim_self_falls_back_to_local_on_unreachable(sb, monkeypatch):
    """A raw transport failure (the package doesn't wrap connection
    errors/timeouts in BananaError at all — see its client.py) must
    still degrade to the local-only claim(), not crash the caller."""
    fake = _FakeClient(claim_exc=ConnectionError("simulated network failure"))
    monkeypatch.setattr(sb, "_get_client", lambda: fake)

    result = await sb.claim_self("agent-chat", subject="test-subject")

    assert result["holder"] == "Marvin"
    status = sb.get_status("agent-chat")
    assert status["holder"] == "Marvin"
    assert status["active"] is True


@pytest.mark.asyncio
async def test_claim_self_no_token_skips_client_entirely(sb, monkeypatch):
    monkeypatch.setattr(sb, "_load_api_token", lambda: None)
    called = []
    monkeypatch.setattr(sb, "AsyncBananaClient", lambda *a, **k: called.append(1) or _FakeClient())

    result = await sb.claim_self("agent-chat", subject="test-subject")

    assert called == [], "no token means no client should ever be constructed"
    assert result["holder"] == "Marvin"  # local claim() fallback


@pytest.mark.asyncio
async def test_release_self_success(sb, monkeypatch):
    fake = _FakeClient(release_result={"released": True})
    monkeypatch.setattr(sb, "_get_client", lambda: fake)
    sb._save_board({"agent-chat": {"holder": "Marvin", "released": False}})

    released = await sb.release_self("agent-chat")

    assert released is True
    assert fake.release_calls == 1
    assert sb._load_board()["agent-chat"]["released"] is True


@pytest.mark.asyncio
async def test_release_self_blocked_returns_false_not_crash(sb, monkeypatch):
    exc = BananaBlockedError("someone-else", {})
    fake = _FakeClient(release_exc=exc)
    monkeypatch.setattr(sb, "_get_client", lambda: fake)

    released = await sb.release_self("agent-chat")

    assert released is False


@pytest.mark.asyncio
async def test_release_self_falls_back_to_local_on_unreachable(sb, monkeypatch):
    fake = _FakeClient(release_exc=TimeoutError("simulated timeout"))
    monkeypatch.setattr(sb, "_get_client", lambda: fake)
    sb._save_board({"agent-chat": {"holder": "Marvin", "released": False}})

    released = await sb.release_self("agent-chat")

    assert released is True
    assert sb._load_board()["agent-chat"]["released"] is True
