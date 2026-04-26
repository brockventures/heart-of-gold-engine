#!/usr/bin/env python3
"""
Karakos Agent Server — Persistent Subprocess Architecture

Accepts messages via HTTP, queues to SQLite, sends to persistent claude
subprocess via stdin (stream-json), posts responses to Discord.

Port: 18791 (configurable via AGENT_SERVER_PORT env var)
"""

import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any
from logging.handlers import RotatingFileHandler

import aiohttp
import aiosqlite
from aiohttp import web

# =============================================================================
# Configuration
# =============================================================================

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
PORT = int(os.environ.get("AGENT_SERVER_PORT", "18791"))
DB_PATH = WORKSPACE_ROOT / "data" / "memory" / "agent-server.db"
AGENTS_CONFIG_PATH = WORKSPACE_ROOT / "config" / "agents.json"
CHANNELS_CONFIG_PATH = WORKSPACE_ROOT / "config" / "channels.json"
STREAM_LOG_DIR = WORKSPACE_ROOT / "logs" / "agent-streams"
AGENT_SERVER_TOKEN = os.environ.get("AGENT_SERVER_TOKEN", "")
OWNER_DISCORD_ID = os.environ.get("OWNER_DISCORD_ID", "0")

# Cost limits
COST_DAILY_LIMIT = float(os.environ.get("COST_DAILY_LIMIT", "25.00"))
COST_MONTHLY_LIMIT = float(os.environ.get("COST_MONTHLY_LIMIT", "500.00"))
COST_WARNING_THRESHOLD = float(os.environ.get("COST_WARNING_THRESHOLD", "0.75"))

# Queue limits
QUEUE_DEPTH_LIMIT = 50
TYPING_INTERVAL = 8  # seconds

# Processing states
STATUS_QUEUED = 0
STATUS_IN_PROGRESS = 1
STATUS_COMPLETE = 2
STATUS_CRASHED = 3
STATUS_SKIPPED = 4

# Session persistence
SUMMARY_DIR = WORKSPACE_ROOT / "logs" / "session-summaries"
LAST_SUMMARY_TEMPLATE = WORKSPACE_ROOT / "data" / "last-session-summary-{agent}.md"

# Logging
STREAM_LOG_DIR.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("agent-server")
log.setLevel(logging.INFO)
handler = RotatingFileHandler(
    WORKSPACE_ROOT / "logs" / "agent-server.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=7
)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
log.addHandler(handler)

# Also log to console
console = logging.StreamHandler()
console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(console)

# Regex patterns
THINKING_BLOCK_RE = re.compile(r"<thinking>(.*?)</thinking>", re.DOTALL)

# =============================================================================
# Global State
# =============================================================================

db: Optional[aiosqlite.Connection] = None
http_session: Optional[aiohttp.ClientSession] = None
agent_config: Dict[str, Dict[str, Any]] = {}
channels_config: Dict[str, Any] = {}
agent_processes: Dict[str, asyncio.subprocess.Process] = {}
agent_locks: Dict[str, asyncio.Lock] = {}
agent_states: Dict[str, str] = {}
response_buffers: Dict[str, str] = {}
agent_last_cost: Dict[str, float] = {}
agent_sessions: Dict[str, str] = {}
typing_tasks: Dict[str, asyncio.Task] = {}
agent_todo_lists: Dict[str, List[Dict]] = {}
active_todo_messages: Dict[str, Dict] = {}

# Discord token mapping
AGENT_TOKENS: Dict[str, str] = {}
DISCORD_ID_TO_AGENT: Dict[int, str] = {}

# Graceful shutdown flag
shutting_down = False

# =============================================================================
# Database Schema
# =============================================================================

async def init_db():
    """Initialize database schema"""
    global db
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row

    # Message queue table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS message_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            channel TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            server TEXT DEFAULT 'discord',
            author TEXT NOT NULL,
            author_id TEXT DEFAULT '0',
            is_bot INTEGER DEFAULT 0,
            content TEXT NOT NULL,
            message_id TEXT UNIQUE NOT NULL,
            mentions_agent INTEGER DEFAULT 0,
            processed INTEGER DEFAULT 0,
            response TEXT,
            discord_response_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processing_started_at TIMESTAMP,
            processed_at TIMESTAMP
        )
    """)

    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_queue_agent
        ON message_queue(agent, processed, created_at)
    """)

    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_queue_pending
        ON message_queue(processed) WHERE processed = 0
    """)

    # Sessions table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            agent TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            input_tokens INTEGER DEFAULT 0,
            compaction_count INTEGER DEFAULT 0,
            last_compacted TIMESTAMP
        )
    """)

    # Cost events table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS cost_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            cost_delta REAL,
            session_total REAL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            duration_ms REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await db.commit()
    log.info("Database initialized")

