"""
Tests for _write_mechanical_status() in bin/agent-server.py — the automatic
idle/busy-per-turn presence layer added 2026-08-18, stolen from Amos's
relay per #agent-chat (his mechanical read side flips presence per turn
state and tags batch work in the activity text; ours only ever moved via
the intentional set_status tool before this).

Like test_rate_limit_pause.py, this imports the real module rather than
parsing source — _write_mechanical_status() is pure file I/O, no event
loop/sqlite/subprocess involved, so importing is safe.

Key invariant under test: a manual set_status() declaration (source
"manual", state idle/dnd — an active "going dark"/checkpoint stretch)
must never be overwritten by the mechanical layer, but a manual "online"
(the documented clear/relinquish step) hands control back.
"""

import json

import pytest

from conftest import import_script


@pytest.fixture
def agent_server(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    return import_script("agent-server")


def _read_status(agent_server):
    with open(agent_server.STATUS_FILE) as f:
        return json.load(f)


def test_writes_when_no_file_exists(agent_server):
    agent_server._write_mechanical_status("Marvin", "replying in #general")
    data = _read_status(agent_server)
    assert data["state"] == "online"
    assert data["activity"] == "replying in #general"
    assert data["source"] == "auto"


def test_clears_activity_on_turn_end(agent_server):
    agent_server._write_mechanical_status("Marvin", "replying in #general")
    agent_server._write_mechanical_status("Marvin", None)
    data = _read_status(agent_server)
    assert data["activity"] is None
    assert data["source"] == "auto"


@pytest.mark.parametrize("declared_state", ["idle", "dnd"])
def test_never_overwrites_active_manual_declaration(agent_server, declared_state):
    agent_server.STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    manual = {
        "state": declared_state,
        "activity": "heads-down: presence refactor, back ~30m",
        "source": "manual",
        "updated_at": "2026-08-18T04:00:00+00:00",
    }
    with open(agent_server.STATUS_FILE, "w") as f:
        json.dump(manual, f)

    agent_server._write_mechanical_status("Marvin", "replying in #general")

    data = _read_status(agent_server)
    assert data == manual  # untouched


def test_manual_online_relinquishes_control_to_mechanical(agent_server):
    """The documented 'Done' step (set_status(state='online')) clears the
    dot AND hands control back — it's not treated as an active declaration
    the way idle/dnd are."""
    agent_server.STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    manual_clear = {
        "state": "online",
        "activity": None,
        "source": "manual",
        "updated_at": "2026-08-18T04:00:00+00:00",
    }
    with open(agent_server.STATUS_FILE, "w") as f:
        json.dump(manual_clear, f)

    agent_server._write_mechanical_status("Marvin", "replying in #general")

    data = _read_status(agent_server)
    assert data["source"] == "auto"
    assert data["activity"] == "replying in #general"


def test_noop_for_other_agents(agent_server):
    """Only Marvin has a presence file today (STATUS_FILE is single-agent,
    matching relay.py) — must not create one for e.g. 'relay'."""
    agent_server._write_mechanical_status("relay", "replying in #general")
    assert not agent_server.STATUS_FILE.exists()


def test_activity_truncated_to_128_chars(agent_server):
    long_activity = "x" * 200
    agent_server._write_mechanical_status("Marvin", long_activity)
    data = _read_status(agent_server)
    assert len(data["activity"]) == 128


def test_corrupt_file_is_overwritten_cleanly(agent_server):
    agent_server.STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    agent_server.STATUS_FILE.write_text("{not valid json")

    agent_server._write_mechanical_status("Marvin", "replying in #general")

    data = _read_status(agent_server)
    assert data["source"] == "auto"
    assert data["activity"] == "replying in #general"
