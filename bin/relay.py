#!/usr/bin/env python3
"""
Karakos Relay — Discord + Dispatch + Capture

Adapters:
- DiscordAdapter: Routes Discord messages to agent server
- DispatchAdapter: Watches inbox dirs, invokes builder/reviewer
- CaptureAdapter: Persists Discord messages to JSONL
"""

import asyncio
import discord
import fcntl
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
from logging.handlers import RotatingFileHandler

from reply_gate import Decision, GateMessage, ReplyGate, SCORER_PROMPT
from handoff import parse_handoff

# =============================================================================
# Utilities
# =============================================================================

def split_discord_message(text: str, max_length: int = 2000) -> List[str]:
    """Split text into chunks Discord will accept (max 2000 chars each).

    Splits on the largest boundary that fits — paragraph, then line, then a
    hard cut mid-line. The hard cut is the part that matters: a reply with no
    blank line and no newline in it has no boundary to split on, and the
    previous implementation returned it as a single oversize chunk. Discord
    rejects anything over 2000 with a 400 and the message is lost.
    """
    if len(text) <= max_length:
        return [text] if text else []

    chunks: List[str] = []
    remaining = text

    while len(remaining) > max_length:
        window = remaining[:max_length]
        cut = window.rfind("\n\n")
        if cut <= 0:
            cut = window.rfind("\n")
        if cut <= 0:
            # A solid wall of text. Cut it at the limit rather than handing
            # Discord something it will refuse.
            cut = max_length
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")

    if remaining:
        chunks.append(remaining)

    return chunks if chunks else [text]

# =============================================================================
# Configuration
# =============================================================================

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
AGENTS_CONFIG_PATH = WORKSPACE_ROOT / "config" / "agents.json"
CHANNELS_CONFIG_PATH = WORKSPACE_ROOT / "config" / "channels.json"
MESSAGES_DIR = WORKSPACE_ROOT / "data" / "messages"
HEALTH_FILE = WORKSPACE_ROOT / "data" / "health" / "relay.json"

# Retry-spooling for messages the agent server rejects or can't be reached
# for (Task #9, built 2026-08-06). Before this, send_to_agent_server()
# logged a non-202/exception and dropped the message — confirmed as a
# real, live failure (Marvin lost a message to a 429 on 2026-08-05, no
# retry, no notice) and matches upstream karakos-package issue #88, which
# names this install as the reproduction case. Shape (spool-and-retry,
# not spool-forever) follows the pattern Amos described for his own
# `poke-amos.sh` — not his source, ported from the description only.
DEFERRED_POKE_DIR = WORKSPACE_ROOT / "data" / "deferred-pokes"
DEFERRED_POKE_DEAD_DIR = DEFERRED_POKE_DIR / "dead"
DEFERRED_POKE_FLUSH_INTERVAL_SEC = 30
DEFERRED_POKE_MAX_AGE_SEC = 24 * 3600  # give up and move to dead/ after this

AGENT_SERVER_PORT = os.environ.get("AGENT_SERVER_PORT", "18791")
AGENT_SERVER_URL = os.environ.get("AGENT_SERVER_URL", f"http://localhost:{AGENT_SERVER_PORT}")
AGENT_SERVER_TOKEN = os.environ.get("AGENT_SERVER_TOKEN", "")
OWNER_DISCORD_ID = int(os.environ.get("OWNER_DISCORD_ID", "0"))

# Dispatch config
DISPATCH_INBOX_DIR = WORKSPACE_ROOT / "inbox"
DISPATCH_POLL_INTERVAL = 30
MAX_CONCURRENT_BUILDERS = int(os.environ.get("MAX_CONCURRENT_BUILDERS", "1"))
MAX_CONCURRENT_REVIEWERS = int(os.environ.get("MAX_CONCURRENT_REVIEWERS", "2"))
DISPATCH_TIMEOUTS = {
    "reviewer": 3600,    # 1 hour
    "builder": 21600,    # 6 hours
}

# Logging
log = logging.getLogger("relay")
log.setLevel(logging.INFO)
handler = RotatingFileHandler(
    WORKSPACE_ROOT / "logs" / "relay.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=7
)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
log.addHandler(handler)

console = logging.StreamHandler()
console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(console)