# =============================================================================
# Configuration Loading
# =============================================================================

async def load_config():
    """Load agent and channel configuration from JSON files"""
    global agent_config, channels_config, AGENT_TOKENS, DISCORD_ID_TO_AGENT

    # Load agents config
    if AGENTS_CONFIG_PATH.exists():
        with open(AGENTS_CONFIG_PATH) as f:
            config_data = json.load(f)
            agent_config = config_data.get("agents", {})
            log.info(f"Loaded configuration for {len(agent_config)} agents")
    else:
        log.error(f"Agents config not found: {AGENTS_CONFIG_PATH}")
        agent_config = {}

    # Load channels config
    if CHANNELS_CONFIG_PATH.exists():
        with open(CHANNELS_CONFIG_PATH) as f:
            channels_config = json.load(f)
            log.info(f"Loaded {len(channels_config.get('channels', {}))} channel mappings")
    else:
        log.warning(f"Channels config not found: {CHANNELS_CONFIG_PATH}")
        channels_config = {}

    # Build Discord token map
    for agent_name, config in agent_config.items():
        token_env_var = config.get("discord_bot_token_env")
        if token_env_var:
            token = os.environ.get(token_env_var, "")
            if token:
                AGENT_TOKENS[agent_name] = token
                bot_id_env = config.get("discord_bot_id_env")
                if bot_id_env:
                    bot_id = os.environ.get(bot_id_env)
                    if bot_id:
                        DISCORD_ID_TO_AGENT[int(bot_id)] = agent_name

# =============================================================================
# Session Management
# =============================================================================

async def get_or_create_session(agent: str) -> str:
    """Get existing session ID or create new one"""
    async with db.execute(
        "SELECT session_id FROM sessions WHERE agent = ?", (agent,)
    ) as cursor:
        row = await cursor.fetchone()
        if row:
            return row["session_id"]

    # Create new session
    session_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO sessions (agent, session_id) VALUES (?, ?)",
        (agent, session_id)
    )
    await db.commit()
    log.info(f"Created new session for {agent}: {session_id}")
    return session_id

async def clear_session(agent: str):
    """Clear agent session and create new ID"""
    session_id = str(uuid.uuid4())
    await db.execute(
        """
        INSERT INTO sessions (agent, session_id, input_tokens, compaction_count)
        VALUES (?, ?, 0, 0)
        ON CONFLICT(agent) DO UPDATE SET
            session_id = ?,
            input_tokens = 0,
            compaction_count = 0,
            last_compacted = CURRENT_TIMESTAMP
        """,
        (agent, session_id, session_id)
    )
    await db.commit()
    agent_last_cost.pop(agent, None)
    log.info(f"Cleared session for {agent}, new ID: {session_id}")

async def update_session_tokens(agent: str, input_tokens: int):
    """Update session token count"""
    await db.execute(
        "UPDATE sessions SET input_tokens = ? WHERE agent = ?",
        (input_tokens, agent)
    )
    await db.commit()

# =============================================================================
# Session Persistence (Summary and Restore)
# =============================================================================

async def load_last_session(agent: str) -> Dict[str, Any]:
    """Load last session summary if available and recent"""
    summary_path = Path(str(LAST_SUMMARY_TEMPLATE).format(agent=agent))

    if not summary_path.exists():
        return {"status": "not_found"}

    # Check age
    mtime = summary_path.stat().st_mtime
    age_hours = (time.time() - mtime) / 3600

    if age_hours > 24:
        return {"status": "stale", "age_hours": age_hours}

    with open(summary_path) as f:
        summary = f.read()

    return {"status": "success", "summary": summary, "age_hours": age_hours}

# =============================================================================
# Agent Subprocess Management
# =============================================================================

def load_persona_files(agent: str) -> str:
    """Load and concatenate persona files for agent"""
    persona_dir = WORKSPACE_ROOT / "agents" / agent / "persona"
    if not persona_dir.exists():
        return ""

    persona_parts = []
    for file in sorted(persona_dir.glob("*.md")):
        with open(file) as f:
            content = f.read().strip()
            if content:
                persona_parts.append(content)

    return "\n\n".join(persona_parts)

