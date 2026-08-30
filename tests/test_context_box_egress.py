"""
Tests for context_box.render_envelope_mirror_line() — the rendering half of
the generalized envelope-egress mechanism (`mirror_to`, task-1788124679).

context_box.py had zero pytest coverage before this (only exercised via
relay.py's own untested inline wiring and its module docstring's manual
`show`/`record` CLI). This covers the one new pure function the mirror_to
generalization added; the parsing/validation half lives in handoff.py's own
selftest (`python3 bin/handoff.py`), and the relay.py wiring that picks
between the state-triggered board path and this envelope-only path follows
the same untested-inline-glue pattern as the pre-existing context_box block
it sits next to.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

PACKAGE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PACKAGE_ROOT / "bin"))

os.environ.setdefault("WORKSPACE_ROOT", str(PACKAGE_ROOT))

import context_box  # noqa: E402


def _envelope(kind="status", subject=""):
    return SimpleNamespace(kind=kind, subject=subject)


def test_renders_kind_subject_sender_and_channel():
    line = context_box.render_envelope_mirror_line(
        _envelope(kind="correction", subject="stale-cache-fix"),
        sender="Amos",
        source_channel="agent-chat",
    )
    assert "correction" in line
    assert "stale-cache-fix" in line
    assert "Amos" in line
    assert "agent-chat" in line


def test_empty_subject_falls_back_to_placeholder():
    line = context_box.render_envelope_mirror_line(
        _envelope(kind="finding", subject=""),
        sender="Zero",
        source_channel="agent-chat",
    )
    assert "(no subject)" in line


def test_whitespace_only_subject_falls_back_to_placeholder():
    line = context_box.render_envelope_mirror_line(
        _envelope(kind="finding", subject="   "),
        sender="Zero",
        source_channel="agent-chat",
    )
    assert "(no subject)" in line
