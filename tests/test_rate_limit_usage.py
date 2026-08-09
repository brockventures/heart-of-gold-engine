"""
Tests for rate-limit headroom tracking, ported/adapted from
mcarmody/karakos-package#128 (2026-08-09).

`cost_events`/`/cost` track dollars. Dollars are not what stops an agent
mid-sentence — the rate limit is, and until now the only visibility into
it was status/utilization (see test_rate_limit_pause.py), which Amos's
instance confirmed can be entirely absent from the CLI's rate_limit_event
(no utilization field at all, ever). Window *time* progress — computed
from resetsAt + a nominal window length — is tracked as an independent,
complementary signal, and surfaced via GET /usage and /sys usage.

Unlike upstream, this doesn't add a parallel rate_limit_state table —
every field the read side needs is already on the existing `rate_limits`
row per agent (see _record_rate_limit_event in test_rate_limit_pause.py's
module), so this reuses agent_rate_limits (the in-memory mirror of that
table) instead of duplicating storage.

Same import pattern as test_rate_limit_pause.py: rate_limit_window_progress
/ format_usage_report / is_rate_limit_warning are pure enough (no event
loop, no sqlite, no subprocess) that importing the real module is safe.
"""

import time

import pytest

from conftest import import_script


@pytest.fixture
def agent_server():
    return import_script("agent-server")


# ---------------------------------------------------------------------------
# rate_limit_window_progress
# ---------------------------------------------------------------------------

def test_window_progress_none_when_info_missing(agent_server):
    assert agent_server.rate_limit_window_progress(None) is None
    assert agent_server.rate_limit_window_progress({}) is None


def test_window_progress_none_when_resets_at_missing(agent_server):
    assert agent_server.rate_limit_window_progress({"rateLimitType": "five_hour"}) is None


def test_window_progress_none_when_rate_limit_type_unrecognised(agent_server):
    now = time.time()
    info = {"resetsAt": now + 100, "rateLimitType": "some_new_window_type"}
    assert agent_server.rate_limit_window_progress(info, now=now) is None


def test_window_progress_none_when_already_past(agent_server):
    """A resetsAt already in the past must render as 'unknown', not as
    100% or 0% — the window is over, the next event describes the new
    one."""
    now = time.time()
    info = {"resetsAt": now - 10, "rateLimitType": "five_hour"}
    assert agent_server.rate_limit_window_progress(info, now=now) is None


def test_window_progress_zero_at_start_of_window(agent_server):
    now = time.time()
    window = agent_server.RATE_LIMIT_WINDOW_SECONDS["five_hour"]
    info = {"resetsAt": now + window, "rateLimitType": "five_hour"}
    assert agent_server.rate_limit_window_progress(info, now=now) == 0.0


def test_window_progress_advances_toward_one_near_reset(agent_server):
    now = time.time()
    window = agent_server.RATE_LIMIT_WINDOW_SECONDS["five_hour"]
    info = {"resetsAt": now + (window * 0.1), "rateLimitType": "five_hour"}
    progress = agent_server.rate_limit_window_progress(info, now=now)
    assert progress == pytest.approx(0.9, abs=0.01)


def test_window_progress_seven_day_uses_its_own_window_length(agent_server):
    now = time.time()
    window = agent_server.RATE_LIMIT_WINDOW_SECONDS["seven_day"]
    info = {"resetsAt": now + (window * 0.75), "rateLimitType": "seven_day"}
    progress = agent_server.rate_limit_window_progress(info, now=now)
    assert progress == pytest.approx(0.25, abs=0.01)


# ---------------------------------------------------------------------------
# is_rate_limit_warning — the window-progress backstop specifically
# (status/utilization triggers are already covered by
# test_rate_limit_pause.py; these pin the addition on top of that)
# ---------------------------------------------------------------------------

