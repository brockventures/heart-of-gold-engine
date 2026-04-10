"""
Tests for setup.sh — Installation wizard validation.

These tests verify the setup script's structure and behavior without
running the interactive wizard. They catch issues like missing function
definitions, broken prerequisite checks, and template errors.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent
SETUP_SCRIPT = PACKAGE_ROOT / "setup.sh"


class TestSetupScriptStructure:
    """Validate setup.sh has required components."""

    @pytest.fixture(autouse=True)
    def load_script(self):
        self.content = SETUP_SCRIPT.read_text()

    def test_has_shebang(self):
        assert self.content.startswith("#!/"), "Missing shebang line"

    def test_has_set_e(self):
        """Script should fail fast on errors."""
        assert "set -e" in self.content or "set -eo" in self.content, (
            "setup.sh should use 'set -e' for fail-fast behavior"
        )

    def test_has_prerequisites_check(self):
        """Must verify Docker, jq, etc. are installed."""
        assert "docker" in self.content.lower()
        # Check for some form of prerequisite validation
        assert any(
            term in self.content
            for term in ["check_prerequisites", "command -v docker", "which docker"]
        ), "No Docker prerequisite check found"

    def test_has_state_persistence(self):
        """Wizard should save/resume state."""
        assert ".setup-state.json" in self.content or "STATE_FILE" in self.content, (
            "No state persistence mechanism found"
        )

    def test_has_env_file_creation(self):
        """Must create .env with proper permissions."""
        assert ".env" in self.content
        assert "chmod" in self.content, "No chmod call for .env security"

    def test_has_error_trap(self):
        """Should trap errors for debugging."""
        assert "trap" in self.content, "No error trap found"

    def test_no_hardcoded_api_keys(self):
        """No API keys should be hardcoded in the script."""
        # Look for patterns like sk-ant-, sk_test_, etc.
        key_patterns = [
            r'sk-ant-[a-zA-Z0-9]+',
            r'sk_test_[a-zA-Z0-9]+',
            r'xoxb-[0-9]+-[a-zA-Z0-9]+',
        ]
        for pattern in key_patterns:
            matches = re.findall(pattern, self.content)
            assert not matches, f"Hardcoded key found: {matches[0][:20]}..."


class TestEnvTemplate:
    """Validate .env.template has required variables."""

    @pytest.fixture(autouse=True)
    def load_template(self):
        self.content = (PACKAGE_ROOT / "config" / ".env.template").read_text()

    def test_has_required_vars(self):
        required = [
            "AGENT_SERVER_TOKEN",
            "OWNER_DISCORD_ID",
        ]
        for var in required:
            assert var in self.content, f"Missing required env var: {var}"

    def test_has_cost_limits(self):
        assert "COST_DAILY_LIMIT" in self.content
        assert "COST_MONTHLY_LIMIT" in self.content

    def test_no_filled_secrets(self):
        """Template should have placeholder values, not real secrets."""
        lines = self.content.strip().split("\n")
        for line in lines:
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                # Strip inline comments
                if "#" in value:
                    value = value[:value.index("#")]
                value = value.strip().strip('"').strip("'")
                # Values should be empty, placeholders, or example-looking
                if key.strip() in ("AGENT_SERVER_TOKEN", "SESSION_SECRET"):
                    assert not value or "..." in value or value.startswith("$") or value.startswith("<"), (
                        f"Template has non-placeholder value for {key.strip()}"
                    )


class TestShellSyntax:
    """Verify shell scripts have valid syntax."""

    @pytest.mark.parametrize("script", [
        "setup.sh",
        "install.sh",
        "bin/entrypoint.sh",
        "bin/poke.sh",
        "bin/heartbeat.sh",
        "bin/create-agent.sh",
    ])
    def test_shell_syntax_valid(self, script):
        """bash -n checks syntax without executing."""
        script_path = PACKAGE_ROOT / script
        if not script_path.exists():
            pytest.skip(f"{script} not found")

        result = subprocess.run(
            ["bash", "-n", str(script_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"{script} has syntax errors:\n{result.stderr}"
        )


class TestPythonSyntax:
    """Verify Python scripts have valid syntax."""

    @pytest.mark.parametrize("script", [
        "bin/agent-server.py",
        "bin/relay.py",
        "bin/scheduler.py",
        "bin/capture.py",
        "bin/health-monitor.py",
        "bin/memory-maintenance.py",
        "bin/purge-data.py",
        "bin/summarize-session.py",
        "mcp/tools-server.py",
        "system/check-protected-paths.py",
    ])
    def test_python_syntax_valid(self, script):
        """py_compile checks syntax without executing."""
        script_path = PACKAGE_ROOT / script
        if not script_path.exists():
            pytest.skip(f"{script} not found")

        result = subprocess.run(
            ["python3", "-m", "py_compile", str(script_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"{script} has syntax errors:\n{result.stderr}"
        )
