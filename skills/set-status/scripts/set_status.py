#!/usr/bin/env python3
"""
set_status skill implementation.

Writes the desired Discord presence to data/status/marvin.json. This
script does not talk to Discord directly — relay.py owns the actual
gateway connection and polls this file (see _status_poll_loop in
relay.py), same separation as everything else here: the agent-server
process that runs this script is not the same process holding the
Discord websocket, so the only sanctioned handoff is a file relay
already watches.

2026-08-18, Ian: "your status needs to reflect the state of your
thinking AND the other bot can read it (and so can I)" — this is the
write side of that. Presence read side (reading Amos's status) already
existed via DiscordAdapter's presence intent + data/presence.json.

2026-08-18, later same day: agent-server.py now also writes this file
automatically at turn start/end (_write_mechanical_status), mirroring
Amos's mechanical idle/busy-per-turn presence layer. The "source" field
below is how the two writers stay out of each other's way — this script
always stamps "manual", the mechanical layer stamps "auto" and never
overwrites an active manual idle/dnd declaration. Calling this with
state="online" (the documented "Done" step) also relinquishes control
back to the mechanical layer, not just clears the dot.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

VALID_STATES = {"online", "idle", "dnd"}
MAX_ACTIVITY_LEN = 128


def main():
    args_json = os.environ.get("TOOL_ARGS", "{}")
    try:
        args = json.loads(args_json)
    except json.JSONDecodeError:
        print(json.dumps({"error": "Invalid TOOL_ARGS JSON"}))
        sys.exit(1)

    state = args.get("state")
    activity = args.get("activity") or ""

    if state not in VALID_STATES:
        print(json.dumps({
            "error": f"Invalid state {state!r}, must be one of {sorted(VALID_STATES)}"
        }))
        sys.exit(1)

    activity = activity.strip()
    if len(activity) > MAX_ACTIVITY_LEN:
        activity = activity[:MAX_ACTIVITY_LEN]

    workspace = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
    status_file = workspace / "data" / "status" / "marvin.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "state": state,
        "activity": activity or None,
        "source": "manual",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Full rewrite, not a patch — single small file, same reasoning as
    # write_presence_snapshot() in relay.py: nothing here benefits from
    # partial updates and a full rewrite can't drift out of sync.
    tmp_file = status_file.with_suffix(".json.tmp")
    with open(tmp_file, "w") as f:
        json.dump(payload, f, indent=2)
    tmp_file.replace(status_file)

    print(json.dumps({"status": "success", **payload}))


if __name__ == "__main__":
    main()
