#!/usr/bin/env python3
"""discord-read.py — Read messages directly from a Discord channel via the
REST API, bypassing relay's ingest pipeline entirely.

Why this exists (2026-08-08, per Ian): the message_queue table in
data/memory/agent-server.db is downstream of relay's on_message handler —
if relay drops a message, is mid-restart, or a channel's gate logic never
promotes it to a queued turn, it simply never lands there. That happened
live tonight: Amos posted in #agent-chat at 08:23:41 UTC and it's absent
from message_queue entirely, discovered only by querying Discord directly.
This script is the fix — a read-only path to Discord itself as the source
of truth, independent of and a backup to our own DB, for exactly the
"did I actually miss something" question the DB can't always answer.

Deliberately stricter than discord-notify.sh's channel resolution: that
script falls back to treating an unrecognized name as a literal channel ID
if it's not in config/channels.json. This one does not -- an unresolvable
channel name is a hard error, not a fallback, since the entire point is a
scoped, auditable read path rather than an arbitrary-channel one. Same
spirit as gmail_guard.py's Marvin-folder restriction: the safeguard is in
the code, not in remembering to be careful.

Usage:
    discord-read.py agent-chat
    discord-read.py agent-chat --limit 30
    discord-read.py agent-chat --after 000000000000000000
    discord-read.py signals --agent Marvin --json

Output is newest-first (Discord's own default), one message per block,
plain text by default or one-JSON-object-per-line with --json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
API_BASE = "https://discord.com/api/v10"
USER_AGENT = "karakos-discord-read (https://iancoley.org, 0.1)"


def load_channel_id(channel_name: str) -> str:
    """Resolve a configured channel name to its Discord ID. Hard error on
    anything not in config/channels.json -- no literal-ID fallback, see
    module docstring."""
    cfg_path = WORKSPACE_ROOT / "config" / "channels.json"
    cfg = json.loads(cfg_path.read_text())
    channels = cfg.get("channels", {})
    if channel_name not in channels:
        known = ", ".join(sorted(channels)) or "(none configured)"
        raise SystemExit(
            f"Error: '{channel_name}' is not a configured channel. "
            f"Known channels: {known}"
        )
    channel_id = channels[channel_name].get("id")
    if not channel_id:
        raise SystemExit(f"Error: channel '{channel_name}' has no id in channels.json")
    return channel_id


def load_bot_token(agent_name: str | None) -> str:
    """Resolve a bot token via config/agents.json's discord_bot_token_env,
    same lookup discord-notify.sh uses. If agent_name is given, use that
    agent's token specifically; otherwise take the first agent with a
    resolvable token (matches discord-notify.sh's permissive default)."""
    cfg_path = WORKSPACE_ROOT / "config" / "agents.json"
    cfg = json.loads(cfg_path.read_text())
    agents = cfg.get("agents", {})

    candidates = [agent_name] if agent_name else list(agents.keys())
    for name in candidates:
        info = agents.get(name, {})
        env_var = info.get("discord_bot_token_env", "")
        if not env_var:
            continue
        token = os.environ.get(env_var, "")
        if token:
            return token

    raise SystemExit(
        f"Error: no Discord bot token available"
        + (f" for agent '{agent_name}'" if agent_name else "")
    )


def fetch_messages(
    channel_id: str, token: str, limit: int, before: str | None, after: str | None
) -> list[dict]:
    params = [f"limit={limit}"]
    if before:
        params.append(f"before={before}")
    if after:
        params.append(f"after={after}")
    url = f"{API_BASE}/channels/{channel_id}/messages?{'&'.join(params)}"

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bot {token}", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 429:
            body = json.loads(e.read())
            retry_after = float(body.get("retry_after", 1.0))
            time.sleep(retry_after + 0.1)
            return fetch_messages(channel_id, token, limit, before, after)
        raise SystemExit(f"Discord API error {e.code}: {e.read().decode()[:500]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("channel", help="Configured channel name, e.g. agent-chat")
    parser.add_argument("--limit", type=int, default=20, help="Max messages (Discord caps at 100)")
    parser.add_argument("--before", help="Only messages before this message ID")
    parser.add_argument("--after", help="Only messages after this message ID")
    parser.add_argument("--agent", help="Bot token owner (default: first agent with a token)")
    parser.add_argument("--json", action="store_true", help="One JSON object per line instead of plain text")
    args = parser.parse_args()

    channel_id = load_channel_id(args.channel)
    token = load_bot_token(args.agent)
    messages = fetch_messages(channel_id, token, args.limit, args.before, args.after)

    for m in messages:
        author = m.get("author", {})
        if args.json:
            print(json.dumps({
                "id": m.get("id"),
                "timestamp": m.get("timestamp"),
                "author": author.get("username"),
                "author_id": author.get("id"),
                "is_bot": author.get("bot", False),
                "content": m.get("content"),
            }))
        else:
            print("=" * 60)
            print(
                f"{m.get('timestamp')} | {author.get('username')} "
                f"(id={author.get('id')}, bot={author.get('bot', False)}) | msg_id={m.get('id')}"
            )
            print(m.get("content"))

    return 0


if __name__ == "__main__":
    sys.exit(main())
