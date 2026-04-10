"""
Smoke tests — Verify the Docker build succeeds and core files are present.

These tests validate that the Dockerfile builds without errors and that the
resulting image contains all expected components. This would have caught both
of Ian's installation issues (missing package-lock.json, invalid route export).
"""

import subprocess
import pytest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parent.parent


class TestDockerBuild:
    """Verify Docker image builds successfully."""

    @pytest.mark.slow
    def test_docker_build_succeeds(self):
        """The Docker image should build without errors."""
        result = subprocess.run(
            ["docker", "build", "-t", "karakos-test:smoke", "."],
            cwd=str(PACKAGE_ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, (
            f"Docker build failed:\n{result.stderr[-2000:]}"
        )

    @pytest.mark.slow
    def test_docker_build_has_dashboard(self):
        """Built image should contain compiled dashboard."""
        result = subprocess.run(
            [
                "docker", "run", "--rm", "karakos-test:smoke",
                "test", "-d", "/workspace/dashboard/.next",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, "Dashboard .next directory missing from image"

    @pytest.mark.slow
    def test_docker_build_has_python_deps(self):
        """Built image should have Python dependencies installed."""
        result = subprocess.run(
            [
                "docker", "run", "--rm", "karakos-test:smoke",
                "python3", "-c", "import aiohttp, aiosqlite, discord; print('ok')",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Python deps missing:\n{result.stderr}"
        )

    @pytest.mark.slow
    def test_docker_build_has_node(self):
        """Built image should have Node.js installed."""
        result = subprocess.run(
            [
                "docker", "run", "--rm", "karakos-test:smoke",
                "node", "--version",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, "Node.js not installed in image"
        assert result.stdout.strip().startswith("v"), f"Unexpected node version: {result.stdout}"


class TestFileStructure:
    """Verify required files exist in the repository."""

    @pytest.mark.parametrize("path", [
        "Dockerfile",
        "config/docker-compose.yml",
        "config/supervisord.conf",
        "config/.env.template",
        "config/protected-paths.json",
        "bin/agent-server.py",
        "bin/relay.py",
        "bin/scheduler.py",
        "bin/entrypoint.sh",
        "bin/capture.py",
        "bin/health-monitor.py",
        "bin/memory-maintenance.py",
        "bin/purge-data.py",
        "bin/summarize-session.py",
        "bin/poke.sh",
        "bin/heartbeat.sh",
        "bin/create-agent.sh",
        "mcp/tools-server.py",
        "setup.sh",
        "install.sh",
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

    def test_setup_is_executable(self):
        assert os.access(PACKAGE_ROOT / "setup.sh", os.X_OK), "setup.sh is not executable"

    def test_install_is_executable(self):
        assert os.access(PACKAGE_ROOT / "install.sh", os.X_OK), "install.sh is not executable"


class TestDockerCompose:
    """Verify docker-compose configuration is valid."""

    def test_compose_config_valid(self):
        """docker compose config should parse without errors.

        Creates a minimal .env if missing, since compose references it.
        """
        import tempfile
        env_path = PACKAGE_ROOT / "config" / ".env"
        created_env = False

        if not env_path.exists():
            env_path.write_text("AGENT_SERVER_TOKEN=test\n")
            created_env = True

        try:
            result = subprocess.run(
                ["docker", "compose", "-f", "config/docker-compose.yml", "config"],
                cwd=str(PACKAGE_ROOT),
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, (
                f"docker compose config failed:\n{result.stderr}"
            )
        finally:
            if created_env:
                env_path.unlink()


class TestNextjsRouteExports:
    """Verify Next.js route files only export valid handlers.

    This test directly prevents the verifySessionToken export bug
    that broke Ian's build (issue #32).
    """

    VALID_EXPORTS = {
        "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
        # Next.js config exports
        "dynamic", "dynamicParams", "revalidate", "fetchCache",
        "runtime", "preferredRegion", "maxDuration",
        "generateStaticParams", "generateMetadata", "metadata",
    }

    def test_route_files_have_valid_exports(self):
        """Route files should only export valid Next.js handlers."""
        import re

        app_dir = PACKAGE_ROOT / "dashboard" / "app"
        issues = []

        for route_file in app_dir.rglob("route.ts"):
            content = route_file.read_text()
            # Find named exports: export { foo } or export function foo
            # and export async function foo
            export_pattern = re.compile(
                r'export\s+(?:async\s+)?function\s+(\w+)'
                r'|export\s*\{\s*([^}]+)\s*\}'
            )
            for match in export_pattern.finditer(content):
                if match.group(1):
                    name = match.group(1)
                    if name not in self.VALID_EXPORTS:
                        rel = route_file.relative_to(PACKAGE_ROOT)
                        issues.append(f"{rel}: invalid export '{name}'")
                elif match.group(2):
                    for name in match.group(2).split(","):
                        name = name.strip().split(" as ")[0].strip()
                        if name and name not in self.VALID_EXPORTS:
                            rel = route_file.relative_to(PACKAGE_ROOT)
                            issues.append(f"{rel}: invalid export '{name}'")

        assert not issues, (
            "Route files have invalid exports:\n" + "\n".join(issues)
        )


import os
