#!/usr/bin/env python3
"""discord-presence.py — Read the live presence snapshot relay.py maintains.

Why this exists (2026-08-18, per Ian): wanting a way to tell whether Amos
(or anyone else in the shared servers) is mid-task before pinging them,
rather than guessing. Presence (online/idle/dnd + custom activity) is a
gateway-only concept in Discord's API — there's no REST endpoint to poll
it on demand the way discord-read.py polls messages. Only a client with a
live gateway connection and the GUILD_PRESENCES + GUILD_MEMBERS intents
sees it, and relay.py is our one such connection. So this script doesn't
open its own connection (a second concurrent gateway session on the same
bot token is unnecessary and something to avoid) — it just reads the
snapshot relay already keeps current at data/presence.json, written on
every presence_update event plus once on startup.

Caveat as of this writing: intents are confirmed live (verified via a
standalone test connection before wiring this in), but no agent process
on either side (us or Amos's) sets a task-specific custom activity yet —
so today this will show "online" with Discord's own default/no activity,
not "building PR #18". Reading the signal and producing the signal are
separate pieces of work; this is only the reading half.

Usage:
    discord-presence.py                 # everyone in the snapshot
    discord-presence.py --name Amos     # filter by name (case-insensitive substring)
    discord-presence.py --json          # raw JSON instead of the table
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/opt/karakos"))
PRESENCE_FILE = WORKSPACE_ROOT / "data" / "presence.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--name", help="Filter by name (case-insensitive substring)")
    parser.add_argument("--json", action="store_true", help="Raw JSON instead of a table")
    args = parser.parse_args()

    if not PRESENCE_FILE.exists():
        print(
            f"No presence snapshot yet at {PRESENCE_FILE} — relay hasn't "
            "written one (not restarted since the intent was wired in, or "
            "GUILD_PRESENCES isn't actually enabled).",
            file=sys.stderr,
        )
        return 1

    data = json.loads(PRESENCE_FILE.read_text())
    members = data.get("members", {})

    if args.name:
        needle = args.name.lower()
        members = {k: v for k, v in members.items() if needle in v.get("name", "").lower()}

    if args.json:
        print(json.dumps({"updated_at": data.get("updated_at"), "members": members}, indent=2))
        return 0

    print(f"Presence snapshot as of {data.get('updated_at', '?')}")
    print("=" * 60)
    if not members:
        print("(no matching members)")
        return 0

    for key, m in sorted(members.items()):
        activity = m.get("activity")
        activity_str = f" | {activity['type']}: {activity['name']}" if activity else ""
        bot_str = " [bot]" if m.get("bot") else ""
        print(f"{key}{bot_str} — {m.get('status', '?')}{activity_str}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
