#!/usr/bin/env python3
"""
outbox.py — Durable queue for messages Marvin needs to post to a Discord
channel outside the channel the current turn is scoped to.

Problem this solves (2026-08-08): every turn is pre-scoped to one channel
by a routing header ("this turn posts ONLY to #general"). When something
needs to go to a *different* channel, the only options were:

  (a) a raw curl POST via the bot token mid-turn (discord-notify.sh called
      directly) — worked most nights, failed with a synthetic 403/40333 at
      least once (see agents/Marvin/memory/facts/discord-api-crosspost-
      blocked.md), and even when it works it's a side-channel bypass of
      the normal relay path rather than a supported action; or
  (b) just remembering to say it the next time a turn happens to be
      scoped to the right channel — which depends on Ian noticing and
      prompting, since nothing survives between turns to remind Marvin.

This module turns "I owe #general a message" into a durable row instead
of a mental note that evaporates at end-of-turn. `add_pending()` (or the
`add` CLI) appends a row to data/outbox/pending.jsonl; scheduler.py calls
`flush_pending()` on a short interval to actually deliver it, using the
same bot-token REST call discord-notify.sh always used — this module
doesn't replace that delivery mechanism, it just makes calling it durable
and scheduled instead of ad hoc and turn-dependent.

Storage: one JSON object per line —
    {id, channel, content, attachments, created_at, delivered_at}
Delivered rows are kept (not deleted) with delivered_at set, for audit;
flush only acts on rows where delivered_at is still null.

Attachments (2026-08-24): `attachments` is a list of absolute local file
paths, resolved at add-time (not delivery-time) so a later change of cwd
can't silently break the reference. discord-notify.sh does the actual
upload; if a path has gone missing by the time flush runs, that surfaces
as an ordinary delivery failure (row stays pending, error goes to
stderr) rather than a silent text-only fallback.
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
OUTBOX_PATH = WORKSPACE_ROOT / "data" / "outbox" / "pending.jsonl"
NOTIFY_SCRIPT = WORKSPACE_ROOT / "bin" / "discord-notify.sh"


def _load_rows() -> list[dict]:
    if not OUTBOX_PATH.exists():
        return []
    rows = []
    with open(OUTBOX_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _save_rows(rows: list[dict]) -> None:
    OUTBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTBOX_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    tmp.replace(OUTBOX_PATH)


def add_pending(channel: str, content: str, attachments: list[str] | None = None) -> str:
    """Queue a message for delivery to `channel`, optionally with local
    file attachments. Returns the row id."""
    rows = _load_rows()
    row_id = str(uuid.uuid4())
    rows.append({
        "id": row_id,
        "channel": channel,
        "content": content,
        "attachments": [str(Path(p).resolve()) for p in (attachments or [])],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "delivered_at": None,
    })
    _save_rows(rows)
    return row_id


def flush_pending() -> list[str]:
    """Attempt delivery of every undelivered row via discord-notify.sh.

    Rows that fail to deliver are left undelivered for the next flush —
    transient Discord/network failures shouldn't drop a queued message,
    only a successful post marks it done.
    """
    rows = _load_rows()
    delivered = []
    changed = False
    for row in rows:
        if row.get("delivered_at"):
            continue
        try:
            subprocess.run(
                [str(NOTIFY_SCRIPT), row["channel"], row["content"], *row.get("attachments", [])],
                check=True, capture_output=True, text=True,
            )
            row["delivered_at"] = datetime.now(timezone.utc).isoformat()
            delivered.append(row["id"])
            changed = True
        except subprocess.CalledProcessError as e:
            sys.stderr.write(
                f"outbox: delivery failed for {row['id']} -> #{row['channel']}: "
                f"{e.stderr.strip() if e.stderr else e}\n"
            )
    if changed:
        _save_rows(rows)
    return delivered


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    add_p = sub.add_parser("add", help="Queue a message for a channel")
    add_p.add_argument("channel")
    add_p.add_argument("content")
    add_p.add_argument(
        "--file", action="append", dest="files", default=[],
        help="Local file path to attach; repeat for multiple attachments",
    )

    sub.add_parser("flush", help="Attempt delivery of all pending rows")
    sub.add_parser("list", help="Show pending (undelivered) rows")

    args = parser.parse_args()

    if args.cmd == "add":
        row_id = add_pending(args.channel, args.content, attachments=args.files)
        suffix = f" with {len(args.files)} attachment(s)" if args.files else ""
        print(f"Queued {row_id} -> #{args.channel}{suffix}")
    elif args.cmd == "flush":
        delivered = flush_pending()
        print(f"Delivered {len(delivered)} message(s)" if delivered else "Nothing to deliver")
    elif args.cmd == "list":
        pending = [r for r in _load_rows() if not r.get("delivered_at")]
        if not pending:
            print("Outbox empty")
        for r in pending:
            atts = r.get("attachments") or []
            att_note = f" ({len(atts)} attachment(s))" if atts else ""
            print(f"{r['id']} [{r['created_at']}] -> #{r['channel']}{att_note}: {r['content'][:80]}")


if __name__ == "__main__":
    main()
