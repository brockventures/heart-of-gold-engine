"""reload-on-commit.py — auto-bounce relay/scheduler when a commit
changes the files that govern their behavior.

Code landing on disk isn't the same as code running: bin/relay.py picked
up the attention-marker gate override in commit fa031a9, but the running
process kept the old behavior until someone noticed and manually
restarted it. These tests exercise the pure changed-files -> bounce-plan
mapping (plan_reloads) without touching real processes or git.
"""

from conftest import import_script

reload_on_commit = import_script("reload-on-commit")


def test_relay_change_bounces_relay():
    to_bounce, warnings = reload_on_commit.plan_reloads(["bin/relay.py"])
    assert to_bounce == {"relay": "bin/relay.py"}
    assert warnings == []


def test_reply_gate_change_bounces_relay():
    """reply_gate.py has no process of its own — it's imported by
    relay.py, so a change there should bounce relay too."""
    to_bounce, warnings = reload_on_commit.plan_reloads(["bin/reply_gate.py"])
    assert to_bounce == {"relay": "bin/relay.py"}


def test_scheduler_change_bounces_scheduler():
    to_bounce, warnings = reload_on_commit.plan_reloads(["bin/scheduler.py"])
    assert to_bounce == {"scheduler": "bin/scheduler.py"}
    assert warnings == []


def test_agent_server_change_warns_but_does_not_bounce():
    """agent-server.py is very likely this script's own ancestry (a
    commit made from within Marvin's own process) — must never be an
    auto-bounce target, only a printed warning."""
    to_bounce, warnings = reload_on_commit.plan_reloads(["bin/agent-server.py"])
    assert to_bounce == {}
    assert len(warnings) == 1
    assert "agent-server.py" in warnings[0]


def test_unrelated_file_change_is_a_noop():
    to_bounce, warnings = reload_on_commit.plan_reloads(["docs/README.md", "tests/test_foo.py"])
    assert to_bounce == {}
    assert warnings == []


def test_multiple_watched_files_bounce_each_process_once():
    to_bounce, warnings = reload_on_commit.plan_reloads(
        ["bin/relay.py", "bin/reply_gate.py", "bin/scheduler.py"]
    )
    assert to_bounce == {"relay": "bin/relay.py", "scheduler": "bin/scheduler.py"}


def test_mixed_watched_and_self_process_change():
    to_bounce, warnings = reload_on_commit.plan_reloads(
        ["bin/relay.py", "bin/agent-server.py"]
    )
    assert to_bounce == {"relay": "bin/relay.py"}
    assert len(warnings) == 1


def test_empty_changeset_is_a_noop():
    to_bounce, warnings = reload_on_commit.plan_reloads([])
    assert to_bounce == {}
    assert warnings == []
