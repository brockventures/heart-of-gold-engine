"""
Tests for bin/health-monitor.py — Component health checking.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from conftest import import_script, PACKAGE_ROOT


class TestHealthFileChecks:
    """Test health file freshness detection."""

    def _make_monitor(self, tmp_workspace, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        return import_script("health-monitor")

    def test_healthy_file_passes(self, tmp_workspace, monkeypatch):
        monitor = self._make_monitor(tmp_workspace, monkeypatch)
        health_dir = tmp_workspace / "data" / "health"

        now = datetime.now().isoformat()
        (health_dir / "relay.json").write_text(json.dumps({"timestamp": now}))

        healthy, reason = monitor.check_health_file("relay.json", 300)
        assert healthy is True
        assert reason == ""

    def test_stale_file_fails(self, tmp_workspace, monkeypatch):
        monitor = self._make_monitor(tmp_workspace, monkeypatch)
        health_dir = tmp_workspace / "data" / "health"

        old = (datetime.now() - timedelta(minutes=10)).isoformat()
        (health_dir / "relay.json").write_text(json.dumps({"timestamp": old}))

        healthy, reason = monitor.check_health_file("relay.json", 300)
        assert healthy is False
        assert "stale" in reason

    def test_missing_file_fails(self, tmp_workspace, monkeypatch):
        monitor = self._make_monitor(tmp_workspace, monkeypatch)

        healthy, reason = monitor.check_health_file("nonexistent.json", 300)
        assert healthy is False
        assert "missing" in reason

    def test_empty_timestamp_fails(self, tmp_workspace, monkeypatch):
        monitor = self._make_monitor(tmp_workspace, monkeypatch)
        health_dir = tmp_workspace / "data" / "health"

        (health_dir / "relay.json").write_text(json.dumps({"timestamp": ""}))

        healthy, reason = monitor.check_health_file("relay.json", 300)
        assert healthy is False
        assert "no timestamp" in reason

    def test_malformed_json_fails(self, tmp_workspace, monkeypatch):
        monitor = self._make_monitor(tmp_workspace, monkeypatch)
        health_dir = tmp_workspace / "data" / "health"

        (health_dir / "relay.json").write_text("not json")

        healthy, reason = monitor.check_health_file("relay.json", 300)
        assert healthy is False
        assert "error" in reason

    def test_memory_has_longer_threshold(self, tmp_workspace, monkeypatch):
        """Memory maintenance only runs daily — 48h threshold."""
        monitor = self._make_monitor(tmp_workspace, monkeypatch)
        health_dir = tmp_workspace / "data" / "health"

        old = (datetime.now() - timedelta(hours=24)).isoformat()
        (health_dir / "memory.json").write_text(json.dumps({"timestamp": old}))

        healthy, _ = monitor.check_health_file("memory.json", 172800)
        assert healthy is True
