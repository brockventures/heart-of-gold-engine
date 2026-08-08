"""recovery-agent.py — deterministic ops watchdog.

Every operational incident so far got fixed by someone getting shell
access and hand-diagnosing it. This is the in-band, attributable
replacement for the two narrowest, most reversible cases: duplicate
supervised processes, and a message stuck STATUS_IN_PROGRESS past a
generous timeout. Dry-run is the default until playbooks prove clean, so
these tests cover both the detection logic and the dry-run/live branch
of remediation.
"""

import sqlite3
import time
from datetime import datetime, timezone

import pytest

from conftest import import_script

ra = import_script("recovery-agent")


# ---------------------------------------------------------------------------
# check_duplicate_processes
# ---------------------------------------------------------------------------

def test_no_duplicates_no_findings():
    procs = [
        {"pid": 1, "ppid": None, "argv": ["python3", "/workspace/bin/agent-server.py"]},
        {"pid": 2, "ppid": None, "argv": ["python3", "/workspace/bin/relay.py"]},
        {"pid": 3, "ppid": None, "argv": ["python3", "/workspace/bin/scheduler.py"]},
    ]
    assert ra.check_duplicate_processes(procs) == []


def test_two_matches_is_a_duplicate_finding():
    procs = [
        {"pid": 1, "ppid": 10, "argv": ["python3", "/workspace/bin/scheduler.py"]},
        {"pid": 2, "ppid": 999, "argv": ["python3", "/workspace/bin/scheduler.py"]},
    ]
    findings = ra.check_duplicate_processes(procs)
    assert len(findings) == 1
    assert findings[0].signature == "duplicate_process"
    assert set(findings[0].context["pids"]) == {1, 2}


def test_unrelated_process_ignored():
    procs = [{"pid": 1, "ppid": None, "argv": ["python3", "/workspace/bin/other-thing.py"]}]
    assert ra.check_duplicate_processes(procs) == []


def test_path_mentioned_in_unrelated_argument_is_not_a_match():
    """Real bug caught during live testing: a `claude -p` subprocess
    passes its entire system prompt as one argv element, which can
    contain a watched file path as plain prose without that process
    being remotely a duplicate supervised instance. Matching must be
    structural (interpreter + script-path tokens), not a substring scan
    of the whole command line."""
    procs = [
        {"pid": 1, "ppid": None, "argv": ["python3", "/workspace/bin/relay.py"]},
        {
            "pid": 2,
            "ppid": None,
            "argv": [
                "claude", "-p", "--system-prompt",
                "... discussion of bin/relay.py and bin/scheduler.py ...",
            ],
        },
    ]
    findings = ra.check_duplicate_processes(procs)
    assert findings == []


# ---------------------------------------------------------------------------
# check_wedged_subprocess
# ---------------------------------------------------------------------------

