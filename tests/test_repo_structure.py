"""
Repo structure sanity — required files exist, scripts are executable.

Split out of tests/test_smoke_docker.py's TestFileStructure on 2026-08-18
when the Docker-specific tests in that file were deleted (native systemd
since 2026-08-11, see native-migration-complete-2026-08-11 in memory).
Dockerfile / config/docker-compose.yml / config/supervisord.conf were
dropped from the required-files list below — they still exist on disk
(README calls them "a stale/drifted copy," not authoritative for this
install) but nothing runs them, so their absence shouldn't fail CI.
native/start.sh and the systemd unit files were added since they're what
this install actually boots from now.
"""

import os
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent


class TestFileStructure:
    """Verify required files exist in the repository."""

    @pytest.mark.parametrize("path", [
        "config/.env.template",
        "config/protected-paths.json",
        "bin/agent-server.py",
        "bin/relay.py",
        "bin/scheduler.py",
        "bin/capture.py",
        "bin/health-monitor.py",
        "bin/memory-maintenance.py",
        "bin/purge-data.py",
        "bin/summarize-session.py",
        "bin/poke.sh",
        "bin/heartbeat.sh",
        "bin/create-agent.sh",
        "mcp/tools-server.py",
        "native/start.sh",
        "native/systemd/karakos-agent-server.service",
        "native/systemd/karakos-relay.service",
        "native/systemd/karakos-scheduler.service",
        "native/systemd/karakos-recovery-agent.service",
        "native/systemd/karakos-dashboard.service",
        "README.md",
        "requirements.txt",
    ])
    def test_required_file_exists(self, path):
        assert (PACKAGE_ROOT / path).exists(), f"Required file missing: {path}"

    @pytest.mark.parametrize("path", [
        "dashboard/package.json",
        "dashboard/package-lock.json",
        "dashboard/app/layout.tsx",
        "dashboard/app/page.tsx",
        "dashboard/lib/api.ts",
    ])
    def test_dashboard_file_exists(self, path):
        assert (PACKAGE_ROOT / path).exists(), f"Dashboard file missing: {path}"

    def test_scripts_are_executable(self):
        """Shell scripts should have execute permission."""
        non_executable = []
        for script in PACKAGE_ROOT.glob("bin/*.sh"):
            if not os.access(script, os.X_OK):
                non_executable.append(script.name)
        assert not non_executable, (
            f"Scripts not executable: {', '.join(non_executable)}\n"
            f"Fix with: chmod +x bin/{' bin/'.join(non_executable)}"
        )

    def test_native_start_is_executable(self):
        script = PACKAGE_ROOT / "native" / "start.sh"
        assert os.access(script, os.X_OK), "native/start.sh is not executable"
