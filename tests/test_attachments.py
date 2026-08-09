"""
Tests for Discord attachment delivery, ported alongside the feature from
mcarmody/karakos-package#127 (2026-08-09).

Before this, posting an image with "what's in this" got answered about the
text only — the relay downloaded nothing and the envelope described
nothing, so the agent never learned a file existed.

Split like test_rate_limit_pause.py / test_agent_server_routes.py:
format_attachments() and safe_attachment_name() are pure functions (no
event loop, no sqlite, no subprocess), so they're imported and exercised
directly. Everything wired into handle_message/process_agent_queue and
relay's send_to_agent_server is checked structurally instead, matching
this suite's existing convention of not booting the full server/Discord
client.
"""

import json
import sys

import pytest

from conftest import import_script, PACKAGE_ROOT

AGENT_SERVER = PACKAGE_ROOT / "bin" / "agent-server.py"
RELAY = PACKAGE_ROOT / "bin" / "relay.py"


@pytest.fixture
def agent_server():
    return import_script("agent-server")


@pytest.fixture
def relay():
    # relay.py does `from reply_gate import ...` / `from handoff import ...`
    # as bare (non-package) imports — fine when relay.py is actually
    # launched from bin/ (see supervisord.conf), but import_script() loads
    # it by file path without bin/ on sys.path. Add it just for this
    # import, same fix reply_gate.py/handoff.py's own test files rely on
    # implicitly by living in bin/ themselves.
    bin_dir = str(PACKAGE_ROOT / "bin")
    added = bin_dir not in sys.path
    if added:
        sys.path.insert(0, bin_dir)
    try:
        return import_script("relay")
    finally:
        if added:
            sys.path.remove(bin_dir)


# ---------------------------------------------------------------------------
# format_attachments (agent-server.py)
# ---------------------------------------------------------------------------

def test_format_attachments_empty_cases(agent_server):
    assert agent_server.format_attachments(None) == ""
    assert agent_server.format_attachments("") == ""
    assert agent_server.format_attachments("[]") == ""
    assert agent_server.format_attachments([]) == ""


def test_format_attachments_saved_file(agent_server):
    raw = json.dumps([{
        "filename": "diagram.png",
        "content_type": "image/png",
        "size": 204800,
        "path": "/workspace/data/attachments/123/0-diagram.png",
        "skipped": None,
    }])
    out = agent_server.format_attachments(raw)
    assert "diagram.png" in out
    assert "image/png" in out
    assert "200.0 KB" in out
    assert "/workspace/data/attachments/123/0-diagram.png" in out
    assert "1 attachment(s)" in out


def test_format_attachments_skipped_file_still_described(agent_server):
    """The point of the feature: a failed/oversize download still gets a
    line in the envelope, so the agent can say what happened rather than
    going quiet about the file entirely."""
    raw = json.dumps([{
        "filename": "movie.mp4",
        "content_type": "video/mp4",
        "size": 90_000_000,
        "path": None,
        "skipped": "exceeds the 26214400 byte download limit",
    }])
    out = agent_server.format_attachments(raw)
    assert "movie.mp4" in out
    assert "NOT saved" in out
    assert "exceeds the 26214400 byte download limit" in out


def test_format_attachments_accepts_list_or_json_string(agent_server):
    entry = {"filename": "a.txt", "path": "/tmp/a.txt", "skipped": None}
    from_list = agent_server.format_attachments([entry])
    from_json = agent_server.format_attachments(json.dumps([entry]))
    assert from_list == from_json


def test_format_attachments_malformed_json_fails_open(agent_server):
    """Unparseable input must not crash the envelope build — a corrupt
    column should degrade to "no attachments shown", not a 500 mid-turn."""
    assert agent_server.format_attachments("{not valid json") == ""


# ---------------------------------------------------------------------------
# safe_attachment_name (relay.py)
# ---------------------------------------------------------------------------

def test_safe_attachment_name_strips_unsafe_chars(relay):
    # "/" is replaced with "_", then leading dots are stripped (so the
    # leading ".." from "../.." doesn't survive as a path component) —
    # see test_safe_attachment_name_empty_or_dots_only for that rule in
    # isolation.
    assert relay.safe_attachment_name("../../config/agents.json", 0) == "0-_.._config_agents.json"


def test_safe_attachment_name_path_traversal_cannot_escape_index_prefix(relay):
    """The index prefix plus character-stripping means the result can
    never contain a bare '..' path segment or a leading '/' — verified
    directly rather than just asserting on one example string."""
    name = relay.safe_attachment_name("../../../etc/passwd", 3)
    assert not name.startswith("/")
    assert "/" not in name
    assert name.startswith("3-")


def test_safe_attachment_name_index_prefix_prevents_collision(relay):
    """Two attachments sharing a filename must not collide — without the
    index prefix the second silently overwrites the first on disk and the
    agent reads the same bytes twice."""
    a = relay.safe_attachment_name("photo.jpg", 0)
    b = relay.safe_attachment_name("photo.jpg", 1)
    assert a != b


def test_safe_attachment_name_empty_or_dots_only(relay):
    assert relay.safe_attachment_name("", 0) == "0-attachment"
    assert relay.safe_attachment_name("...", 0) == "0-attachment"


def test_safe_attachment_name_long_name_keeps_extension(relay):
    long_name = "a" * 200 + ".png"
    result = relay.safe_attachment_name(long_name, 0)
    assert result.endswith(".png")
    assert len(result) <= len("0-") + 96


# ---------------------------------------------------------------------------
# Structural checks — wiring, matching this file's existing convention for
# anything that needs the event loop / sqlite / a real Discord message.
# ---------------------------------------------------------------------------

def test_message_queue_has_attachments_column_and_migration():
    src = AGENT_SERVER.read_text()
    assert "attachments TEXT" in src
    assert 'ALTER TABLE message_queue ADD COLUMN attachments TEXT' in src


def test_handle_message_allows_empty_content_with_attachments():
    """An image with no caption must reach the agent — this used to be
    rejected as 'Empty content' before ever checking for attachments."""
    src = AGENT_SERVER.read_text()
    start = src.index("async def handle_message")
    end = src.index("\nasync def ", start + 1)
    body = src[start:end]
    assert "if not content and not attachments:" in body
    assert '"attachments"' in body  # inserted into message_queue


def test_process_agent_queue_appends_attachment_lines_to_envelope():
    src = AGENT_SERVER.read_text()
    start = src.index("async def process_agent_queue")
    end = src.index("\nasync def ", start + 1)
    body = src[start:end]
    assert "format_attachments(msg[\"attachments\"])" in body


def test_relay_downloads_attachments_before_posting():
    src = RELAY.read_text()
    assert "async def download_attachments" in src
    send_start = src.index("async def send_to_agent_server")
    send_end = src.index("\n    async def ", send_start + 1)
    body = src[send_start:send_end]
    assert "await self.download_attachments(message)" in body
    assert '"attachments": attachments' in body


def test_relay_download_happens_in_send_not_on_message():
    """Downloads must be triggered from send_to_agent_server, not
    on_message — a message the reply gates are about to drop should never
    touch disk. Checked by confirming on_message itself never calls
    download_attachments directly."""
    src = RELAY.read_text()
    on_message_start = src.index("async def on_message")
    on_message_end = src.index("\n    async def ", on_message_start + 1)
    body = src[on_message_start:on_message_end]
    assert "download_attachments" not in body