def test_warning_fires_on_window_progress_alone(agent_server):
    """The case Amos's instance hits: no utilization field at all, status
    still 'allowed', but the window is almost over. Must still warn."""
    now = time.time()
    window = agent_server.RATE_LIMIT_WINDOW_SECONDS["five_hour"]
    agent_server.agent_rate_limits["TestAgent"] = {
        "status": "allowed",
        "rateLimitType": "five_hour",
        "resetsAt": now + (window * 0.15),  # 85% through
        # no "utilization" key at all
    }
    assert agent_server.is_rate_limit_warning("TestAgent") is True
    # And still must not hard-pause — window progress is not the pause
    # criterion, only status==rejected / utilization >=97% are.
    assert agent_server.is_rate_limit_paused("TestAgent") is False


def test_no_warning_below_window_progress_threshold(agent_server):
    now = time.time()
    window = agent_server.RATE_LIMIT_WINDOW_SECONDS["five_hour"]
    agent_server.agent_rate_limits["TestAgent"] = {
        "status": "allowed",
        "rateLimitType": "five_hour",
        "resetsAt": now + (window * 0.5),  # 50% through
    }
    assert agent_server.is_rate_limit_warning("TestAgent") is False


def test_window_progress_does_not_override_utilization_result(agent_server):
    """Sabotage check: a low window progress must not suppress a warning
    that utilization alone already earns."""
    now = time.time()
    window = agent_server.RATE_LIMIT_WINDOW_SECONDS["five_hour"]
    agent_server.agent_rate_limits["TestAgent"] = {
        "status": "allowed",
        "rateLimitType": "five_hour",
        "resetsAt": now + (window * 0.9),  # only 10% through
        "utilization": 0.95,
    }
    assert agent_server.is_rate_limit_warning("TestAgent") is True


# ---------------------------------------------------------------------------
# format_usage_report
# ---------------------------------------------------------------------------

def test_usage_report_no_reading_yet(agent_server):
    agent_server.agent_rate_limits.pop("Ghost", None)
    report = agent_server.format_usage_report("Ghost")
    assert "No rate-limit reading yet" in report


def test_usage_report_includes_status_and_window_position(agent_server):
    now = time.time()
    window = agent_server.RATE_LIMIT_WINDOW_SECONDS["five_hour"]
    agent_server.agent_rate_limits["TestAgent"] = {
        "status": "allowed",
        "rateLimitType": "five_hour",
        "resetsAt": now + (window * 0.4),
    }
    report = agent_server.format_usage_report("TestAgent", now=now)
    assert "status `allowed`" in report
    assert "60%" in report  # 60% through
    assert "resets in" in report


def test_usage_report_unknown_window_position_not_rendered_as_zero(agent_server):
    """The invariant upstream's PR specifically calls out: 'no reading' and
    '0% consumed' are opposite answers and must never render the same."""
    agent_server.agent_rate_limits["TestAgent"] = {
        "status": "allowed",
        # no resetsAt / rateLimitType at all
    }
    report = agent_server.format_usage_report("TestAgent")
    assert "window position unknown" in report
    assert "0%" not in report


def test_usage_report_mentions_overage(agent_server):
    agent_server.agent_rate_limits["TestAgent"] = {
        "status": "allowed_warning",
        "isUsingOverage": True,
    }
    report = agent_server.format_usage_report("TestAgent")
    assert "overage" in report.lower()


def test_usage_report_includes_utilization_when_present(agent_server):
    agent_server.agent_rate_limits["TestAgent"] = {
        "status": "allowed",
        "utilization": 0.42,
    }
    report = agent_server.format_usage_report("TestAgent")
    assert "42%" in report


# ---------------------------------------------------------------------------
# Structural checks — route registration and relay wiring, matching this
# suite's existing convention (test_agent_server_routes.py) for anything
# that needs the event loop / sqlite / a real Discord client.
# ---------------------------------------------------------------------------

def test_usage_route_registered():
    from conftest import PACKAGE_ROOT
    src = (PACKAGE_ROOT / "bin" / "agent-server.py").read_text()
    assert 'app.router.add_get("/usage", handle_usage)' in src


def test_relay_sys_usage_command_wired():
    from conftest import PACKAGE_ROOT
    src = (PACKAGE_ROOT / "bin" / "relay.py").read_text()
    assert 'cmd == "usage"' in src
    assert "/usage" in src
