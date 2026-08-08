"""outbox.py — durable queue for cross-channel relays.

Every turn is pre-scoped to one Discord channel. Without a durable queue,
"Marvin owes #general a message" was a mental note that evaporated at
end-of-turn if the next turn wasn't scoped there. These tests exercise the
queue/flush cycle against a temp file, with discord-notify.sh delivery
mocked out (no real network calls).
"""

import json
import subprocess

import pytest

from conftest import import_script


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    mod = import_script("outbox")
    mod.OUTBOX_PATH = tmp_path / "data" / "outbox" / "pending.jsonl"
    return mod


def test_add_pending_creates_row(outbox):
    row_id = outbox.add_pending("general", "hello")
    rows = outbox._load_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == row_id
    assert rows[0]["channel"] == "general"
    assert rows[0]["content"] == "hello"
    assert rows[0]["delivered_at"] is None


def test_add_pending_appends_not_overwrites(outbox):
    outbox.add_pending("general", "first")
    outbox.add_pending("signals", "second")
    rows = outbox._load_rows()
    assert len(rows) == 2
    assert [r["content"] for r in rows] == ["first", "second"]


def test_flush_pending_delivers_and_marks(outbox, monkeypatch):
    calls = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="Posted", stderr="")

    monkeypatch.setattr(outbox.subprocess, "run", fake_run)

    outbox.add_pending("general", "queued while scoped elsewhere")
    delivered = outbox.flush_pending()

    assert len(delivered) == 1
    assert calls[0][1:] == [str(outbox.NOTIFY_SCRIPT), "general", "queued while scoped elsewhere"] or \
        calls[0] == [str(outbox.NOTIFY_SCRIPT), "general", "queued while scoped elsewhere"]

    rows = outbox._load_rows()
    assert rows[0]["delivered_at"] is not None


def test_flush_pending_skips_already_delivered(outbox, monkeypatch):
    calls = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="Posted", stderr="")

    monkeypatch.setattr(outbox.subprocess, "run", fake_run)

    outbox.add_pending("general", "one")
    outbox.flush_pending()
    assert len(calls) == 1

    # Second flush with nothing new pending should make no delivery calls.
    delivered = outbox.flush_pending()
    assert delivered == []
    assert len(calls) == 1


def test_flush_pending_leaves_failed_rows_undelivered(outbox, monkeypatch):
    def fake_run(cmd, check, capture_output, text):
        raise subprocess.CalledProcessError(1, cmd, stderr="network down")

    monkeypatch.setattr(outbox.subprocess, "run", fake_run)

    outbox.add_pending("general", "will fail")
    delivered = outbox.flush_pending()

    assert delivered == []
    rows = outbox._load_rows()
    assert rows[0]["delivered_at"] is None


def test_flush_pending_retries_after_transient_failure(outbox, monkeypatch):
    state = {"calls": 0}

    def flaky_run(cmd, check, capture_output, text):
        state["calls"] += 1
        if state["calls"] == 1:
            raise subprocess.CalledProcessError(1, cmd, stderr="network down")
        return subprocess.CompletedProcess(cmd, 0, stdout="Posted", stderr="")

    monkeypatch.setattr(outbox.subprocess, "run", flaky_run)

    outbox.add_pending("general", "retry me")
    first = outbox.flush_pending()
    assert first == []

    second = outbox.flush_pending()
    assert len(second) == 1
    rows = outbox._load_rows()
    assert rows[0]["delivered_at"] is not None


def test_multiple_rows_only_undelivered_are_flushed(outbox, monkeypatch):
    calls = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd[1])  # channel arg
        return subprocess.CompletedProcess(cmd, 0, stdout="Posted", stderr="")

    monkeypatch.setattr(outbox.subprocess, "run", fake_run)

    outbox.add_pending("general", "a")
    outbox.flush_pending()
    outbox.add_pending("signals", "b")
    outbox.flush_pending()

    assert calls == ["general", "signals"]