# Singleton-instance guard (2026-08-07) — added after the 08:02-08:05
# duplicate-process incident: a rogue duplicate supervisord launched a
# second copy of this process alongside the real one. relay.py doesn't
# bind a listening port, so nothing about a normal double-launch failed
# loudly the way agent-server's port conflict did — the second copy just
# ran, undetected, with its own Discord connection and its own 30s
# retry-spool loop, racing the real one on every message. See
# agents/Marvin/memory/facts/agent-server-duplicate-process-incident.md
# for what that actually caused (alternating 401/500s, duplicate
# spool entries). Kept as a module-level reference so the flock isn't
# released by garbage collection; the OS releases it automatically the
# instant this process exits for ANY reason, including a hard kill —
# deliberately not a PID file, which would need its own stale-cleanup
# logic that could itself get skipped the same way the duplicate
# supervisord's children were.
_SINGLETON_LOCK_FD = None

def _acquire_singleton_lock(name: str) -> None:
    global _SINGLETON_LOCK_FD
    lock_path = WORKSPACE_ROOT / "data" / f"{name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.critical(
            f"Another {name} instance already holds {lock_path} — refusing "
            "to start as a duplicate. If this is unexpected (e.g. a stale "
            "lock after a hard crash), the OS should already have released "
            "it on process exit — check for a genuinely live process before "
            "assuming the lock file itself needs manual cleanup."
        )
        sys.exit(1)
    fd.write(str(os.getpid()))
    fd.flush()
    _SINGLETON_LOCK_FD = fd

# Global state
agent_config: Dict = {}
channels_config: Dict = {}
discord_id_to_agent: Dict[int, str] = {}
active_dispatches: Dict[str, asyncio.Task] = {}
dispatch_semaphores: Dict[str, asyncio.Semaphore] = {}

# =============================================================================
# Configuration Loading
# =============================================================================

def load_config():
    """Load agent and channel configuration"""
    global agent_config, channels_config, discord_id_to_agent

    # Load agents
    if AGENTS_CONFIG_PATH.exists():
        with open(AGENTS_CONFIG_PATH) as f:
            config_data = json.load(f)
            agent_config = config_data.get("agents", {})
    else:
        agent_config = {}
        log.warning(f"Agents config not found: {AGENTS_CONFIG_PATH}")

    # Load channels
    if CHANNELS_CONFIG_PATH.exists():
        with open(CHANNELS_CONFIG_PATH) as f:
            channels_config = json.load(f)
    else:
        channels_config = {}
        log.warning(f"Channels config not found: {CHANNELS_CONFIG_PATH}")

    # Build Discord ID map
    for agent_name, config in agent_config.items():
        bot_id_env = config.get("discord_bot_id_env")
        if bot_id_env:
            bot_id = os.environ.get(bot_id_env)
            if bot_id:
                discord_id_to_agent[int(bot_id)] = agent_name

    log.info(f"Loaded config for {len(agent_config)} agents, {len(channels_config.get('channels', {}))} channels")

# =============================================================================
# Discord Adapter
# =============================================================================

