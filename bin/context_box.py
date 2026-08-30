#!/usr/bin/env python3
"""
context_box.py — persistent board for the handoff envelope's `context_box`
field (see handoff.py's docstring for the schema).

Problem this solves (Ian, 2026-08-27): "agent-chat stalls out because
it's unclear (to me and Mike) where the blocker is" — and he shouldn't
need to open #agent-chat to find out. Two standing facts already
described the shape of the gap without closing it:

  - facts/decisions-need-explicit-flag-2026-08-27.md: a decision embedded
    in prose reads as FYI, not a pending ask.
  - facts/agent-chat-replies-also-outbox-to-general.md: the existing fix
    for "content meant for Ian doesn't reach #general" is a *judgment
    call made fresh every turn* ("is this worth mirroring?") — and it
    recurred three times (2026-08-08 x2, 2026-08-18) even with a written
    standing rule, because remembering isn't a mechanism.

This module is the mechanism: a small on-disk board, one row per
`subject`, updated whenever a parsed handoff envelope carries a
`context_box`. relay.py's inbound message handler calls `record()` for
every #agent-chat message with one, and — for `state in {blocked,
waiting-human}` only — auto-queues a one-line mirror to #general via
outbox.add_pending(), unconditionally, no per-turn judgment call. `/sys
context` (relay.py) renders the current board on demand, so Ian/Mike can
check status at any moment without opening #agent-chat at all, not just
when a new message happens to trigger a mirror.

Storage: data/context_boxes.json, one object per `subject` (last write
wins per subject — this is a current-status board, not a history log;
the underlying #agent-chat messages remain the audit trail). Written
atomically (tmp file + rename), same pattern as outbox.py.

Known gap (see handoff.py's context_box docstring): this only covers the
*inbound* half. relay.py's on_message handler never sees Marvin's own
outgoing replies (`if message.author == self.user: return` skips them
before parsing runs), so Marvin's own blockers don't yet auto-record or
auto-mirror. Closing that requires a call into this module from
agent-server.py's post_to_discord (or at compose time) — filed as a
follow-up, not built yet. Until then, Marvin's own context_box fields
still rely on the same queue_outbox_message discipline as before, just
now with a structured line to put in it instead of a freeform one.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
BOARD_PATH = WORKSPACE_ROOT / "data" / "context_boxes.json"

MIRROR_STATES = {"blocked", "waiting-human"}


def _load_board() -> dict:
    if not BOARD_PATH.exists():
        return {}
    try:
        with open(BOARD_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        # A corrupt board file shouldn't take down message handling —
        # same fail-open posture as handoff.py's parser. Worst case: the
        # board resets to empty on next record().
        return {}


def _save_board(board: dict) -> None:
    BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BOARD_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(board, f, indent=2, sort_keys=True)
    tmp.replace(BOARD_PATH)


def record(
    subject: str,
    state: str,
    blocked_on: Optional[str] = None,
    waiting_on: Optional[str] = None,
    sender: str = "unknown",
    channel: str = "unknown",
) -> dict:
    """Update the board's row for `subject` (last write wins). Returns the
    stored row. `subject` falling back to a placeholder when empty keeps
    every context_box visible on the board even from a sender who didn't
    bother filling in `subject` — an unlabeled blocker is still a blocker."""
    board = _load_board()
    key = subject.strip() if subject and subject.strip() else "(no subject)"
    board[key] = {
        "state": state,
        "blocked_on": blocked_on,
        "waiting_on": waiting_on,
        "sender": sender,
        "channel": channel,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_board(board)
    return board[key]


def should_mirror(state: str) -> bool:
    return state in MIRROR_STATES


def render_mirror_line(subject: str, row: dict) -> str:
    """One-line rendering for the auto-mirror to #general — deliberately
    terse, this is a pointer at the blocker, not the full status."""
    bits = [f"**agent-chat stalled** — `{subject}` [{row['state']}]"]
    if row.get("blocked_on"):
        bits.append(f"blocked on: {row['blocked_on']}")
    if row.get("waiting_on"):
        bits.append(f"waiting on: {row['waiting_on']}")
    bits.append(f"(from {row.get('sender', 'unknown')})")
    return " — ".join(bits)


def render_envelope_mirror_line(envelope, sender: str, source_channel: str) -> str:
    """One-line rendering for the generalized envelope-egress path
    (`mirror_to`, added 2026-08-30 -- task-1788124679), used when a
    message requests a mirror with no triggering `context_box` at all.
    Deliberately separate from render_mirror_line() above: that one
    renders a *board row* (state/blocked_on/waiting_on) for the
    state-triggered mechanism; this one renders an *envelope* directly,
    since a `mirror_to`-only message was never recorded on the board in
    the first place -- there's no stalled-thread state to show, just "the
    sender wanted this specific message seen here."""
    subject = envelope.subject.strip() if envelope.subject and envelope.subject.strip() else "(no subject)"
    return (
        f"**{envelope.kind}** — `{subject}` "
        f"(from {sender} in #{source_channel})"
    )


def render_board(open_only: bool = True) -> str:
    """Render the current board for /sys context. `open_only` (default)
    drops `resolved` rows — the board is meant to answer "what's stuck
    right now," not serve as history."""
    board = _load_board()
    if not board:
        return "**/sys context**: board is empty — nothing's been recorded."

    rows = sorted(board.items(), key=lambda kv: kv[1].get("updated_at", ""), reverse=True)
    if open_only:
        rows = [(k, v) for k, v in rows if v.get("state") != "resolved"]
        if not rows:
            return "**/sys context**: nothing open — every recorded thread is resolved."

    lines = ["**/sys context** — current agent-chat thread status:"]
    for subject, row in rows:
        line = f"- `{subject}` [{row.get('state', '?')}]"
        if row.get("blocked_on"):
            line += f" — blocked on: {row['blocked_on']}"
        if row.get("waiting_on"):
            line += f" — waiting on: {row['waiting_on']}"
        line += f" ({row.get('sender', 'unknown')}, {row.get('updated_at', '?')})"
        lines.append(line)
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    show_p = sub.add_parser("show", help="Render the current board")
    show_p.add_argument("--all", action="store_true", help="Include resolved rows")

    rec_p = sub.add_parser("record", help="Manually record a row (mainly for testing)")
    rec_p.add_argument("subject")
    rec_p.add_argument("state", choices=["active", "blocked", "waiting-human", "resolved"])
    rec_p.add_argument("--blocked-on")
    rec_p.add_argument("--waiting-on")
    rec_p.add_argument("--sender", default="manual")
    rec_p.add_argument("--channel", default="manual")

    args = parser.parse_args()

    if args.cmd == "show":
        print(render_board(open_only=not args.all))
    elif args.cmd == "record":
        row = record(
            args.subject, args.state,
            blocked_on=args.blocked_on, waiting_on=args.waiting_on,
            sender=args.sender, channel=args.channel,
        )
        print(f"Recorded `{args.subject}`: {row}")


if __name__ == "__main__":
    main()
