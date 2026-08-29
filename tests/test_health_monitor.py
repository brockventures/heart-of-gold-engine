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


class TestGitSyncCheck:
    """Test the local-main-vs-origin/main drift check.

    Incident 2026-08-29: local main silently drifted 43 commits / 18 days
    ahead of origin/main because a GITHUB_TOKEN missing the 'workflow'
    scope made every `git push` fail, and nothing read the exit code or
    stderr. These tests exercise check_git_sync()'s command sequence
    (fetch, rev-list x2, push-if-ahead) by faking subprocess.run rather
    than touching a real remote.
    """

    def _make_monitor(self, tmp_workspace, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        return import_script("health-monitor")

    def test_in_sync_passes_without_pushing(self, tmp_workspace, monkeypatch):
        monitor = self._make_monitor(tmp_workspace, monkeypatch)
        calls = []

        class Result:
            def __init__(self, stdout=""):
                self.stdout = stdout
                self.stderr = ""

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["git", "fetch"]:
                return Result()
            if args[:3] == ["git", "rev-list", "--count"]:
                return Result(stdout="0\n")
            raise AssertionError(f"unexpected git call: {args}")

        monkeypatch.setattr(monitor.subprocess, "run", fake_run)

        healthy, reason = monitor.check_git_sync()
        assert healthy is True
        assert reason == ""
        assert not any(c[:2] == ["git", "push"] for c in calls), (
            "must not push when already in sync"
        )

    def test_ahead_pushes_and_self_heals(self, tmp_workspace, monkeypatch):
        """Same-session ahead-count >0 right after a commit is normal —
        if the push succeeds, no alert should fire."""
        monitor = self._make_monitor(tmp_workspace, monkeypatch)
        push_calls = []

        class Result:
            def __init__(self, stdout=""):
                self.stdout = stdout
                self.stderr = ""

        def fake_run(args, **kwargs):
            if args[:2] == ["git", "fetch"]:
                return Result()
            if args[:3] == ["git", "rev-list", "--count"]:
                if args[3] == "origin/main..main":
                    return Result(stdout="3\n")
                return Result(stdout="0\n")
            if args[:2] == ["git", "push"]:
                push_calls.append(args)
                return Result()
            raise AssertionError(f"unexpected git call: {args}")

        monkeypatch.setattr(monitor.subprocess, "run", fake_run)

        healthy, reason = monitor.check_git_sync()
        assert healthy is True
        assert reason == ""
        assert len(push_calls) == 1
        assert push_calls[0] == ["git", "push", "origin", "main"], (
            "must be a plain fast-forward push, never --force"
        )

    def test_ahead_with_failed_push_alerts_with_git_stderr(self, tmp_workspace, monkeypatch):
        """The actual regression: push fails because the token lacks
        'workflow' scope. The alert must surface git's real stderr so a
        human doesn't have to re-derive the diagnosis from scratch."""
        monitor = self._make_monitor(tmp_workspace, monkeypatch)

        class Result:
            def __init__(self, stdout=""):
                self.stdout = stdout
                self.stderr = ""

        workflow_error = (
            "refusing to allow a Personal Access Token to create or "
            "update workflow `.github/workflows/ci.yml` without "
            "`workflow` scope"
        )

        def fake_run(args, **kwargs):
            if args[:2] == ["git", "fetch"]:
                return Result()
            if args[:3] == ["git", "rev-list", "--count"]:
                if args[3] == "origin/main..main":
                    return Result(stdout="43\n")
                return Result(stdout="0\n")
            if args[:2] == ["git", "push"]:
                raise monitor.subprocess.CalledProcessError(
                    1, args, output="", stderr=f"! [remote rejected] main -> main ({workflow_error})"
                )
            raise AssertionError(f"unexpected git call: {args}")

        monkeypatch.setattr(monitor.subprocess, "run", fake_run)

        healthy, reason = monitor.check_git_sync()
        assert healthy is False
        assert "43 commit(s) ahead" in reason
        assert "workflow" in reason and "scope" in reason

    def test_ahead_push_failure_alerts_every_run_not_just_once(self, tmp_workspace, monkeypatch):
        """A failing push is worth flagging every time the check runs
        until it's fixed — no debouncing beyond the self-heal above."""
        monitor = self._make_monitor(tmp_workspace, monkeypatch)

        class Result:
            def __init__(self, stdout=""):
                self.stdout = stdout
                self.stderr = ""

        def fake_run(args, **kwargs):
            if args[:2] == ["git", "fetch"]:
                return Result()
            if args[:3] == ["git", "rev-list", "--count"]:
                if args[3] == "origin/main..main":
                    return Result(stdout="1\n")
                return Result(stdout="0\n")
            if args[:2] == ["git", "push"]:
                raise monitor.subprocess.CalledProcessError(1, args, output="", stderr="still broken")
            raise AssertionError(f"unexpected git call: {args}")

        monkeypatch.setattr(monitor.subprocess, "run", fake_run)

        first = monitor.check_git_sync()
        second = monitor.check_git_sync()
        assert first[0] is False
        assert second[0] is False
        assert first[1] == second[1]

    def test_behind_is_informational_and_does_not_push_or_merge(self, tmp_workspace, monkeypatch):
        monitor = self._make_monitor(tmp_workspace, monkeypatch)
        calls = []

        class Result:
            def __init__(self, stdout=""):
                self.stdout = stdout
                self.stderr = ""

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["git", "fetch"]:
                return Result()
            if args[:3] == ["git", "rev-list", "--count"]:
                if args[3] == "origin/main..main":
                    return Result(stdout="0\n")
                return Result(stdout="2\n")
            raise AssertionError(f"unexpected git call: {args}")

        monkeypatch.setattr(monitor.subprocess, "run", fake_run)

        healthy, reason = monitor.check_git_sync()
        assert healthy is False
        assert "2 commit(s) behind" in reason
        assert "informational" in reason
        assert not any(c[:2] in (["git", "push"], ["git", "merge"], ["git", "pull"]) for c in calls)

    def test_fetch_failure_does_not_raise(self, tmp_workspace, monkeypatch):
        """A network hiccup during fetch must be reported as an unhealthy
        result, not an unhandled exception that crashes the health-monitor
        run before other checks execute."""
        monitor = self._make_monitor(tmp_workspace, monkeypatch)

        def fake_run(args, **kwargs):
            if args[:2] == ["git", "fetch"]:
                raise monitor.subprocess.TimeoutExpired(cmd=args, timeout=monitor.GIT_FETCH_TIMEOUT)
            raise AssertionError(f"unexpected git call: {args}")

        monkeypatch.setattr(monitor.subprocess, "run", fake_run)

        healthy, reason = monitor.check_git_sync()
        assert healthy is False
        assert "timed out" in reason