def _make_db(tmp_path, rows):
    """rows: list of (agent, id, processed, processing_started_at)"""
    db_path = tmp_path / "agent-server.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE message_queue (
            id INTEGER PRIMARY KEY, agent TEXT, processed INTEGER,
            processing_started_at TIMESTAMP
        )
    """)
    conn.executemany(
        "INSERT INTO message_queue (agent, id, processed, processing_started_at) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def test_recent_in_progress_is_not_wedged(tmp_path):
    now = time.time()
    recent = datetime.fromtimestamp(now - 60, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db_path = _make_db(tmp_path, [("Marvin", 1, ra.STATUS_IN_PROGRESS, recent)])
    assert ra.check_wedged_subprocess(db_path=db_path, now=now) == []


def test_old_in_progress_is_wedged(tmp_path):
    now = time.time()
    old = datetime.fromtimestamp(now - 30 * 60, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db_path = _make_db(tmp_path, [("Marvin", 1, ra.STATUS_IN_PROGRESS, old)])
    findings = ra.check_wedged_subprocess(db_path=db_path, now=now)
    assert len(findings) == 1
    assert findings[0].signature == "wedged_subprocess"
    assert findings[0].context["agent"] == "Marvin"


def test_completed_rows_never_flagged(tmp_path):
    now = time.time()
    old = datetime.fromtimestamp(now - 3 * 3600, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    db_path = _make_db(tmp_path, [("Marvin", 1, 2, old)])  # STATUS_COMPLETE = 2
    assert ra.check_wedged_subprocess(db_path=db_path, now=now) == []


def test_missing_db_returns_no_findings(tmp_path):
    assert ra.check_wedged_subprocess(db_path=tmp_path / "nope.db", now=time.time()) == []


# ---------------------------------------------------------------------------
# remediate_duplicate_process
# ---------------------------------------------------------------------------

def test_dry_run_never_kills(monkeypatch):
    killed = []
    monkeypatch.setattr(ra.os, "kill", lambda pid, sig: killed.append(pid))
    finding = ra.Finding(signature="duplicate_process", detail="x", context={"pids": [1, 2]})
    action = ra.remediate_duplicate_process(finding, dry_run=True, procs=[])
    assert action.dry_run is True
    assert action.action == "would_kill_orphans"
    assert killed == []


def test_live_keeps_supervisord_child_kills_others(monkeypatch):
    killed = []
    monkeypatch.setattr(ra.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(ra, "supervisord_pid", lambda: 999)
    procs = [
        {"pid": 1, "ppid": 999, "cmdline": "..."},  # the real one
        {"pid": 2, "ppid": 1, "cmdline": "..."},     # orphaned duplicate
    ]
    finding = ra.Finding(signature="duplicate_process", detail="x", context={"pids": [1, 2]})
    action = ra.remediate_duplicate_process(finding, dry_run=False, procs=procs)
    assert action.action == "killed_orphans"
    assert killed == [2]


def test_ambiguous_large_duplicate_set_escalates_instead_of_guessing(monkeypatch):
    killed = []
    monkeypatch.setattr(ra.os, "kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(ra, "supervisord_pid", lambda: None)
    finding = ra.Finding(
        signature="duplicate_process", detail="x", context={"pids": [1, 2, 3, 4]}
    )
    action = ra.remediate_duplicate_process(finding, dry_run=False, procs=[])
    assert action.action == "escalate"
    assert killed == []


# ---------------------------------------------------------------------------
# remediate_wedged_subprocess
# ---------------------------------------------------------------------------

def test_wedged_dry_run_does_not_shell_out(monkeypatch):
    calls = []
    monkeypatch.setattr(ra.subprocess, "run", lambda *a, **k: calls.append(a))
    finding = ra.Finding(signature="wedged_subprocess", detail="x", context={"agent": "Marvin"})
    action = ra.remediate_wedged_subprocess(finding, dry_run=True)
    assert action.dry_run is True
    assert action.action == "would_restart"
    assert calls == []


def test_wedged_live_shells_out_to_safe_pkill(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        return ra.subprocess.CompletedProcess(cmd, 0, stdout="safe-pkill: sending SIGTERM", stderr="")

    monkeypatch.setattr(ra.subprocess, "run", fake_run)
    finding = ra.Finding(signature="wedged_subprocess", detail="x", context={"agent": "relay"})
    action = ra.remediate_wedged_subprocess(finding, dry_run=False)
    assert action.action == "restarted"
    assert str(ra.SAFE_PKILL) in calls[0]
    assert "bin/relay.py" in calls[0]


def test_wedged_unknown_agent_escalates():
    finding = ra.Finding(signature="wedged_subprocess", detail="x", context={"agent": "mystery"})
    action = ra.remediate_wedged_subprocess(finding, dry_run=True)
    assert action.action == "escalate"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def test_rate_limit_not_exceeded_under_threshold():
    state = {}
    now = time.time()
    for _ in range(ra.MAX_ACTIONS_PER_HOUR - 1):
        assert not ra.rate_limit_exceeded("sig", state, now=now)
        ra.record_action("sig", state, now=now)
    assert not ra.rate_limit_exceeded("sig", state, now=now)


def test_rate_limit_exceeded_at_threshold():
    state = {}
    now = time.time()
    for _ in range(ra.MAX_ACTIONS_PER_HOUR):
        ra.record_action("sig", state, now=now)
    assert ra.rate_limit_exceeded("sig", state, now=now)


def test_rate_limit_window_expires_after_an_hour():
    state = {"sig": [time.time() - 3700]}  # just over an hour ago
    assert not ra.rate_limit_exceeded("sig", state, now=time.time())


# ---------------------------------------------------------------------------
# run_sweep integration (findings -> playbooks -> actions), with all
# side effects (killing, shelling out, posting) mocked.
# ---------------------------------------------------------------------------

def test_run_sweep_unmatched_signature_goes_to_proposals(tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "list_processes", lambda: [])
    monkeypatch.setattr(ra, "check_wedged_subprocess", lambda: [
        ra.Finding(signature="totally_new_shape", detail="x", context={})
    ])
    proposals_calls = []
    monkeypatch.setattr(ra, "log_and_propose", lambda finding: proposals_calls.append(finding))
    posts = []
    monkeypatch.setattr(ra, "post_to_signals", lambda text: posts.append(text))

    actions = ra.run_sweep(dry_run=True, rate_state={})
    assert actions == []
    assert len(proposals_calls) == 1
    assert posts == []


def test_run_sweep_known_signature_dry_run_posts_to_signals(monkeypatch):
    monkeypatch.setattr(ra, "list_processes", lambda: [])
    monkeypatch.setattr(ra, "check_wedged_subprocess", lambda: [
        ra.Finding(signature="wedged_subprocess", detail="Marvin stuck", context={"agent": "Marvin"})
    ])
    posts = []
    monkeypatch.setattr(ra, "post_to_signals", lambda text: posts.append(text))

    actions = ra.run_sweep(dry_run=True, rate_state={})
    assert len(actions) == 1
    assert actions[0].dry_run is True
    assert "[DRY RUN]" in posts[0]


def test_run_sweep_rate_limited_escalates_without_acting(monkeypatch):
    monkeypatch.setattr(ra, "list_processes", lambda: [])
    monkeypatch.setattr(ra, "check_wedged_subprocess", lambda: [
        ra.Finding(signature="wedged_subprocess", detail="Marvin stuck", context={"agent": "Marvin"})
    ])
    posts = []
    monkeypatch.setattr(ra, "post_to_signals", lambda text: posts.append(text))
    already_at_limit = {"wedged_subprocess": [time.time()] * ra.MAX_ACTIONS_PER_HOUR}

    actions = ra.run_sweep(dry_run=True, rate_state=already_at_limit)
    assert actions == []
    assert len(posts) == 1
    assert "rate-limited" in posts[0]
