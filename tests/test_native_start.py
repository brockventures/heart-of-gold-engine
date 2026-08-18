"""
Tests for native/start.sh — one-time bootstrap for a native (no-Docker)
install, replacing bin/entrypoint.sh's per-container-start logic now that
systemd owns process supervision (see native-migration-complete-2026-08-11
in memory, and the file's own header comment).

Added 2026-08-18 alongside deleting the Docker-era test suite
(tests/test_smoke_docker.py, test_entrypoint_volume_guard.py,
test_entrypoint_discord_registration.py, most of test_preflight.py) — this
repo's actual deployment has been native systemd since 2026-08-11 and had
zero test coverage of the script that replaced entrypoint.sh until now.

Two things intentionally NOT ported from the old entrypoint.sh suite:
  - The volume-writability guard: native/start.sh's own comment says why —
    that failure mode was specific to a Docker named volume seeded from an
    old image's ownership; there's no equivalent here, "not ported" is the
    documented decision, not an oversight.
  - Testing `exec supervisord` reached at the end: start.sh is a one-time
    bootstrap, not a supervisor — it just exits 0. Nothing to stand in for.

What IS ported: the Discord slash-command registration behavior
(register only when all three vars are set, a failure must warn but not
abort) is identical logic to entrypoint.sh's, same technique (fake
register-discord-commands.py on disk that leaves a marker).
"""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent
START_SCRIPT = PACKAGE_ROOT / "native" / "start.sh"

DISCORD_ENV = {
    "DISCORD_BOT_TOKEN_PRIMARY": "test-token",
    "DISCORD_BOT_ID_PRIMARY": "111222333",
    "DISCORD_SERVER_ID": "444555666",
}


def _make_executable(path: Path, script_body: str):
    path.write_text(script_body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def make_workspace(tmp_path: Path, registration_exit: int = 0, with_agents: bool = True) -> Path:
    (tmp_path / "bin").mkdir()
    (tmp_path / "system").mkdir()
    (tmp_path / "config").mkdir()

    marker = tmp_path / "registration-ran.marker"
    _make_executable(
        tmp_path / "bin" / "register-discord-commands.py",
        f"import pathlib, sys\npathlib.Path(r'{marker}').touch()\nsys.exit({registration_exit})\n",
    )

    # install-hooks.sh / install-post-commit-hook.sh: real copies from this
    # repo, since start.sh just `cp`s them verbatim — no need to fake these.
    for name in ("install-hooks.sh", "install-post-commit-hook.sh"):
        real = PACKAGE_ROOT / "system" / name
        if real.exists():
            (tmp_path / "system" / name).write_text(real.read_text())
            (tmp_path / "system" / name).chmod(0o755)
    (tmp_path / "system" / "check-protected-paths.py").touch()
    (tmp_path / "system" / "reload-on-commit.py").touch()

    if with_agents:
        (tmp_path / "config" / "agents.json").write_text(
            json.dumps({"agents": {"Marvin": {}, "relay": {}}})
        )

    return tmp_path


def run_start(workspace: Path, extra_env=None) -> subprocess.CompletedProcess:
    # Strip real Discord credentials this live install runs with — same
    # reasoning as the entrypoint.sh suite this replaces: without this,
    # "not configured" cases would see the real env leak through.
    base_environ = {k: v for k, v in os.environ.items() if k not in DISCORD_ENV}
    env = {
        **base_environ,
        "WORKSPACE_ROOT": str(workspace),
        "DASHBOARD_PORT": "3000",
        "AGENT_SERVER_TOKEN": "test-token",
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", str(START_SCRIPT)],
        env=env,
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_missing_required_env_vars_aborts():
    workspace_env = {
        k: v for k, v in os.environ.items()
        if k not in ("WORKSPACE_ROOT", "DASHBOARD_PORT", "AGENT_SERVER_TOKEN")
    }
    result = subprocess.run(
        ["bash", str(START_SCRIPT)],
        env=workspace_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "WORKSPACE_ROOT" in result.stderr or "WORKSPACE_ROOT" in result.stdout


def test_missing_dashboard_port_aborts(tmp_path):
    workspace = make_workspace(tmp_path)
    result = run_start(workspace, {"DASHBOARD_PORT": ""})
    assert result.returncode == 1
    assert "DASHBOARD_PORT" in result.stderr


def test_creates_data_log_inbox_directories(tmp_path):
    workspace = make_workspace(tmp_path)
    result = run_start(workspace)

    assert result.returncode == 0, result.stderr
    for rel in (
        "data/messages", "data/memory", "data/health",
        "logs/agent-streams", "logs/session-summaries", "inbox",
    ):
        assert (workspace / rel).is_dir(), f"{rel} not created"


def test_creates_per_agent_inbox_and_journal_dirs(tmp_path):
    workspace = make_workspace(tmp_path, with_agents=True)
    result = run_start(workspace)

    assert result.returncode == 0, result.stderr
    for agent in ("Marvin", "relay"):
        assert (workspace / "inbox" / agent).is_dir()
        assert (workspace / "agents" / agent / "inbox").is_dir()
        assert (workspace / "agents" / agent / "journal").is_dir()


def test_skips_per_agent_dirs_when_no_agents_json(tmp_path):
    """No agents.json yet (very first bootstrap) must not be a hard error."""
    workspace = make_workspace(tmp_path, with_agents=False)
    result = run_start(workspace)
    assert result.returncode == 0, result.stderr


def test_git_init_is_idempotent(tmp_path):
    workspace = make_workspace(tmp_path)
    first = run_start(workspace)
    assert first.returncode == 0, first.stderr
    assert (workspace / ".git").is_dir()

    second = run_start(workspace)
    assert second.returncode == 0, second.stderr


def test_installs_pre_commit_and_post_commit_hooks(tmp_path):
    workspace = make_workspace(tmp_path)
    result = run_start(workspace)

    assert result.returncode == 0, result.stderr
    pre_commit = workspace / ".git" / "hooks" / "pre-commit"
    post_commit = workspace / ".git" / "hooks" / "post-commit"
    assert pre_commit.exists() and os.access(pre_commit, os.X_OK)
    assert post_commit.exists() and os.access(post_commit, os.X_OK)


def test_registration_skipped_when_discord_not_configured(tmp_path):
    workspace = make_workspace(tmp_path)
    marker = workspace / "registration-ran.marker"

    result = run_start(workspace)

    assert result.returncode == 0, result.stderr
    assert not marker.exists(), "registration ran with no Discord config present"


def test_registration_skipped_when_partially_configured(tmp_path):
    workspace = make_workspace(tmp_path)
    marker = workspace / "registration-ran.marker"

    result = run_start(workspace, {"DISCORD_BOT_TOKEN_PRIMARY": "test-token"})

    assert result.returncode == 0, result.stderr
    assert not marker.exists()


def test_registration_runs_when_fully_configured(tmp_path):
    workspace = make_workspace(tmp_path)
    marker = workspace / "registration-ran.marker"

    result = run_start(workspace, DISCORD_ENV)

    assert result.returncode == 0, result.stderr
    assert marker.exists(), "start.sh did not invoke slash-command registration"


def test_registration_failure_does_not_abort_bootstrap(tmp_path):
    workspace = make_workspace(tmp_path, registration_exit=1)

    result = run_start(workspace, DISCORD_ENV)

    assert result.returncode == 0, (
        f"a registration failure must not abort the rest of bootstrap:\n{result.stderr}"
    )
    assert "WARNING" in result.stderr
    assert "slash-command registration failed" in result.stderr