async def start_agent_subprocess(agent: str):
    """Start persistent Claude subprocess for agent"""
    config = agent_config.get(agent, {})
    if not config:
        log.error(f"No config found for agent: {agent}")
        return

    session_id = await get_or_create_session(agent)
    system_prompt_path = WORKSPACE_ROOT / config.get("system_prompt", "")

    if not system_prompt_path.exists():
        log.error(f"System prompt not found for {agent}: {system_prompt_path}")
        return

    # The CLI's --system-prompt flag takes the prompt string, not a file
    # path. Read the file contents here.
    try:
        system_prompt_text = system_prompt_path.read_text()
    except Exception as e:
        log.error(f"Failed to read system prompt for {agent}: {e}")
        return

    # Load persona
    persona_content = load_persona_files(agent)

    # Load last session summary if available
    last_session = await load_last_session(agent)
    if last_session["status"] == "success":
        log.info(f"Injecting session summary for {agent} (age: {last_session['age_hours']:.1f}h)")
        persona_content = f"[SESSION RESET]\n\n{last_session['summary']}\n\n{persona_content}"

    # Build command
    cmd = [
        "claude", "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--model", config.get("model", "sonnet"),
        "--max-turns", str(config.get("max_turns", 200)),
        "--verbose",
        "--dangerously-skip-permissions",
        "--session-id", session_id,
        "--system-prompt", system_prompt_text,
    ]

    if persona_content:
        cmd.extend(["--append-system-prompt", persona_content])

    # Add disallowed tools
    disallowed = config.get("disallowed_tools", [])
    for pattern in disallowed:
        cmd.extend(["--disallowedTools", pattern])

    # Add allowed tools if specified
    allowed = config.get("allowed_tools")
    if allowed:
        cmd.extend(["--allowedTools", ",".join(allowed)])

    log.info(f"Starting {agent} subprocess (model={config.get('model')}, session={session_id[:8]})")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        agent_processes[agent] = proc
        agent_states[agent] = "IDLE"
        agent_sessions[agent] = session_id

        # Start stderr reader
        asyncio.create_task(stderr_reader(agent, proc))

        log.info(f"{agent} subprocess started (PID {proc.pid})")
    except Exception as e:
        log.error(f"Failed to start {agent}: {e}")
        agent_states[agent] = "ERROR_RECOVERY"

async def stderr_reader(agent: str, proc: asyncio.subprocess.Process):
    """Read and log stderr from agent subprocess"""
    try:
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            msg = line.decode().strip()
            if msg:
                log.warning(f"{agent} stderr: {msg}")
    except Exception as e:
        log.error(f"stderr reader error for {agent}: {e}")

async def kill_agent_subprocess(agent: str):
    """Terminate agent subprocess"""
    proc = agent_processes.get(agent)
    if not proc:
        return

    log.info(f"Killing {agent} subprocess (PID {proc.pid})")
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        log.warning(f"{agent} didn't terminate, sending SIGKILL")
        proc.kill()
        await proc.wait()

    agent_processes.pop(agent, None)
    log.info(f"{agent} subprocess terminated")

async def restart_agent(agent: str):
    """Restart agent subprocess"""
    log.info(f"Restarting {agent}")
    await kill_agent_subprocess(agent)
    await clear_session(agent)
    agent_last_cost.pop(agent, None)
    response_buffers[agent] = ""
    await start_agent_subprocess(agent)

# =============================================================================
# Cost Tracking
# =============================================================================

