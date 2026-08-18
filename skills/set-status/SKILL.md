# Set Status Skill

Sets Marvin's live Discord presence (status dot + short activity text) so
Ian and other bots (e.g. Amos) can see what kind of work is happening
without it being announced in chat and without them having to guess from
silence.

## What It Does

Provides a `set_status` tool that writes the desired presence to
`data/status/marvin.json`. relay.py polls that file (`_status_poll_loop`)
and calls `change_presence()` against the live Discord connection —
this script itself never touches the gateway, since the process running
skill scripts (agent-server) isn't the process holding the websocket
(relay).

## When To Use It

Maps to the status-update tiers agreed with Ian 2026-08-18:

- **Quick think / short task** — don't call this. Default resting state
  (Online, no activity) covers it; flickering status for a five-second
  lookup is noise.
- **Long autonomous session, checkpointing** — `state: "idle"` with
  `activity` set to the current step (e.g. `"presence refactor: editing
  relay.py"`), updated at each checkpoint.
- **Going dark** — `state: "dnd"` with `activity` stating the task and a
  rough ETA (e.g. `"heads-down: presence refactor, back ~30m"`), set
  once going in.
- **Done** — `state: "online"`, no `activity`, to clear it. Call this at
  the end of any idle/dnd stretch — nothing else clears it automatically.

## Files

```
set-status/
├── SKILL.md                  # This file
├── tools.json                # Tool definition (MCP schema)
└── scripts/
    └── set_status.py         # Writes data/status/marvin.json
```

## Usage

```
Tool: set_status
Input: {"state": "dnd", "activity": "heads-down: presence refactor, back ~30m"}
Output: {"status": "success", "state": "dnd", "activity": "heads-down: presence refactor, back ~30m", "updated_at": "2026-08-18T04:05:00+00:00"}
```

Clearing:

```
Tool: set_status
Input: {"state": "online"}
Output: {"status": "success", "state": "online", "activity": null, "updated_at": "..."}
```

## Data

`data/status/marvin.json` — full-rewrite-on-write, single small file,
written atomically (tmp file + rename) so relay's poll never reads a
half-written file.
