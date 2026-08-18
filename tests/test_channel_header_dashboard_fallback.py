"""
Tests for the "#0" channel-header bug found live 2026-08-18: Ian tested
Marvin's responsiveness via the dashboard chat UI (not Discord) mid rate-
limit incident, and the turn-scoping header rendered as
"This turn posts ONLY to #0" — channel_id "0" is a deliberate sentinel
the dashboard's /api/chat route uses for "no Discord channel to
cross-post to" (see dashboard/app/api/chat/route.ts), but
process_agent_queue's channel_name lookup had no fallback for a
channel_id that isn't in channels_config at all, so it rendered the raw
sentinel as if it were a literal (nonexistent) Discord channel.

Fix: fall back to the channel name already resolved and stored on the
message row at insert time (messages[0]["channel"]) instead of the raw
channel_id, and phrase non-Discord origins ("server" != "discord") as
"the X chat (not Discord...)" rather than a fake "#X".

Like test_attachments.py, this checks the logic structurally (source
inspection) rather than running process_agent_queue end-to-end — that
function needs a live DB/queue/subprocess to execute, which is out of
proportion for what's really a string-formatting fallback fix. The
detection/suppression logic in read_agent_response (a separate, more
self-contained piece of tonight's work) gets real execution coverage in
test_spend_limit_detection.py instead.
"""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).parent.parent
AGENT_SERVER = PACKAGE_ROOT / "bin" / "agent-server.py"


def _channel_header_body() -> str:
    src = AGENT_SERVER.read_text()
    start = src.index("# Explicit channel header (2026-08-06)")
    end = src.index("\n        # Start typing indicator", start)
    return src[start:end]


def test_channel_name_lookup_defaults_to_none_not_raw_id():
    """The channels_config lookup must fall through to None (a
    recognizable "not found" sentinel we then branch on), not silently
    default to the raw channel_id — that raw fallback is exactly what
    rendered "#0" for the dashboard's sentinel channel_id."""
    body = _channel_header_body()
    assert "if cfg.get(\"id\") == channel_id),\n            None,\n        )" in body


def test_non_discord_origin_does_not_render_as_a_fake_channel():
    """A channel_id with no channels_config match falls back to the
    stored channel name (messages[0]["channel"]), and non-Discord origins
    get phrased as a chat, not a fake "#channel"."""
    body = _channel_header_body()
    assert 'messages[0]["channel"]' in body
    assert 'messages[0]["server"] == "discord"' in body
    assert "not Discord" in body


def test_activity_text_reuses_channel_label_not_bare_channel_name():
    """channel_name is None for non-Discord origins after the fix above
    — the mechanical-status activity text ("replying in #x") must use
    the already-resolved channel_label, not f"...{channel_name}", or it
    regresses to literally rendering "replying in #None"."""
    src = AGENT_SERVER.read_text()
    start = src.index("# Mechanical presence activity text")
    end = src.index("\n        if len(messages) > 1:", start)
    body = src[start:end]
    assert "activity_bits = [f\"replying in {channel_label}\"]" in body
    assert "channel_name}" not in body
