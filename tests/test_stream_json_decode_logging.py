"""
Tests for the JSONDecodeError/UnicodeDecodeError logging added to
read_agent_response's stream-json read loop (task-1788135135, 2026-08-31).

Real incident: long single-turn replies were silently losing whole
interior sections before ever reaching Discord -- no delivery error, no
exception in the logs, content just missing from the text itself and
resuming at a non-adjacent section (confirmed via #lounge history,
2026-08-31 00:02-00:14, re-pasting a design doc to another bot twice,
same gap both times).

Root cause traced to `except json.JSONDecodeError: continue` in the main
read loop -- completely silent, no log, no counter. stream-json is one
JSON object per line; if a single large `text` content block's write
ever gets split across an unescaped newline upstream, both the line and
its continuation fail to parse and were dropped with zero trace, while
whatever assistant events parsed fine before and after kept
concatenating into final_text -- exactly the "cuts off, resumes
elsewhere" symptom, as opposed to a simple end-of-stream truncation
(which the existing 16 MiB readline-limit fix from 2026-08-07 already
covers and does log).

Not yet proven as the definitive mechanism -- these tests lock in the
instrumentation (a malformed line must now log, not vanish) so the next
real occurrence leaves evidence instead of requiring another guess, and
confirm the surrounding content still assembles correctly around the
dropped line rather than the whole turn aborting.

Like test_spend_limit_detection.py, this imports the real module and
drives read_agent_response with a fake stdout -- it has real subprocess
I/O (proc.stdout.readline()), so a minimal stream-json-shaped fake
process is enough to exercise the actual loop without a live CLI.
"""

import json
import logging

import pytest

from conftest import import_script


class FakeStdout:
    """Minimal async stand-in for proc.stdout -- readline() pops one
    pre-baked line at a time, then signals EOF with b'' like a real
    closed pipe."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)


class FakeProc:
    def __init__(self, lines):
        self.stdout = FakeStdout(lines)


def _line(payload: dict) -> bytes:
    return (json.dumps(payload) + "\n").encode()


def _text_event(text: str) -> bytes:
    return _line(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}], "usage": {}},
        }
    )


def _result_event(**overrides) -> bytes:
    base = {
        "type": "result",
        "usage": {},
        "total_cost_usd": 0.0,
        "duration_ms": 100,
        "is_error": False,
        "session_id": "test-session",
    }
    base.update(overrides)
    return _line(base)


@pytest.fixture
def agent_server(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    mod = import_script("agent-server")
    mod.agent_config["TestAgent"] = {"tool_streaming": False, "stream_to_channel": False}
    mod.channels_config = {"channels": {"signals": {"id": "999888777"}}}
    return mod


@pytest.mark.asyncio
async def test_malformed_line_is_logged_not_silent(agent_server, caplog):
    """The core regression: a line that fails json.loads must produce a
    log record, not vanish. Before this fix, `except json.JSONDecodeError:
    continue` had no log call at all anywhere in its body."""
    agent_server.agent_processes["TestAgent"] = FakeProc(
        [
            _text_event("first block, arrives fine"),
            b"{this is not valid json at all\n",
            _text_event("second block, arrives fine"),
            _result_event(),
        ]
    )
    with caplog.at_level(logging.WARNING):
        final_text, _, _, _ = await agent_server.read_agent_response(
            "TestAgent", "999888777", []
        )

    assert final_text == "first block, arrives finesecond block, arrives fine"
    assert "unparseable stream-json line" in caplog.text
    assert "TestAgent" in caplog.text


@pytest.mark.asyncio
async def test_content_before_and_after_bad_line_both_survive(agent_server):
    """A malformed line in the middle of the stream must not take down
    the rest of the turn -- both the block before and the block after it
    should still land in final_text. This is the "resumes at a
    non-adjacent section" bug made visible: without this, a bad line
    either aborted the whole read (losing the second block) or --
    depending on where it fell -- silently erased content with nothing
    to show for it either way."""
    agent_server.agent_processes["TestAgent"] = FakeProc(
        [
            _text_event("## Core loop (solver side)\n\nPer-plate state: "),
            b"{not json\n",
            _text_event("stain instances with a type and severity."),
            _result_event(),
        ]
    )
    final_text, _, pending_final, _ = await agent_server.read_agent_response(
        "TestAgent", "999888777", []
    )

    assert "Core loop (solver side)" in final_text
    assert "stain instances with a type and severity." in final_text
    # stream_to_channel is False for this fixture (quiet channel), so
    # pending_final should carry the full assembled text, same as
    # final_text -- not just the trailing fragment.
    assert pending_final == final_text


@pytest.mark.asyncio
async def test_undecodable_bytes_logged_with_byte_length(agent_server, caplog):
    """A line that fails at the .decode() stage (invalid UTF-8) is a
    different failure than malformed JSON -- distinct log message, and
    critically must not raise up into the outer broad `except Exception`,
    which would silently abort reading the rest of the stream with no
    named cause."""
    agent_server.agent_processes["TestAgent"] = FakeProc(
        [
            _text_event("before the bad bytes"),
            b"\xff\xfe not valid utf-8 \n",
            _text_event("after the bad bytes"),
            _result_event(),
        ]
    )
    with caplog.at_level(logging.WARNING):
        final_text, _, _, _ = await agent_server.read_agent_response(
            "TestAgent", "999888777", []
        )

    assert "undecodable stdout line" in caplog.text
    assert "before the bad bytes" in final_text
    assert "after the bad bytes" in final_text


@pytest.mark.asyncio
async def test_clean_stream_unaffected(agent_server, caplog):
    """No malformed lines at all -- no warning noise, normal assembly."""
    agent_server.agent_processes["TestAgent"] = FakeProc(
        [
            _text_event("all good here."),
            _result_event(),
        ]
    )
    with caplog.at_level(logging.WARNING):
        final_text, _, _, _ = await agent_server.read_agent_response(
            "TestAgent", "999888777", []
        )

    assert final_text == "all good here."
    assert "unparseable stream-json line" not in caplog.text
    assert "undecodable stdout line" not in caplog.text