class DiscordAdapter(discord.Client):
    """Discord message routing to agent server"""

    def __init__(self, *args, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.reactions = True
        super().__init__(intents=intents, *args, **kwargs)

        self.http_session = None
        self.server_ids = []
        self.gate: Optional[ReplyGate] = None
        self._health_task: Optional[asyncio.Task] = None
        self._deferred_poke_task: Optional[asyncio.Task] = None

        # Native Discord slash commands for the three /sys commands that
        # already have a real handler (Task #12, 2026-08-07). We're a
        # single bare discord.Client, unlike Amos's five-bots-per-process
        # setup — his reason for staying on raw REST registration instead
        # of discord.py's CommandTree (avoiding a restructure across all
        # five) doesn't apply here, so CommandTree is the better call for
        # us: officially supported, handles registration and interaction
        # dispatch itself, one less hand-rolled REST surface to get
        # subtly wrong. Registers only status/clear/reload — the ones
        # with a real handler already. Amos's explicit warning, taken
        # seriously: a command that registers cleanly and has no matching
        # branch silently does nothing when clicked, nothing errors
        # anywhere. The text `/sys` intercept stays as-is, unchanged,
        # both paths call the same _run_sys_command().
        self.tree = discord.app_commands.CommandTree(self)
        self._register_slash_commands()

    def _register_slash_commands(self):
        adapter = self

        async def _owner_check(interaction: discord.Interaction) -> bool:
            if OWNER_DISCORD_ID == 0 or interaction.user.id != OWNER_DISCORD_ID:
                await interaction.response.send_message(
                    "`[SYS]` Permission denied.", ephemeral=True
                )
                return False
            return True

        def _default_agent() -> Optional[str]:
            return next(
                (name for name, cfg in agent_config.items()
                 if cfg.get("discord_bot_token_env")),
                next(iter(agent_config), None)
            )

        @self.tree.command(name="status", description="Agent server status")
        async def status_cmd(interaction: discord.Interaction):
            if not await _owner_check(interaction):
                return
            reply = await adapter._run_sys_command("status", None)
            await interaction.response.send_message(reply)

        @self.tree.command(name="clear", description="Clear session + restart subprocess (destructive)")
        @discord.app_commands.describe(agent="Target agent (default: the channel's owning agent)")
        async def clear_cmd(interaction: discord.Interaction, agent: Optional[str] = None):
            if not await _owner_check(interaction):
                return
            reply = await adapter._run_sys_command("clear", agent or _default_agent())
            await interaction.response.send_message(reply)

        @self.tree.command(name="reload", description="Restart subprocess, keep session")
        @discord.app_commands.describe(agent="Target agent (default: the channel's owning agent)")
        async def reload_cmd(interaction: discord.Interaction, agent: Optional[str] = None):
            if not await _owner_check(interaction):
                return
            reply = await adapter._run_sys_command("reload", agent or _default_agent())
            await interaction.response.send_message(reply)

    async def setup_hook(self):
        """Initialize HTTP session"""
        import aiohttp
        self.http_session = aiohttp.ClientSession()
        # "server_ids" (list) supports multiple connected guilds; fall back
        # to the older singular "server_id" for configs that predate that.
        server_ids = channels_config.get("server_ids")
        if server_ids is None:
            single = channels_config.get("server_id")
            server_ids = [single] if single else []
        self.server_ids = [str(s) for s in server_ids]
        log.info("Discord adapter initialized")

    async def on_ready(self):
        """Bot logged in"""
        log.info(f"Discord bot ready as {self.user.name} (ID: {self.user.id})")
        # Reply gate: graduated wake logic for channels marked gate_mode
        # "tier2" in channels.json (currently #agent-chat only). Design is
        # Amos's (Mike's Karakos instance), ported with credit — see
        # reply_gate.py docstring. One instance covers every gated channel;
        # cooldown state is tracked per-channel internally.
        self.gate = ReplyGate(
            self_id=str(self.user.id),
            names=(self.user.name.lower(),),
            threshold=0.5,
            cooldown_sec=300,
        )
        await self.write_health_heartbeat()

        # health-monitor.py checks relay.json's age against a 5-minute
        # threshold, but write_health_heartbeat() used to only fire once
        # here in on_ready — so a relay that's been happily connected for
        # longer than 5 minutes without a reconnect would false-positive
        # as "stale" even though it's fine. Found via a real alert
        # 2026-08-06. Refresh periodically instead of only on (re)connect.
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_heartbeat_loop())

        # Task #9, 2026-08-06: retry spooled messages the agent server
        # rejected or couldn't be reached for. See DEFERRED_POKE_DIR above
        # and send_to_agent_server()/_flush_deferred_pokes() below.
        if self._deferred_poke_task is None or self._deferred_poke_task.done():
            self._deferred_poke_task = asyncio.create_task(self._deferred_poke_flush_loop())

        # Task #12, 2026-08-07: sync the native slash commands to the
        # primary guild only (server_ids[0] — Heart of Gold, not Amos's
        # Crab Cavern, which we're also connected to). Guild-scoped sync
        # is instant per Amos's own note; global sync takes up to an hour
        # to propagate, which makes iterating on it miserable. Re-synced
        # on every reconnect — idempotent, discord.py only pushes an
        # update if the command set actually changed.
        if self.server_ids:
            try:
                guild = discord.Object(id=int(self.server_ids[0]))
                # Commands registered via @self.tree.command(...) with no
                # explicit guild= are tracked as global in the tree's own
                # bookkeeping — sync(guild=X) alone only pushes commands
                # already associated with X, which is none of them.
                # copy_global_to() copies the global set into that
                # guild's local set first. First deploy synced 0 commands
                # without this — found live, not assumed.
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info(f"Synced {len(synced)} slash command(s) to guild {self.server_ids[0]}")
            except Exception as e:
                log.error(f"Slash command sync failed: {e}")

    async def _health_heartbeat_loop(self):
        """Refresh the health file every 60s for as long as the client is
        connected, so file age actually reflects current liveness."""
        try:
            while not self.is_closed():
                await asyncio.sleep(60)
                if not self.is_closed():
                    await self.write_health_heartbeat()
        except asyncio.CancelledError:
            pass

    async def _deferred_poke_flush_loop(self):
        """Periodically retry spooled messages for as long as the client is
        connected. 30s interval matches issue #88's acceptance test shape
        ("stop the agent server, send a message, start it again within
        five minutes, pass = the reply arrives without resending") with
        room to spare."""
        try:
            while not self.is_closed():
                await asyncio.sleep(DEFERRED_POKE_FLUSH_INTERVAL_SEC)
                if not self.is_closed():
                    await self._flush_deferred_pokes()
        except asyncio.CancelledError:
            pass

    async def _flush_deferred_pokes(self):
        """Retry every spooled payload once. Successes are deleted; still-
        failing ones are left for the next pass; anything older than
        DEFERRED_POKE_MAX_AGE_SEC gets moved to dead/ instead of retried
        forever — a permanently malformed payload shouldn't spin here for
        the life of the container."""
        if not DEFERRED_POKE_DIR.is_dir():
            return
        files = sorted(DEFERRED_POKE_DIR.glob("*.json"))
        if not files:
            return

        now = time.time()
        for f in files:
            try:
                record = json.loads(f.read_text())
            except Exception as e:
                log.error(f"Deferred poke {f.name} unreadable, moving to dead/: {e}")
                self._move_deferred_poke_to_dead(f)
                continue

            ok, detail = await self._post_to_agent_server(record.get("payload", {}))
            if ok:
                log.info(f"Deferred poke {f.name} delivered on retry")
                f.unlink(missing_ok=True)
                continue

            age_sec = now - record.get("spooled_at", now)
            if age_sec > DEFERRED_POKE_MAX_AGE_SEC:
                log.error(
                    f"Deferred poke {f.name} exceeded {DEFERRED_POKE_MAX_AGE_SEC}s "
                    f"({age_sec:.0f}s), giving up: {detail}"
                )
                self._move_deferred_poke_to_dead(f)
            else:
                log.info(f"Deferred poke {f.name} still failing ({detail}), will retry")

    def _move_deferred_poke_to_dead(self, path: Path):
        try:
            DEFERRED_POKE_DEAD_DIR.mkdir(parents=True, exist_ok=True)
            path.rename(DEFERRED_POKE_DEAD_DIR / path.name)
        except Exception as e:
            log.error(f"Failed to move {path.name} to dead/: {e}")

    async def handle_sys_command(self, message: discord.Message) -> bool:
        """Intercept /sys owner commands before any normal routing.
        Returns True if this message was a /sys command (caller returns
        immediately after). Design from Amos (Mike's Karakos instance),
        2026-08-06: OWNER_DISCORD_ID existed in this file already but
        nothing ever checked it — this is what it's for. A session-clear
        command has to be reachable even when the agent it targets is
        completely wedged, which is exactly why this is handled here,
        in relay, rather than routed through the normal message queue
        like everything else — a wedged session can't process the
        command that unwedges it."""
        if OWNER_DISCORD_ID == 0 or message.author.id != OWNER_DISCORD_ID:
            return False
        content = message.content.strip()
        if not content.startswith("/sys"):
            return False

        parts = content.split()
        cmd = parts[1] if len(parts) > 1 else "status"
        target_agent = parts[2] if len(parts) > 2 else None
        if not target_agent:
            target_agent = next(
                (name for name, cfg in agent_config.items()
                 if cfg.get("discord_bot_token_env")),
                next(iter(agent_config), None)
            )

        reply = await self._run_sys_command(cmd, target_agent)
        await message.channel.send(reply)
        return True

    async def _run_sys_command(self, cmd: str, agent: Optional[str]) -> str:
        """Talk to agent-server's existing /agents endpoints directly —
        not adding a mechanism, just a Discord surface for what already
        exists (GET /agents, POST /agents/{name}/reset|reload)."""
        headers = {"Authorization": f"Bearer {AGENT_SERVER_TOKEN}"}
        try:
            if cmd == "status":
                async with self.http_session.get(
                    f"{AGENT_SERVER_URL}/agents", headers=headers
                ) as resp:
                    data = await resp.json()
                lines = []
                for a in data.get("agents", []):
                    line = f"`{a['name']}`: {a['state']}"
                    # Anthropic's own live rate-limit signal, added
                    # 2026-08-06 — see agent-server.py's
                    # _record_rate_limit_event(). Empty until that
                    # agent's subprocess has completed a turn since the
                    # server last started.
                    rl = a.get("rate_limit") or {}
                    if rl:
                        resets = rl.get("resetsAt")
                        resets_str = (
                            datetime.fromtimestamp(resets).strftime("%H:%M")
                            if isinstance(resets, (int, float)) else "?"
                        )
                        overage = " [OVERAGE]" if rl.get("isUsingOverage") else ""
                        line += (
                            f" — {rl.get('status', '?')} ({rl.get('rateLimitType', '?')}, "
                            f"resets {resets_str}){overage}"
                        )
                    lines.append(line)
                return "**/sys status**\n" + "\n".join(lines) if lines else "No agents found."

            if not agent:
                return "**/sys**: no agent configured to target"

            if cmd == "clear":
                async with self.http_session.post(
                    f"{AGENT_SERVER_URL}/agents/{agent}/reset", headers=headers
                ) as resp:
                    ok = resp.status == 200
                return (f"**/sys clear** `{agent}`: "
                        f"{'done — fresh session' if ok else f'failed ({resp.status})'}")

            if cmd == "reload":
                async with self.http_session.post(
                    f"{AGENT_SERVER_URL}/agents/{agent}/reload", headers=headers
                ) as resp:
                    ok = resp.status == 200
                return (f"**/sys reload** `{agent}`: "
                        f"{'done — session preserved' if ok else f'failed ({resp.status})'}")

            return f"Unknown /sys command: `{cmd}`. Known: status, clear, reload"
        except Exception as e:
            return f"**/sys {cmd}** failed: {e}"

    async def on_message(self, message: discord.Message):
        """Route Discord message to agent"""
        # Ignore own messages
        if message.author == self.user:
            return

        # Ignore messages from other servers
        if message.guild and str(message.guild.id) not in self.server_ids:
            return

        # Capture message
        await self.capture_message(message)

        # /sys owner commands, intercepted before any normal routing so
        # they work even against a wedged agent. See handle_sys_command.
        if await self.handle_sys_command(message):
            return

        # (Removed 2026-08-06: a literal trailing "∎" used to be checked
        # here as an anti-loop termination token. Superseded by the handoff
        # protocol's `reply: none` field — Amos's own framing when he
        # pitched it was "it's your ∎, made checkable" — and neither side
        # has actually appended a bare ∎ since that landed. Real loop
        # prevention lives in reply_gate.py's Tier 1/2 wake logic plus the
        # handoff envelope's reply field, both of which stay in effect
        # below; this was dead code checking for a signal nobody produces
        # anymore. Ian approved the removal.)

        # Reply gate for channels that opt into graduated wake logic instead
        # of the blunt always-on / mention-only config split. Tier 1
        # (@mention, reply-to-self) is free and always wakes. Everything
        # else is scored by a cheap model, cooldown-gated, biased to
        # silence — see reply_gate.py.
        channel_name = self.get_channel_name(str(message.channel.id))
        channel_config = (
            channels_config.get("channels", {}).get(channel_name, {})
            if channel_name else {}
        )
        if channel_config.get("gate_mode") == "tier2" and self.gate:
            if not message.author.bot:
                self.gate.note_human_message(str(message.channel.id))

            # Handoff envelope (handoff.py): a sender-declared `reply` field
            # that short-circuits the gate entirely when present and valid.
            # required -> forced wake, free, same tier as an @mention.
            # none -> forced quiet, skips even the Tier 2 scorer call, which
            # a plain gate decline doesn't save. optional, missing, or
            # malformed -> no envelope, fall through to the normal gate
            # unchanged. Proposed by Amos 2026-08-05; see handoff.py for the
            # measured reasoning (a full turn costs ~1000x a compressed
            # message, so the only real lever is whether a turn happens).
            envelope = parse_handoff(message.content or "")
            if envelope and envelope.reply == "none":
                # Sender's declared intent still wins — silence stays free,
                # no scorer call either way — but a '?' in the prose next to
                # reply:none is a plausible mis-declaration. Free to catch,
                # so it's caught. Amos's addition, 2026-08-05.
                if "?" in (message.content or ""):
                    log.warning(
                        f"[gate] {channel_name} handoff: reply=none but "
                        f"content has '?' - possible sender mis-declare, "
                        f"staying quiet anyway"
                    )
                else:
                    log.info(
                        f"[gate] {channel_name} handoff: reply=none -> "
                        f"quiet (scorer skipped)"
                    )
                return
            if envelope and envelope.reply == "required":
                decision = Decision(
                    True, "handoff", "reply: required",
                    channel_id=str(message.channel.id),
                )
            else:
                gate_msg = GateMessage(
                    channel_id=str(message.channel.id),
                    author_id=str(message.author.id),
                    content=message.content or "",
                    mentions_self=self.user in message.mentions,
                    is_reply_to_self=await self._is_reply_to_self(message),
                    author_is_bot=message.author.bot,
                )
                decision = self.gate.evaluate(gate_msg)
                if decision.needs_score:
                    context = await self._recent_context(message.channel)
                    score = await self.score_with_cheap_model(
                        context, message.author.display_name
                    )
                    decision = self.gate.resolve(decision, score)

            log.info(
                f"[gate] {channel_name} {decision.tier}: {decision.reason} "
                f"-> {'WAKE' if decision.wake else 'quiet'}"
            )
            if not decision.wake:
                return

        # Determine target agent
        target_agent = None

        # Check for bot mention
        for mention in message.mentions:
            if mention.bot and mention.id in discord_id_to_agent:
                target_agent = discord_id_to_agent[mention.id]
                break

        # Fall back to channel default agent
        if not target_agent:
            channel_name = self.get_channel_name(str(message.channel.id))
            if channel_name:
                channel_config = channels_config.get("channels", {}).get(channel_name, {})
                target_agent = channel_config.get("default_agent")

        if not target_agent:
            return  # No routing

        # Send to agent server
        await self.send_to_agent_server(message, target_agent)

    def get_channel_name(self, channel_id: str) -> Optional[str]:
        """Get channel name from ID"""
        for name, config in channels_config.get("channels", {}).items():
            if config.get("id") == channel_id:
                return name
        return None

    async def _is_reply_to_self(self, message: discord.Message) -> bool:
        """True if message is a Discord reply to one of this bot's messages.
        A fetch failure fails toward False (not a reply) rather than raising,
        so a transient API hiccup drops one gate signal, not the message."""
        if message.reference is None:
            return False
        resolved = message.reference.resolved
        if isinstance(resolved, discord.Message):
            return resolved.author.id == self.user.id
        if message.reference.message_id is None:
            return False
        try:
            fetched = await message.channel.fetch_message(message.reference.message_id)
            return fetched.author.id == self.user.id
        except Exception:
            return False

    async def _recent_context(self, channel, limit: int = 12) -> str:
        """Recent channel history, oldest-first, for the Tier 2 scorer prompt."""
        history = [m async for m in channel.history(limit=limit)]
        return "\n".join(
            f"{m.author.display_name}: {m.content}" for m in reversed(history)
        )

    async def score_with_cheap_model(self, context: str, author: str) -> float:
        """Tier 2 scorer: one-shot Haiku call, no session state. Any failure
        (timeout, bad output, missing binary) returns 0.0 — a broken
        classifier must fail toward silence, never toward waking on
        everything, per Amos's design."""
        prompt = SCORER_PROMPT.format(agent="Marvin", context=context, author=author)
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "-p",
                "--model", "haiku",
                "--max-turns", "1",
                "--dangerously-skip-permissions",
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
            raw = stdout.decode(errors="ignore")
            match = re.search(r"[01]?\.\d+|\b[01]\b", raw)
            return float(match.group(0)) if match else 0.0
        except Exception as e:
            log.error(f"Gate scorer failed, defaulting to silence: {e}")
            return 0.0

    async def _post_to_agent_server(self, payload: dict) -> tuple[bool, str]:
        """POST one message payload to the agent server. Returns (success,
        detail) where detail is a short status/error string for logging.
        Shared by the live send path and the deferred-poke flush loop so
        both retry the exact same request shape."""
        try:
            async with self.http_session.post(
                f"{AGENT_SERVER_URL}/message",
                json=payload,
                headers={"Authorization": f"Bearer {AGENT_SERVER_TOKEN}"}
            ) as resp:
                if resp.status == 202:
                    return True, "202"
                text = await resp.text()
                return False, f"{resp.status}: {text[:200]}"
        except Exception as e:
            return False, str(e)

    def _spool_deferred_poke(self, payload: dict, reason: str):
        """Write a failed payload to disk for later retry instead of
        dropping it. Filename embeds a timestamp so the flush loop can
        enforce DEFERRED_POKE_MAX_AGE_SEC without needing to open every
        file just to sort them."""
        try:
            DEFERRED_POKE_DIR.mkdir(parents=True, exist_ok=True)
            now = time.time()
            fname = f"{now:.6f}-{payload.get('message_id', 'unknown')}.json"
            record = {"spooled_at": now, "reason": reason, "payload": payload}
            (DEFERRED_POKE_DIR / fname).write_text(json.dumps(record))
            log.warning(
                f"Spooled message {payload.get('message_id')} for "
                f"{payload.get('agent')} after failure: {reason}"
            )
        except Exception as e:
            log.error(f"Failed to spool deferred poke — message lost: {e}")

    async def send_to_agent_server(self, message: discord.Message, agent: str):
        """Send message to agent server. On failure (non-202 response or a
        connection error — agent server down, rate-limited, etc.), spool
        it to DEFERRED_POKE_DIR instead of dropping it silently. Task #9,
        2026-08-06 — matches upstream karakos-package issue #88, which
        names a real dropped message on this install (2026-08-05, a 429
        with no retry) as the reproduction case."""
        channel_name = self.get_channel_name(str(message.channel.id))
        if not channel_name:
            channel_name = "unknown"

        payload = {
            "agent": agent,
            "channel": channel_name,
            "channel_id": str(message.channel.id),
            "server": "discord",
            "author": message.author.display_name,
            "author_id": str(message.author.id),
            "is_bot": message.author.bot,
            "content": message.content,
            "message_id": str(message.id),
            "mentions_agent": any(m.id in discord_id_to_agent for m in message.mentions)
        }

        ok, detail = await self._post_to_agent_server(payload)
        if ok:
            log.info(f"Queued message for {agent} from {message.author.display_name}")
        else:
            log.error(f"Agent server error, spooling for retry: {detail}")
            self._spool_deferred_poke(payload, detail)

    async def capture_message(self, message: discord.Message):
        """Capture message to JSONL"""
        channel_name = self.get_channel_name(str(message.channel.id))

        entry = {
            "v": 1,
            "ts": datetime.now().isoformat(),
            "channel": "discord",
            "channel_id": str(message.channel.id),
            "channel_name": channel_name or "unknown",
            "author_id": str(message.author.id),
            "author_name": message.author.display_name,
            "is_bot": message.author.bot,
            "content": message.content,
            "message_id": str(message.id)
        }

        # Write to daily JSONL
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = MESSAGES_DIR / f"messages-{date_str}.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    async def write_health_heartbeat(self):
        """Write health heartbeat"""
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HEALTH_FILE, "w") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "status": "healthy"
            }, f)

    async def close(self):
        """Cleanup on shutdown"""
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
        if self.http_session:
            await self.http_session.close()
        await super().close()

