"""
Tests for bin/capture.py — Message persistence to JSONL.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import import_script, PACKAGE_ROOT


class TestCaptureMessage:
    """Test the capture_message function."""

    def test_creates_jsonl_file(self, tmp_workspace, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        capture = import_script("capture")

        msg = {
            "ts": "2026-04-10T12:00:00Z",
            "channel": "general",
            "author": "testuser",
            "content": "Hello world",
            "message_id": "msg-001",
        }
        capture.capture_message(msg)

        log_file = tmp_workspace / "data" / "messages" / "messages-2026-04-10.jsonl"
        assert log_file.exists(), "JSONL file not created"

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["channel_name"] == "general"
        assert entry["author_name"] == "testuser"
        assert entry["content"] == "Hello world"
        assert entry["message_id"] == "msg-001"

    def test_appends_multiple_messages(self, tmp_workspace, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        capture = import_script("capture")

        for i in range(5):
            capture.capture_message({
                "ts": "2026-04-10T12:00:00Z",
                "channel": "general",
                "author": f"user{i}",
                "content": f"Message {i}",
                "message_id": f"msg-{i:03d}",
            })

        log_file = tmp_workspace / "data" / "messages" / "messages-2026-04-10.jsonl"
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 5

    def test_generates_timestamp_if_missing(self, tmp_workspace, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        capture = import_script("capture")

        capture.capture_message({
            "channel": "general",
            "author": "testuser",
            "content": "No timestamp",
            "message_id": "msg-no-ts",
        })

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = tmp_workspace / "data" / "messages" / f"messages-{today}.jsonl"
        assert log_file.exists()

        entry = json.loads(log_file.read_text().strip())
        assert entry["ts"]

    def test_separates_by_date(self, tmp_workspace, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        capture = import_script("capture")

        capture.capture_message({
            "ts": "2026-04-09T23:59:00Z",
            "channel": "general",
            "author": "user",
            "content": "Yesterday",
            "message_id": "msg-yesterday",
        })
        capture.capture_message({
            "ts": "2026-04-10T00:01:00Z",
            "channel": "general",
            "author": "user",
            "content": "Today",
            "message_id": "msg-today",
        })

        assert (tmp_workspace / "data" / "messages" / "messages-2026-04-09.jsonl").exists()
        assert (tmp_workspace / "data" / "messages" / "messages-2026-04-10.jsonl").exists()

    def test_handles_missing_fields_gracefully(self, tmp_workspace, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        capture = import_script("capture")

        capture.capture_message({"ts": "2026-04-10T12:00:00Z"})

        log_file = tmp_workspace / "data" / "messages" / "messages-2026-04-10.jsonl"
        entry = json.loads(log_file.read_text().strip())
        assert entry["channel_name"] == ""
        assert entry["author_name"] == ""
        assert entry["content"] == ""
        assert entry["is_bot"] is False
