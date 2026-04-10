"""
Tests for system/check-protected-paths.py — Git pre-commit protection.
"""

import json
import os
from pathlib import Path

import pytest

from conftest import import_script, PACKAGE_ROOT


class TestTier1Protection:
    """Verify Tier 1 paths are blocked from commit."""

    def _make_checker(self, tmp_workspace, protected_paths_config, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        return import_script("check-protected-paths")

    def test_blocks_system_directory(self, tmp_workspace, protected_paths_config, monkeypatch):
        cpp = self._make_checker(tmp_workspace, protected_paths_config, monkeypatch)
        assert cpp.check_tier1("system/some-file.py", protected_paths_config["tier1_protected"])

    def test_blocks_config_directory(self, tmp_workspace, protected_paths_config, monkeypatch):
        cpp = self._make_checker(tmp_workspace, protected_paths_config, monkeypatch)
        assert cpp.check_tier1("config/agents.json", protected_paths_config["tier1_protected"])

    def test_blocks_specific_files(self, tmp_workspace, protected_paths_config, monkeypatch):
        cpp = self._make_checker(tmp_workspace, protected_paths_config, monkeypatch)
        assert cpp.check_tier1("bin/agent-server.py", protected_paths_config["tier1_protected"])
        assert cpp.check_tier1("bin/relay.py", protected_paths_config["tier1_protected"])
        assert cpp.check_tier1("Dockerfile", protected_paths_config["tier1_protected"])

    def test_allows_non_protected_files(self, tmp_workspace, protected_paths_config, monkeypatch):
        cpp = self._make_checker(tmp_workspace, protected_paths_config, monkeypatch)
        assert not cpp.check_tier1("README.md", protected_paths_config["tier1_protected"])
        assert not cpp.check_tier1("docs/QUICKSTART.md", protected_paths_config["tier1_protected"])


class TestTier2ReviewRequired:
    """Verify Tier 2 paths are flagged for review."""

    def _make_checker(self, tmp_workspace, protected_paths_config, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        return import_script("check-protected-paths")

    def test_flags_bin_directory(self, tmp_workspace, protected_paths_config, monkeypatch):
        cpp = self._make_checker(tmp_workspace, protected_paths_config, monkeypatch)
        assert cpp.check_tier2("bin/heartbeat.sh", protected_paths_config["tier2_review_required"])

    def test_flags_agent_templates(self, tmp_workspace, protected_paths_config, monkeypatch):
        cpp = self._make_checker(tmp_workspace, protected_paths_config, monkeypatch)
        assert cpp.check_tier2("agents/templates/primary.md", protected_paths_config["tier2_review_required"])

    def test_allows_unprotected_paths(self, tmp_workspace, protected_paths_config, monkeypatch):
        cpp = self._make_checker(tmp_workspace, protected_paths_config, monkeypatch)
        assert not cpp.check_tier2("README.md", protected_paths_config["tier2_review_required"])


class TestOverrides:
    """Verify unprotected overrides bypass protection."""

    def _make_checker(self, tmp_workspace, protected_paths_config, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        return import_script("check-protected-paths")

    def test_persona_files_are_unprotected(self, tmp_workspace, protected_paths_config, monkeypatch):
        cpp = self._make_checker(tmp_workspace, protected_paths_config, monkeypatch)
        overrides = protected_paths_config["unprotected_overrides"]
        assert cpp.is_override("agents/test-agent/persona/voice.md", overrides)

    def test_journal_files_are_unprotected(self, tmp_workspace, protected_paths_config, monkeypatch):
        cpp = self._make_checker(tmp_workspace, protected_paths_config, monkeypatch)
        overrides = protected_paths_config["unprotected_overrides"]
        assert cpp.is_override("agents/test-agent/journal/2026-04-10.md", overrides)

    def test_non_override_paths_are_not_bypassed(self, tmp_workspace, protected_paths_config, monkeypatch):
        cpp = self._make_checker(tmp_workspace, protected_paths_config, monkeypatch)
        overrides = protected_paths_config["unprotected_overrides"]
        assert not cpp.is_override("bin/agent-server.py", overrides)
        assert not cpp.is_override("config/agents.json", overrides)