# =============================================================================
# Dispatch Adapter
# =============================================================================

class DispatchAdapter:
    """Watch inbox directories and invoke builder/reviewer scripts"""

    def __init__(self):
        self.running = False
        self.task = None

        # Initialize semaphores
        dispatch_semaphores["builder"] = asyncio.Semaphore(MAX_CONCURRENT_BUILDERS)
        dispatch_semaphores["reviewer"] = asyncio.Semaphore(MAX_CONCURRENT_REVIEWERS)

    async def start(self):
        """Start dispatch polling loop"""
        self.running = True
        self.task = asyncio.create_task(self.poll_loop())
        log.info("Dispatch adapter started")

    async def stop(self):
        """Stop dispatch adapter"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        # Wait for active dispatches
        if active_dispatches:
            log.info(f"Waiting for {len(active_dispatches)} active dispatches to complete")
            await asyncio.gather(*active_dispatches.values(), return_exceptions=True)

    async def poll_loop(self):
        """Poll inbox directories for new briefs"""
        while self.running:
            try:
                await self.check_inboxes()
                await asyncio.sleep(DISPATCH_POLL_INTERVAL)
            except Exception as e:
                log.error(f"Dispatch poll error: {e}")
                await asyncio.sleep(DISPATCH_POLL_INTERVAL)

    async def check_inboxes(self):
        """Check inbox directories for new briefs"""
        for agent_type in ["builder", "reviewer"]:
            inbox_dir = DISPATCH_INBOX_DIR / agent_type
            if not inbox_dir.exists():
                continue

            # Find brief files
            briefs = sorted(inbox_dir.glob("*.md"), key=lambda p: p.stat().st_mtime)

            for brief_file in briefs:
                # Check if already dispatched
                if brief_file.stem in active_dispatches:
                    continue

                # Try to acquire semaphore (non-blocking)
                semaphore = dispatch_semaphores.get(agent_type)
                if semaphore and semaphore._value > 0:
                    # Dispatch
                    task = asyncio.create_task(self.dispatch(agent_type, brief_file))
                    active_dispatches[brief_file.stem] = task
                    log.info(f"Dispatched {agent_type}: {brief_file.name}")

    async def dispatch(self, agent_type: str, brief_file: Path):
        """Dispatch brief to agent"""
        semaphore = dispatch_semaphores.get(agent_type)
        if not semaphore:
            return

        async with semaphore:
            try:
                # Read brief
                with open(brief_file) as f:
                    brief_content = f.read()

                # Parse frontmatter
                metadata = self.parse_frontmatter(brief_content)
                requester = metadata.get("requester", "unknown")
                callback_channel = metadata.get("callback_channel", "general")

                # Determine invoke script
                invoke_script = WORKSPACE_ROOT / "bin" / f"invoke-{agent_type}.sh"
                if not invoke_script.exists():
                    log.error(f"Invoke script not found: {invoke_script}")
                    return

                # Invoke script
                timeout = DISPATCH_TIMEOUTS.get(agent_type, 21600)
                log.info(f"Invoking {agent_type} for {brief_file.name} (timeout: {timeout}s)")

                proc = await asyncio.create_subprocess_exec(
                    str(invoke_script),
                    str(brief_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                    returncode = proc.returncode

                    if returncode == 0:
                        log.info(f"{agent_type} completed: {brief_file.name}")
                    else:
                        log.error(f"{agent_type} failed with code {returncode}: {brief_file.name}")
                        log.error(f"stderr: {stderr.decode()}")

                except asyncio.TimeoutError:
                    log.error(f"{agent_type} timed out: {brief_file.name}")
                    proc.kill()
                    await proc.wait()

                # Archive brief
                archive_dir = brief_file.parent / "archive"
                archive_dir.mkdir(exist_ok=True)
                brief_file.rename(archive_dir / brief_file.name)

            finally:
                # Remove from active dispatches
                active_dispatches.pop(brief_file.stem, None)

    def parse_frontmatter(self, content: str) -> Dict:
        """Parse YAML frontmatter from brief"""
        if not content.startswith("---"):
            return {}

        lines = content.split("\n")
        frontmatter_lines = []
        in_frontmatter = False

        for i, line in enumerate(lines):
            if i == 0 and line.strip() == "---":
                in_frontmatter = True
                continue
            if in_frontmatter:
                if line.strip() == "---":
                    break
                frontmatter_lines.append(line)

        # Simple key: value parser (not full YAML)
        metadata = {}
        for line in frontmatter_lines:
            if ":" in line:
                key, _, value = line.partition(":")
                metadata[key.strip()] = value.strip()

        return metadata

# =============================================================================
# Main
# =============================================================================

async def main():
    """Main relay service"""
    _acquire_singleton_lock("relay")
    log.info("Karakos relay starting")

    # Load config
    load_config()

    # Start dispatch adapter
    dispatch = DispatchAdapter()
    await dispatch.start()

    # Get primary agent's Discord token
    primary_agent = None
    for agent_name, config in agent_config.items():
        token_env = config.get("discord_bot_token_env")
        if token_env and os.environ.get(token_env):
            primary_agent = agent_name
            break

    if not primary_agent:
        log.warning("No Discord tokens configured, Discord adapter disabled")
        # Run dispatch-only mode
        try:
            while True:
                await asyncio.sleep(60)
        except KeyboardInterrupt:
            pass
        finally:
            await dispatch.stop()
        return

    # Start Discord bot
    token = os.environ.get(agent_config[primary_agent]["discord_bot_token_env"])
    discord_client = DiscordAdapter()

    try:
        # Run Discord bot (blocks until closed)
        await discord_client.start(token)
    except KeyboardInterrupt:
        log.info("Shutdown signal received")
    finally:
        await discord_client.close()
        await dispatch.stop()
        log.info("Relay shutdown complete")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
