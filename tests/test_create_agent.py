"""
Tests for bin/create-agent.sh — agent creation script.

Focus: the heredoc-injection vector flagged in #61. User input
(`--discord-token`, etc.) is now passed via env vars and read inside
single-quoted heredocs, so a token containing a single quote cannot
end the literal and execute arbitrary Python.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).parent.parent
CREATE_AGENT = PACKAGE_ROOT / "bin" / "create-agent.sh"


@pytest.fixture
def workspace(tmp_path):
    """Build a minimal WORKSPACE_ROOT layout the script can run against."""
    (tmp_path / "agents" / "templates").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "inbox").mkdir()

    template = tmp_path / "agents" / "templates" / "primary.md"
    template.write_text(
        "# {{AGENT_NAME}}\n\nSystem: {{SYSTEM_NAME}}\nOwner: {{OWNER_NAME}}\n"
        "Channels:\n{{CHANNELS}}\n\nOther agents:\n{{OTHER_AGENTS}}\n"
    )

    agents_json = tmp_path / "config" / "agents.json"
    agents_json.write_text(json.dumps({"agents": {}}, indent=2) + "\n")

    return tmp_path


def _run(workspace, *args, env_extra=None):
    env = {
        **os.environ,
        "WORKSPACE_ROOT": str(workspace),
        # Avoid hitting a real agent-server in tests
        "AGENT_SERVER_PORT": "1",  # 1 → connection refused → script handles gracefully
        "AGENT_SERVER_TOKEN": "",
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(CREATE_AGENT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_creates_agent_with_simple_name(workspace):
    result = _run(workspace, "alpha")
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"

    agent_dir = workspace / "agents" / "alpha"
    assert agent_dir.is_dir()
    assert (agent_dir / "SYSTEM_PROMPT.md").is_file()

    agents = json.loads((workspace / "config" / "agents.json").read_text())
    assert "alpha" in agents["agents"]
    assert agents["agents"]["alpha"]["model"] == "sonnet"


def test_rejects_uppercase_name(workspace):
    result = _run(workspace, "BadName")
    assert result.returncode != 0
    assert "lowercase alphanumeric" in result.stderr


def test_discord_token_with_quotes_does_not_inject(workspace):
    """A token containing single quotes used to terminate the python literal
    and execute the rest. Verify the env-pass refactor neutralizes that."""
    # This token would have escaped the old `'$DISCORD_TOKEN'` literal and
    # injected `__import__('os').system('touch /tmp/PWNED_xxx')` in the
    # original code. The new implementation reads it from os.environ.
    sentinel = workspace / "PWNED_should_not_exist"
    payload = (
        "x'); __import__('pathlib').Path('"
        + str(sentinel)
        + "').touch(); ('"
    )

    result = _run(workspace, "beta", "--discord-token", payload)
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert not sentinel.exists(), "injection landed — heredoc terminator failed"

    agents = json.loads((workspace / "config" / "agents.json").read_text())
    assert agents["agents"]["beta"].get("discord_bot_token_env") == "DISCORD_BOT_TOKEN_BETA"


def test_register_writes_correct_env_var_name(workspace):
    """Hyphens in agent names should map to underscores in the env var name."""
    result = _run(workspace, "ops-relay", "--discord-token", "anything")
    assert result.returncode == 0, f"stderr: {result.stderr}"

    agents = json.loads((workspace / "config" / "agents.json").read_text())
    assert agents["agents"]["ops-relay"]["discord_bot_token_env"] == "DISCORD_BOT_TOKEN_OPS_RELAY"


def test_no_single_quote_interpolation_in_python_blocks():
    """Defensive: catch a regression where someone reintroduces `'$VAR'` inside
    a `python3 -c` or heredoc body. The fixed version uses `os.environ`."""
    content = CREATE_AGENT.read_text()
    # Cheap heuristic: every python invocation in the script should use the
    # env-passing pattern (`<<'PY'`) and never the inline `python3 -c "..."`
    # form that interpolates shell vars into Python literals.
    assert "python3 -c \"" not in content, (
        "Found `python3 -c \"...\"` — switch to `python3 - <<'PY'` with env-passed values."
    )
