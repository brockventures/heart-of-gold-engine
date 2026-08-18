#!/usr/bin/env python3
"""
queue_outbox_message — MCP tool wrapper around bin/outbox.py's add_pending().

Built 2026-08-18 after a recurring failure mode Ian named "jumbling": a
turn scoped to one Discord channel would blend content meant for a
second channel/audience into that single in-turn reply, so the second
audience never got its own message (concrete incident: a delivery
confirmation to Amos got folded into an engineering summary for Ian and
both landed only in #agent-chat — see
agents/Marvin/memory/facts/agent-chat-replies-also-outbox-to-general.md).

bin/outbox.py already solved the *delivery* half of cross-channel
posting (durable queue + scheduler.py flush, live since 2026-08-08,
26/26 track record). It was only reachable via a raw Bash shell-out
(`python3 bin/outbox.py add <channel> <content>`), which depends on
remembering mid-turn that it exists and that this is the moment to use
it — exactly the step that kept getting skipped. This wraps the same
add_pending() as a first-class MCP tool, listed every turn next to
set_status, so queuing the other channel's message is one deliberate,
visible tool call instead of a recalled-from-memory shell command.

This tool only queues; it never touches the current turn's own reply.
The discipline it encodes: the instant a turn's content would cover two
audiences, call this for the *other* channel's content immediately,
then write the in-turn reply for *this* channel only.
"""

import json
import os
import sys
from pathlib import Path

# Kept in sync with config/channels.json by hand (small, stable list;
# see docstring in that file's consumers for why it isn't parsed here —
# this script must not depend on channels.json's exact shape changing
# out from under it without a matching tools.json enum update anyway).
VALID_CHANNELS = {"general", "signals", "staff-comms", "agent-chat", "lounge"}
MAX_CONTENT_LEN = 4000


def main():
    args_json = os.environ.get("TOOL_ARGS", "{}")
    try:
        args = json.loads(args_json)
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid TOOL_ARGS JSON"}))
        sys.exit(1)

    channel = args.get("channel")
    content = (args.get("content") or "").strip()

    if channel not in VALID_CHANNELS:
        print(json.dumps({
            "error": f"Invalid channel {channel!r}, must be one of {sorted(VALID_CHANNELS)}"
        }))
        sys.exit(1)

    if not content:
        print(json.dumps({"error": "content is required and cannot be empty"}))
        sys.exit(1)

    if len(content) > MAX_CONTENT_LEN:
        print(json.dumps({
            "error": f"content exceeds {MAX_CONTENT_LEN} chars ({len(content)})"
        }))
        sys.exit(1)

    # bin/ is a fixed sibling of skills/ in this package regardless of what
    # WORKSPACE_ROOT points at (WORKSPACE_ROOT only controls where outbox.py
    # itself writes data — see its module-level OUTBOX_PATH), so resolve the
    # import from this script's own location rather than WORKSPACE_ROOT.
    package_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(package_root / "bin"))
    import outbox  # bin/outbox.py — module-level paths read WORKSPACE_ROOT at import time

    row_id = outbox.add_pending(channel, content)
    print(json.dumps({
        "status": "queued",
        "id": row_id,
        "channel": channel,
    }))


if __name__ == "__main__":
    main()
