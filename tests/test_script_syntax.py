"""
Syntax-only checks (bash -n / py_compile) across the repo's scripts.
Split out of tests/test_setup.py on 2026-08-18 when that file's
setup.sh-wizard-specific tests were deleted — these two classes were
never actually about setup.sh or Docker, just parked in the same file.
"""

import subprocess
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent


class TestShellSyntax:
    """Verify shell scripts have valid syntax."""

    @pytest.mark.parametrize("script", [
        "setup.sh",
        "install.sh",
        "bin/entrypoint.sh",
        "bin/poke.sh",
        "bin/heartbeat.sh",
        "bin/create-agent.sh",
        "bin/preflight.sh",
        "native/start.sh",
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
