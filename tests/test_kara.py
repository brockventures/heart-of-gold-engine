"""
Tests for bin/kara — CLI client.

The script self-execs under .venv when present, so we test by invoking it
as a subprocess with a sentinel env var disabling the self-exec.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import PACKAGE_ROOT

KARA = PACKAGE_ROOT / "bin" / "kara"


def _run(args, env_extra=None, stdin=None, timeout=10):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(KARA), *args],
        capture_output=True, text=True, env=env, input=stdin, timeout=timeout,
    )


def test_kara_exists_and_executable():
    assert KARA.exists()
    assert os.access(KARA, os.X_OK)


def test_kara_parses():
    """Script must parse as valid Python."""
    import ast
    ast.parse(KARA.read_text())


def test_help_flag():
    proc = _run(["--help"])
    assert proc.returncode == 0
    assert "kara" in proc.stdout.lower()
    assert "AGENT_SERVER_URL" in proc.stdout
    assert "KARA_CHANNEL" in proc.stdout


def test_unknown_flag():
    proc = _run(["--bogus"])
    assert proc.returncode == 2
    assert "unknown flag" in proc.stderr.lower()


def test_missing_token_oneshot(tmp_path):
    """Without AGENT_SERVER_TOKEN, one-shot should fail clean."""
    # Force agent so we don't try to discover one
    proc = _run(
        ["hello"],
        env_extra={
            "AGENT_SERVER_TOKEN": "",
            "KARA_AGENT": "amos",
            "AGENT_SERVER_URL": "http://127.0.0.1:1",  # unreachable
        },
        timeout=5,
    )
    assert proc.returncode != 0
    assert "AGENT_SERVER_TOKEN" in proc.stderr or "kara:" in proc.stderr


def test_default_channel_constant():
    """Sanity-check that DEFAULT_CHANNEL reads from KARA_CHANNEL env."""
    src = KARA.read_text()
    assert 'os.environ.get("KARA_CHANNEL", "cli")' in src


def test_message_id_prefix():
    """Outgoing messages are tagged cli-<uuid> so the server can spot them."""
    src = KARA.read_text()
    assert '"cli-{uuid.uuid4()}"' in src or 'f"cli-{uuid.uuid4()}"' in src
