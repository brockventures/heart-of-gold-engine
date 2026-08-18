"""
Tests for skills/outbox/scripts/queue_outbox_message.py — the MCP tool
wrapper around bin/outbox.py's add_pending(), added 2026-08-18 to fix
"jumbling" (a turn blending content for two channels/audiences into one
in-turn reply instead of splitting them).

This exercises the wrapper script directly (subprocess, same as the real
MCP dispatch path in mcp/tools-server.py: TOOL_ARGS + WORKSPACE_ROOT via
env, JSON on stdout, exit code signals success/failure) rather than the
underlying add_pending()/pending.jsonl mechanics, which are already
covered by tests/test_outbox.py.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent
SCRIPT = PACKAGE_ROOT / "skills" / "outbox" / "scripts" / "queue_outbox_message.py"


def run_tool(tmp_path, args):
    env = os.environ.copy()
    env["WORKSPACE_ROOT"] = str(tmp_path)
    env["TOOL_ARGS"] = json.dumps(args)
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, env=env, timeout=10,
    )
    return result


def test_queues_valid_message(tmp_path):
    result = run_tool(tmp_path, {"channel": "general", "content": "hello from another channel"})
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "queued"
    assert payload["channel"] == "general"
    assert "id" in payload

    pending = tmp_path / "data" / "outbox" / "pending.jsonl"
    rows = [json.loads(line) for line in pending.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["channel"] == "general"
    assert rows[0]["content"] == "hello from another channel"
    assert rows[0]["delivered_at"] is None


def test_two_calls_append_two_rows(tmp_path):
    run_tool(tmp_path, {"channel": "general", "content": "first"})
    run_tool(tmp_path, {"channel": "agent-chat", "content": "second"})

    pending = tmp_path / "data" / "outbox" / "pending.jsonl"
    rows = [json.loads(line) for line in pending.read_text().splitlines()]
    assert [r["channel"] for r in rows] == ["general", "agent-chat"]
    assert [r["content"] for r in rows] == ["first", "second"]


@pytest.mark.parametrize("bad_channel", ["nonsense", "", "General", "#general", None])
def test_rejects_invalid_channel(tmp_path, bad_channel):
    result = run_tool(tmp_path, {"channel": bad_channel, "content": "x"})
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert "error" in payload

    pending = tmp_path / "data" / "outbox" / "pending.jsonl"
    assert not pending.exists()


@pytest.mark.parametrize("bad_content", ["", "   ", None])
def test_rejects_empty_content(tmp_path, bad_content):
    args = {"channel": "general"}
    if bad_content is not None:
        args["content"] = bad_content
    result = run_tool(tmp_path, args)
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert "error" in payload


def test_rejects_oversize_content(tmp_path):
    result = run_tool(tmp_path, {"channel": "general", "content": "x" * 4001})
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert "error" in payload


def test_accepts_content_at_max_length(tmp_path):
    result = run_tool(tmp_path, {"channel": "general", "content": "x" * 4000})
    assert result.returncode == 0


@pytest.mark.parametrize(
    "channel", ["general", "signals", "staff-comms", "agent-chat", "lounge"]
)
def test_all_known_channels_accepted(tmp_path, channel):
    result = run_tool(tmp_path, {"channel": channel, "content": "x"})
    assert result.returncode == 0
