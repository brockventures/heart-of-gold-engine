"""relay.py graceful SIGTERM handling, added 2026-08-11.

Amos reported two live outages caused by this exact shape of bug on his
side: a supervised process gets SIGTERM'd (here, via reload-on-commit.py
bouncing relay when bin/relay.py or bin/reply_gate.py changes) with no
grace period, tearing down whatever handler was mid-flight — an in-flight
Discord reply, a /sys command response, a dispatch to agent-server.
bin/agent-server.py sidesteps this by being excluded from auto-bounce
entirely; relay can't take that option since it needs to pick up its own
code changes. These tests exercise the fix in isolation: an in-flight
counter (_INFLIGHT_COUNT) tracked via the _inflight_tracked decorator,
and _graceful_shutdown draining it (bounded) before closing the client.
"""

import asyncio
import sys

import pytest

from conftest import import_script, PACKAGE_ROOT


@pytest.fixture
def relay():
    # Same sys.path fix test_attachments.py uses — relay.py does bare
    # `from reply_gate import ...` / `from handoff import ...`, which only
    # resolves with bin/ on sys.path.
    bin_dir = str(PACKAGE_ROOT / "bin")
    added = bin_dir not in sys.path
    if added:
        sys.path.insert(0, bin_dir)
    try:
        return import_script("relay")
    finally:
        if added:
            sys.path.remove(bin_dir)


@pytest.mark.asyncio
async def test_inflight_tracked_increments_and_decrements(relay):
    assert relay._INFLIGHT_COUNT == 0

    seen_during = []

    @relay._inflight_tracked
    async def handler():
        seen_during.append(relay._INFLIGHT_COUNT)

    await handler()
    assert seen_during == [1]
    assert relay._INFLIGHT_COUNT == 0


@pytest.mark.asyncio
async def test_inflight_tracked_decrements_even_on_exception(relay):
    """A handler that raises must still release its slot — otherwise one
    bad message wedges every future shutdown into waiting out the full
    grace period for a count that can never reach zero."""
    assert relay._INFLIGHT_COUNT == 0

    @relay._inflight_tracked
    async def handler():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await handler()
    assert relay._INFLIGHT_COUNT == 0


@pytest.mark.asyncio
async def test_inflight_tracked_handles_concurrent_calls(relay):
    """Two handlers in flight at once must both be counted, and neither
    should stomp the other's decrement — the risk with a plain module
    global if it weren't safe under asyncio's single-threaded interleaving."""
    assert relay._INFLIGHT_COUNT == 0
    release = asyncio.Event()
    peak = []

    @relay._inflight_tracked
    async def handler():
        await release.wait()

    t1 = asyncio.create_task(handler())
    t2 = asyncio.create_task(handler())
    await asyncio.sleep(0)  # let both handlers start and increment
    peak.append(relay._INFLIGHT_COUNT)
    release.set()
    await t1
    await t2

    assert peak == [2]
    assert relay._INFLIGHT_COUNT == 0


@pytest.mark.asyncio
async def test_graceful_shutdown_waits_for_drain_then_closes(relay, monkeypatch):
    """Core behavior: if a handler is in flight when SIGTERM arrives,
    _graceful_shutdown must not close the client until it finishes (within
    the grace period)."""
    monkeypatch.setattr(relay, "GRACEFUL_SHUTDOWN_TIMEOUT_SEC", 2)
    relay._INFLIGHT_COUNT = 1

    closed = []

    class FakeClient:
        async def close(self):
            closed.append(relay._INFLIGHT_COUNT)

    async def finish_handler_soon():
        await asyncio.sleep(0.05)
        relay._INFLIGHT_COUNT = 0

    asyncio.create_task(finish_handler_soon())
    await relay._graceful_shutdown(FakeClient())

    assert closed == [0], "close() must only happen after the handler drained to 0"


@pytest.mark.asyncio
async def test_graceful_shutdown_gives_up_after_timeout(relay, monkeypatch):
    """A wedged handler must not block shutdown forever — the grace period
    is a bound, not a guarantee. Proceeds and closes anyway, logging the
    drop rather than hanging the restart indefinitely."""
    monkeypatch.setattr(relay, "GRACEFUL_SHUTDOWN_TIMEOUT_SEC", 0.1)
    relay._INFLIGHT_COUNT = 1  # never released

    closed = []

    class FakeClient:
        async def close(self):
            closed.append(relay._INFLIGHT_COUNT)

    await relay._graceful_shutdown(FakeClient())

    assert closed == [1], "must still close() even though the count never hit 0"
    relay._INFLIGHT_COUNT = 0  # reset shared module state for other tests
