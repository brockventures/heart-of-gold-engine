"""
Tests for bin/relay.py's DiscordAdapter.score_with_cheap_model() and
_substance_floor() — the retry-then-None fallback fixed 2026-08-09 after
Amos independently hit and fixed the same bug on his side (a scorer
failure used to collapse into score=0.0, indistinguishable in the logs
from a real confident-no).

Nothing before tonight exercised this path directly: reply_gate.py's own
selftest covers the generic resolve(score=None, fallback=...) mechanism,
but the actual retry loop, the two-strikes-then-None behaviour, and the
substance-floor heuristic live in relay.py and had zero test coverage.
Real `claude` subprocess calls are never made here — asyncio.create_
subprocess_exec is monkeypatched so the retry/failure paths are exercised
without spawning anything or depending on the `claude` binary being
present (it isn't, in this sandbox).
"""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent
RELAY_PATH = PACKAGE_ROOT / "bin" / "relay.py"

discord = pytest.importorskip("discord", reason="relay.py imports discord.py")


@pytest.fixture(scope="module")
def relay(tmp_path_factory):
    """Import bin/relay.py standalone, same pattern as test_relay_server_ids.py."""
    import importlib.util

    workspace = tmp_path_factory.mktemp("workspace")
    (workspace / "logs").mkdir()

    prev = os.environ.get("WORKSPACE_ROOT")
    os.environ["WORKSPACE_ROOT"] = str(workspace)
    bin_dir = str(PACKAGE_ROOT / "bin")
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    try:
        spec = importlib.util.spec_from_file_location("relay_under_test_scorer", RELAY_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules["relay_under_test_scorer"] = module
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


def _fake_proc(stdout_bytes: bytes, raise_on_communicate: Exception | None = None):
    proc = SimpleNamespace()
    if raise_on_communicate is not None:
        async def communicate():
            raise raise_on_communicate
    else:
        async def communicate():
            return (stdout_bytes, b"")
    proc.communicate = communicate
    return proc


# -- _substance_floor --------------------------------------------------------

def test_substance_floor_short_plain_message_is_false(relay):
    assert relay.DiscordAdapter._substance_floor("ok") is False


def test_substance_floor_long_message_is_true(relay):
    assert relay.DiscordAdapter._substance_floor("one two three four five six seven eight") is True


def test_substance_floor_short_question_is_true(relay):
    assert relay.DiscordAdapter._substance_floor("why?") is True


def test_substance_floor_empty_or_none_is_false(relay):
    assert relay.DiscordAdapter._substance_floor("") is False
    assert relay.DiscordAdapter._substance_floor(None) is False


# -- score_with_cheap_model ---------------------------------------------------

@pytest.mark.asyncio
async def test_returns_parsed_score_on_first_success(relay, adapter, monkeypatch):
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return _fake_proc(b"0.85\n")

    monkeypatch.setattr(relay.asyncio, "create_subprocess_exec", fake_exec)
    score = await adapter.score_with_cheap_model("context", "someone")
    assert score == 0.85
    assert len(calls) == 1, "must not retry when the first attempt succeeds"


@pytest.mark.asyncio
async def test_retries_once_then_succeeds(relay, adapter, monkeypatch):
    """The whole point of the retry: a single transient hiccup must not
    fall all the way through to the fallback path."""
    attempts = {"n": 0}

    async def fake_exec(*args, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise TimeoutError("simulated hang")
        return _fake_proc(b"0.2\n")

    monkeypatch.setattr(relay.asyncio, "create_subprocess_exec", fake_exec)
    score = await adapter.score_with_cheap_model("context", "someone")
    assert score == 0.2
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_returns_none_not_zero_after_two_failures(relay, adapter, monkeypatch):
    """The actual bug fix: two dead attempts must produce None, never a
    fabricated 0.0 that reads identically to a real confident-no score."""
    attempts = {"n": 0}

    async def fake_exec(*args, **kwargs):
        attempts["n"] += 1
        raise ConnectionError("simulated: claude binary missing")

    monkeypatch.setattr(relay.asyncio, "create_subprocess_exec", fake_exec)
    score = await adapter.score_with_cheap_model("context", "someone")
    assert score is None
    assert attempts["n"] == 2, "must try exactly twice, not once and not forever"


@pytest.mark.asyncio
async def test_returns_none_on_unparseable_output_both_times(relay, adapter, monkeypatch):
    async def fake_exec(*args, **kwargs):
        return _fake_proc(b"I refuse to output a number today")

    monkeypatch.setattr(relay.asyncio, "create_subprocess_exec", fake_exec)
    score = await adapter.score_with_cheap_model("context", "someone")
    assert score is None


@pytest.mark.asyncio
async def test_timeout_is_treated_as_a_failure_not_a_crash(relay, adapter, monkeypatch):
    """asyncio.wait_for raising TimeoutError is the realistic failure mode
    (a hung claude -p call) — must be caught like any other exception, not
    propagate out of score_with_cheap_model."""
    async def fake_exec(*args, **kwargs):
        return _fake_proc(b"", raise_on_communicate=asyncio.TimeoutError())

    monkeypatch.setattr(relay.asyncio, "create_subprocess_exec", fake_exec)
    # wait_for itself also needs to actually time out for this to be
    # realistic; simplest reliable simulation is communicate() raising
    # TimeoutError directly, since asyncio.wait_for re-raises it.
    score = await adapter.score_with_cheap_model("context", "someone")
    assert score is None
