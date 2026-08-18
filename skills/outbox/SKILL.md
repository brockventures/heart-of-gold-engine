# Outbox Skill

Queues a message for delivery to a Discord channel other than the one
the current turn is scoped to. Built 2026-08-18 to fix "jumbling" — a
turn blending content for two audiences/channels into a single in-turn
reply, so the second audience's message never actually goes out.

## What It Does

Provides a `queue_outbox_message` tool that appends a row to
`data/outbox/pending.jsonl` via `bin/outbox.py`'s `add_pending()`.
`bin/scheduler.py` flushes that queue roughly once a minute, delivering
each row through the same bot-token path `discord-notify.sh` always
used. This tool is a thin, validated front door onto that existing,
already-proven mechanism (live since 2026-08-08) — it doesn't change how
delivery works, only how it's invoked: a first-class MCP tool call
instead of a Bash shell-out that has to be remembered mid-turn.

## When To Use It

The moment a turn's reply would otherwise need to cover two channels or
two audiences — e.g. a status line meant for Ian arriving inside a
turn that's scoped to #agent-chat, or a technical reply to another
agent arriving inside a turn scoped to #general. Call this tool for the
*other* channel's content first, then write the in-turn reply for only
the channel this turn is actually scoped to. Don't try to write one
message that covers both — that's the exact failure this exists to
close off.

Not for: routine single-audience replies (just answer in-turn), or
resending the same content to two channels for redundancy (queue the
content that's actually different for the other audience, not a copy).

## Files

```
outbox/
├── SKILL.md                       # This file
├── tools.json                     # Tool definition (MCP schema)
└── scripts/
    └── queue_outbox_message.py    # Validates + calls bin/outbox.py add_pending()
```

## Usage

```
Tool: queue_outbox_message
Input: {"channel": "general", "content": "Delivered the campaign-assistant fix to Amos, details in #agent-chat if you want them."}
Output: {"status": "queued", "id": "...", "channel": "general"}
```

Errors (all non-zero exit, JSON `{"error": ...}` on stdout):

- `channel` missing or not one of `general`, `signals`, `staff-comms`,
  `agent-chat`, `lounge`
- `content` empty or over 4000 chars

## Data

Delegates entirely to `bin/outbox.py` / `data/outbox/pending.jsonl` —
see that module's docstring and
`agents/Marvin/memory/facts/outbox-cross-channel-fix-2026-08-08.md` for
the underlying queue/flush design. This skill adds no new storage.