async def post_cost_update(agent: str, metadata: Dict):
    """Post cost update to Discord and database"""
    session_total = metadata.get("total_cost_usd", 0.0)
    input_tokens = metadata.get("input_tokens", 0)
    output_tokens = metadata.get("output_tokens", 0)
    duration_ms = metadata.get("duration_ms", 0)

    # Calculate delta
    last_cost = agent_last_cost.get(agent, 0.0)
    cost_delta = session_total - last_cost
    agent_last_cost[agent] = session_total

    # Store in database
    await db.execute(
        """
        INSERT INTO cost_events (agent, cost_delta, session_total, input_tokens, output_tokens, duration_ms)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (agent, cost_delta, session_total, input_tokens, output_tokens, duration_ms)
    )
    await db.commit()

    # Post to Discord cost channel (if configured)
    cost_channel_id = channels_config.get("channels", {}).get("cost", {}).get("id")
    if cost_channel_id and cost_delta > 0.001:
        duration_s = duration_ms / 1000.0
        message = f"`{agent}` +${cost_delta:.2f} (session: ${session_total:.2f}) • {input_tokens:,}in/{output_tokens:,}out • {duration_s:.1f}s"
        await post_to_discord(agent, cost_channel_id, message)

async def check_cost_limits(author_id: str) -> Dict[str, Any]:
    """Check if cost limits have been exceeded"""
    if author_id == OWNER_DISCORD_ID:
        return {"exceeded": False, "reason": "owner"}

    # Get daily cost
    async with db.execute(
        """
        SELECT SUM(cost_delta) as total
        FROM cost_events
        WHERE timestamp > datetime('now', '-1 day')
        """
    ) as cursor:
        row = await cursor.fetchone()
        daily_total = row["total"] or 0.0

    if daily_total >= COST_DAILY_LIMIT:
        return {"exceeded": True, "reason": "daily", "total": daily_total, "limit": COST_DAILY_LIMIT}

    # Get monthly cost
    async with db.execute(
        """
        SELECT SUM(cost_delta) as total
        FROM cost_events
        WHERE timestamp > datetime('now', '-30 days')
        """
    ) as cursor:
        row = await cursor.fetchone()
        monthly_total = row["total"] or 0.0

    if monthly_total >= COST_MONTHLY_LIMIT:
        return {"exceeded": True, "reason": "monthly", "total": monthly_total, "limit": COST_MONTHLY_LIMIT}

    return {"exceeded": False, "daily": daily_total, "monthly": monthly_total}

# =============================================================================
# Discord Integration
# =============================================================================

MAX_DISCORD_MSG_LEN = 2000

def split_discord_message(text: str, max_length: int = MAX_DISCORD_MSG_LEN) -> List[str]:
    """Split text into Discord-compatible chunks (max 2000 chars per message)"""
    if len(text) <= max_length:
        return [text]

    chunks = []
    paragraphs = text.split('\n\n')
    current_chunk = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_length:
            # Split oversized paragraphs on newlines
            lines = paragraph.split('\n')
            for line in lines:
                if len(current_chunk) + len(line) + 2 > max_length:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = line
                else:
                    current_chunk += ('\n' if current_chunk else '') + line
        else:
            if len(current_chunk) + len(paragraph) + 2 > max_length:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = paragraph
            else:
                current_chunk += ('\n\n' if current_chunk else '') + paragraph

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks if chunks else [text]

async def post_to_discord(agent: str, channel_id: str, content: str, reply_to: Optional[str] = None) -> Optional[str]:
    """Post message to Discord as agent, splitting if over 2000 chars"""
    global http_session

    # Skip posting if channel_id is "0" (silent mode)
    if channel_id == "0":
        return None

    # Get agent's Discord token, fallback to primary agent
    token = AGENT_TOKENS.get(agent)
    if not token:
        # Use first available token as fallback
        if AGENT_TOKENS:
            token = list(AGENT_TOKENS.values())[0]
            content = f"[{agent}] {content}"
        else:
            log.warning(f"No Discord tokens configured, cannot post for {agent}")
            return None

    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json"
    }

    chunks = split_discord_message(content)
    last_msg_id = None

    for chunk in chunks:
        payload = {"content": chunk}
        # Only reply-reference the first chunk
        if reply_to and last_msg_id is None:
            payload["message_reference"] = {"message_id": reply_to}

        try:
            async with http_session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    last_msg_id = data.get("id")
                elif resp.status == 429:
                    retry_after = (await resp.json()).get("retry_after", 1)
                    log.warning(f"Rate limited posting to {channel_id}, retry after {retry_after}s")
                    await asyncio.sleep(retry_after)
                    # Retry this chunk
                    async with http_session.post(url, headers=headers, json=payload) as retry_resp:
                        if retry_resp.status == 200:
                            data = await retry_resp.json()
                            last_msg_id = data.get("id")
                else:
                    log.error(f"Discord API error {resp.status}: {await resp.text()}")
        except Exception as e:
            log.error(f"Error posting to Discord: {e}")

    return last_msg_id

async def start_typing(agent: str, channel_id: str):
    """Start typing indicator in Discord channel"""
    if channel_id == "0" or channel_id in typing_tasks:
        return

    async def typing_loop():
        token = AGENT_TOKENS.get(agent)
        if not token and AGENT_TOKENS:
            token = list(AGENT_TOKENS.values())[0]
        if not token:
            return

        url = f"https://discord.com/api/v10/channels/{channel_id}/typing"
        headers = {"Authorization": f"Bot {token}"}

        while True:
            try:
                async with http_session.post(url, headers=headers) as resp:
                    if resp.status != 204:
                        break
                await asyncio.sleep(TYPING_INTERVAL)
            except Exception:
                break

    task = asyncio.create_task(typing_loop())
    typing_tasks[channel_id] = task

async def stop_typing(channel_id: str):
    """Stop typing indicator"""
    task = typing_tasks.pop(channel_id, None)
    if task:
        task.cancel()

# =============================================================================
# Message Processing
# =============================================================================

async def send_to_agent(agent: str, content: str, message_ids: List[str]):
    """Send message to agent subprocess"""
    proc = agent_processes.get(agent)
    if not proc or not proc.stdin:
        log.error(f"No subprocess for {agent}")
        return

    agent_states[agent] = "PROCESSING"
    response_buffers[agent] = ""

    # Send message — Claude Code stream-json input envelope.
    # Format: {"type": "user", "message": {"role": "user", "content": <str>}}
    # The bare {"type":"user","content":...} form is rejected by the SDK.
    msg = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": content},
    }) + "\n"
    try:
        proc.stdin.write(msg.encode())
        await proc.stdin.drain()
        log.info(f"Sent message to {agent} ({len(message_ids)} queued messages)")
    except Exception as e:
        log.error(f"Error sending to {agent}: {e}")
        agent_states[agent] = "ERROR_RECOVERY"

async def read_agent_response(agent: str, channel_id: str) -> tuple[str, Dict]:
    """Read and process agent response stream"""
    proc = agent_processes.get(agent)
    if not proc or not proc.stdout:
        return "", {}

    config = agent_config.get(agent, {})
    tool_streaming = config.get("tool_streaming", False)
    stream_to_channel = config.get("stream_to_channel", False)

    final_text = ""
    metadata = {}
    last_posted_chunk = ""

    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break

            try:
                event = json.loads(line.decode())
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")

            # Claude Code stream-json output: each turn emits one or more
            # `assistant` events with content blocks (thinking/text/tool_use),
            # then a single `result` event closes the turn.
            if event_type == "assistant":
                message = event.get("message", {}) or {}
                for block in message.get("content", []) or []:
                    btype = block.get("type")
                    if btype == "text":
                        text = block.get("text", "")
                        if text:
                            final_text += text
                            response_buffers[agent] = final_text
                            if stream_to_channel and channel_id != "0":
                                # TODO: Implement chunked streaming
                                pass
                    elif btype == "tool_use":
                        tool_name = block.get("name", "unknown")
                        log.info(f"{agent} called tool: {tool_name}")
                        if tool_streaming and channel_id != "0":
                            await post_to_discord(agent, channel_id, f"🔧 {tool_name}")
                    # `thinking` blocks are intentionally ignored here — they
                    # are stripped from the final text below as a belt-and-
                    # braces measure for any inline <thinking> tags.

            elif event_type == "result":
                # Extract metadata. Token counts live under `usage`,
                # cost/duration are top-level. Final text is in `result`
                # for success, or `error` field for failures.
                usage = event.get("usage", {}) or {}
                metadata = {
                    "session_id": event.get("session_id"),
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_cost_usd": event.get("total_cost_usd", 0.0),
                    "duration_ms": event.get("duration_ms", 0),
                    "is_error": event.get("is_error", False),
                }
                # If the assistant stream produced nothing, fall back to
                # the result's flat `result` string (success) or `error`.
                if not final_text:
                    final_text = event.get("result", "") or event.get("error", "")
                break

    except Exception as e:
        log.error(f"Error reading response from {agent}: {e}")

    # Strip any inline thinking blocks (defense in depth)
    final_text = THINKING_BLOCK_RE.sub("", final_text).strip()

    agent_states[agent] = "IDLE"
    return final_text, metadata

async def process_agent_queue(agent: str):
    """Process pending messages for agent"""
    lock = agent_locks.get(agent)
    if not lock:
        return

    async with lock:
        if agent_states.get(agent) != "IDLE":
            return

        # Get pending messages
        async with db.execute(
            """
            SELECT * FROM message_queue
            WHERE agent = ? AND processed = ?
            ORDER BY created_at ASC
            LIMIT 20
            """,
            (agent, STATUS_QUEUED)
        ) as cursor:
            messages = await cursor.fetchall()

        if not messages:
            return

        # Mark as in progress
        message_ids = [msg["message_id"] for msg in messages]
        await db.execute(
            f"""
            UPDATE message_queue
            SET processed = ?, processing_started_at = CURRENT_TIMESTAMP
            WHERE message_id IN ({','.join('?' * len(message_ids))})
            """,
            (STATUS_IN_PROGRESS, *message_ids)
        )
        await db.commit()

        # Format batch
        channel_id = messages[0]["channel_id"]
        formatted_parts = []
        for msg in messages:
            timestamp = msg["created_at"]
            author = msg["author"]
            content = msg["content"]
            formatted_parts.append(f"[{timestamp}] {author}: {content}")

        formatted_content = "\n\n".join(formatted_parts)

        # Start typing indicator
        await start_typing(agent, channel_id)

        # Send to agent
        await send_to_agent(agent, formatted_content, message_ids)

        # Read response
        response_text, metadata = await read_agent_response(agent, channel_id)

        # Stop typing
        await stop_typing(channel_id)

        # Post cost update
        if metadata:
            await post_cost_update(agent, metadata)
            await update_session_tokens(agent, metadata.get("input_tokens", 0))

        # Post response to Discord
        discord_msg_id = None
        if response_text and channel_id != "0":
            discord_msg_id = await post_to_discord(agent, channel_id, response_text)

        # Mark complete
        await db.execute(
            f"""
            UPDATE message_queue
            SET processed = ?, response = ?, discord_response_id = ?, processed_at = CURRENT_TIMESTAMP
            WHERE message_id IN ({','.join('?' * len(message_ids))})
            """,
            (STATUS_COMPLETE, response_text, discord_msg_id, *message_ids)
        )
        await db.commit()

        log.info(f"{agent} processed {len(message_ids)} messages")

# =============================================================================
# Crash Recovery
# =============================================================================

async def crash_recovery():
    """Recover from crashes on startup"""
    # Find messages stuck in PROCESSING state
    async with db.execute(
        "SELECT * FROM message_queue WHERE processed = ?",
        (STATUS_IN_PROGRESS,)
    ) as cursor:
        stuck_messages = await cursor.fetchall()

    if stuck_messages:
        log.warning(f"Found {len(stuck_messages)} stuck messages from previous crash")

        for msg in stuck_messages:
            # Mark as crashed
            await db.execute(
                "UPDATE message_queue SET processed = ? WHERE message_id = ?",
                (STATUS_CRASHED, msg["message_id"])
            )

            # Notify channel
            channel_id = msg["channel_id"]
            agent = msg["agent"]
            if channel_id != "0":
                crash_msg = f"⚠️ {agent} crashed while processing message from {msg['author']}"
                await post_to_discord(agent, channel_id, crash_msg)

        await db.commit()

    # Retry posting messages that completed but weren't posted
    async with db.execute(
        "SELECT * FROM message_queue WHERE processed = ? AND discord_response_id IS NULL AND channel_id != '0'",
        (STATUS_COMPLETE,)
    ) as cursor:
        unposted = await cursor.fetchall()

    if unposted:
        log.warning(f"Found {len(unposted)} unposted responses, retrying")
        for msg in unposted:
            if msg["response"]:
                discord_id = await post_to_discord(msg["agent"], msg["channel_id"], msg["response"])
                if discord_id:
                    await db.execute(
                        "UPDATE message_queue SET discord_response_id = ? WHERE message_id = ?",
                        (discord_id, msg["message_id"])
                    )
        await db.commit()

# =============================================================================
# HTTP API
# =============================================================================

async def handle_message(request):
    """POST /message - Queue message for agent"""
    # Check bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or auth_header[7:] != AGENT_SERVER_TOKEN:
        return web.json_response({"error": "Unauthorized"}, status=401)

    data = await request.json()

    agent = data.get("agent")
    channel = data.get("channel", "general")
    channel_id = data.get("channel_id", "0")
    server = data.get("server", "discord")
    author = data.get("author", "unknown")
    author_id = data.get("author_id", "0")
    is_bot = data.get("is_bot", False)
    content = data.get("content", "")
    message_id = data.get("message_id", f"msg-{uuid.uuid4()}")
    mentions_agent = data.get("mentions_agent", False)

    if not agent or agent not in agent_config:
        return web.json_response({"error": "Invalid agent"}, status=400)

    if not content:
        return web.json_response({"error": "Empty content"}, status=400)

    # Check cost limits (unless owner or heartbeat)
    if server != "local" and author_id != OWNER_DISCORD_ID:
        cost_check = await check_cost_limits(author_id)
        if cost_check["exceeded"]:
            return web.json_response(
                {"error": "Cost limit exceeded", "reason": cost_check["reason"]},
                status=429,
                headers={"Retry-After": "3600"}
            )

    # Check queue depth
    async with db.execute(
        "SELECT COUNT(*) as count FROM message_queue WHERE agent = ? AND processed = ?",
        (agent, STATUS_QUEUED)
    ) as cursor:
        row = await cursor.fetchone()
        if row["count"] >= QUEUE_DEPTH_LIMIT:
            return web.json_response({"error": "Queue full"}, status=503)

    # Insert message
    try:
        await db.execute(
            """
            INSERT INTO message_queue
            (agent, channel, channel_id, server, author, author_id, is_bot, content, message_id, mentions_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (agent, channel, channel_id, server, author, author_id, int(is_bot), content, message_id, int(mentions_agent))
        )
        await db.commit()
    except Exception as e:
        log.error(f"Error inserting message: {e}")
        return web.json_response({"error": "Database error"}, status=500)

    # Trigger processing if agent is idle
    if agent_states.get(agent) == "IDLE":
        asyncio.create_task(process_agent_queue(agent))

    return web.json_response({"status": "queued", "message_id": message_id}, status=202)

async def handle_health(request):
    """GET /health - Health check"""
    # Check bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or auth_header[7:] != AGENT_SERVER_TOKEN:
        return web.json_response({"error": "Unauthorized"}, status=401)

    agent_status = {}
    for agent in agent_config:
        proc = agent_processes.get(agent)
        queue_depth = 0
        async with db.execute(
            "SELECT COUNT(*) as count FROM message_queue WHERE agent = ? AND processed = ?",
            (agent, STATUS_QUEUED)
        ) as cursor:
            row = await cursor.fetchone()
            queue_depth = row["count"]

        agent_status[agent] = {
            "state": agent_states.get(agent, "UNKNOWN"),
            "alive": proc is not None and proc.returncode is None,
            "queue_depth": queue_depth,
            "session_id": agent_sessions.get(agent, "")[:8]
        }

    return web.json_response({
        "status": "healthy",
        "agents": agent_status
    })

async def handle_agents(request):
    """GET /agents - List agents"""
    # Check bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or auth_header[7:] != AGENT_SERVER_TOKEN:
        return web.json_response({"error": "Unauthorized"}, status=401)

    agents_list = []
    for agent, config in agent_config.items():
        agents_list.append({
            "name": agent,
            "model": config.get("model"),
            "state": agent_states.get(agent, "UNKNOWN"),
            "has_discord_token": agent in AGENT_TOKENS
        })

    return web.json_response({"agents": agents_list})

async def handle_agent_reset(request):
    """POST /agents/{name}/reset - Reset agent session"""
    # Check bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or auth_header[7:] != AGENT_SERVER_TOKEN:
        return web.json_response({"error": "Unauthorized"}, status=401)

    agent = request.match_info.get("name")
    if agent not in agent_config:
        return web.json_response({"error": "Unknown agent"}, status=404)

    await restart_agent(agent)
    return web.json_response({"status": "reset"})

async def handle_cost(request):
    """POST /cost - Record external cost event"""
    # Check bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or auth_header[7:] != AGENT_SERVER_TOKEN:
        return web.json_response({"error": "Unauthorized"}, status=401)

    data = await request.json()
    agent = data.get("agent")
    cost_delta = data.get("cost_delta", 0.0)

    if agent not in agent_config:
        return web.json_response({"error": "Unknown agent"}, status=400)

    # Record cost
    await db.execute(
        "INSERT INTO cost_events (agent, cost_delta, session_total) VALUES (?, ?, ?)",
        (agent, cost_delta, cost_delta)
    )
    await db.commit()

    # Reset last cost (external sessions are independent)
    agent_last_cost[agent] = 0.0

    return web.json_response({"status": "recorded"})

async def handle_cost_get(request):
    """GET /cost/{agent} - Get cost summary"""
    # Check bearer token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or auth_header[7:] != AGENT_SERVER_TOKEN:
        return web.json_response({"error": "Unauthorized"}, status=401)

    agent = request.match_info.get("agent")

    # Daily cost
    async with db.execute(
        """
        SELECT SUM(cost_delta) as total
        FROM cost_events
        WHERE agent = ? AND timestamp > datetime('now', '-1 day')
        """,
        (agent,)
    ) as cursor:
        row = await cursor.fetchone()
        daily = row["total"] or 0.0

    # Monthly cost
    async with db.execute(
        """
        SELECT SUM(cost_delta) as total
        FROM cost_events
        WHERE agent = ? AND timestamp > datetime('now', '-30 days')
        """,
        (agent,)
    ) as cursor:
        row = await cursor.fetchone()
        monthly = row["total"] or 0.0

    return web.json_response({
        "agent": agent,
        "daily": daily,
        "monthly": monthly,
        "session": agent_last_cost.get(agent, 0.0)
    })

# =============================================================================
# Graceful Shutdown
# =============================================================================

async def graceful_shutdown(sig):
    """Handle SIGTERM gracefully"""
    global shutting_down
    log.info(f"Received {sig}, shutting down gracefully...")
    shutting_down = True

    # Stop accepting new messages (set flag checked by handlers)

    # Wait for agents to finish (max 30s)
    log.info("Waiting for agents to finish current messages...")
    for i in range(30):
        all_idle = all(agent_states.get(a) == "IDLE" for a in agent_config)
        if all_idle:
            break
        await asyncio.sleep(1)

    # Generate summaries for active agents
    log.info("Finalizing sessions...")
    for agent in agent_config:
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", str(Path(__file__).parent / "summarize-session.py"), agent,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=25)
            if proc.returncode == 0:
                log.info(f"Session summary generated for {agent}")
            else:
                log.warning(f"Session summary failed for {agent}: {stderr.decode()[:200]}")
        except asyncio.TimeoutError:
            log.warning(f"Session summary timed out for {agent}")
        except Exception as e:
            log.warning(f"Session summary error for {agent}: {e}")

    # Kill subprocesses
    log.info("Terminating agent subprocesses...")
    for agent in list(agent_processes.keys()):
        await kill_agent_subprocess(agent)

    # Close DB
    if db:
        await db.close()

    # Close HTTP session
    if http_session:
        await http_session.close()

    log.info("Shutdown complete")
    sys.exit(0)

# =============================================================================
# Server Startup
# =============================================================================

async def startup(app):
    """Initialize server on startup"""
    global http_session

    log.info("Starting Karakos Agent Server")

    # Initialize HTTP session
    http_session = aiohttp.ClientSession()

    # Initialize database
    await init_db()

    # Load configuration
    await load_config()

    # Initialize locks and state
    for agent in agent_config:
        agent_locks[agent] = asyncio.Lock()
        agent_states[agent] = "IDLE"
        response_buffers[agent] = ""

    # Crash recovery
    await crash_recovery()

    # Start agent subprocesses
    for agent in agent_config:
        await start_agent_subprocess(agent)

    # Register signal handlers in event loop context
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(graceful_shutdown("SIGTERM")))
    loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(graceful_shutdown("SIGINT")))

    log.info(f"Agent server ready on port {PORT}")

async def shutdown(app):
    """Cleanup on shutdown"""
    log.info("Server shutdown initiated")

    # Kill all subprocesses
    for agent in list(agent_processes.keys()):
        await kill_agent_subprocess(agent)

    # Close HTTP session
    if http_session:
        await http_session.close()

    # Close database
    if db:
        await db.close()

# =============================================================================
# Main
# =============================================================================

def main():
    """Main entry point"""
    # Signal handlers will be registered after event loop starts (in startup)
    # For now, just set flag to handle in asyncio context

    # Create app
    app = web.Application()

    # Register routes
    app.router.add_post("/message", handle_message)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/agents", handle_agents)
    app.router.add_post("/agents/{name}/reset", handle_agent_reset)
    app.router.add_post("/cost", handle_cost)
    app.router.add_get("/cost/{agent}", handle_cost_get)

    # Register startup/shutdown handlers
    app.on_startup.append(startup)
    app.on_shutdown.append(shutdown)

    # Run server
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None)

if __name__ == "__main__":
    main()
