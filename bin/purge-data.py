#!/usr/bin/env python3
"""
Data Purging — Clean up old logs and messages.

Purges:
1. Message JSONL files older than MESSAGE_RETENTION_DAYS (default 90)
2. Tool audit records older than TOOL_AUDIT_RETENTION_DAYS (default 30)
3. Session summary archives (keeps last 30 per agent)

Called by scheduler daily at 4:30 AM.
"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
MESSAGES_DIR = WORKSPACE / "data" / "messages"
TOOL_AUDIT_DB = WORKSPACE / "mcp" / "tool-audit.db"
SESSION_SUMMARIES_DIR = WORKSPACE / "logs" / "session-summaries"

MESSAGE_RETENTION_DAYS = int(os.environ.get("MESSAGE_RETENTION_DAYS", "90"))
TOOL_AUDIT_RETENTION_DAYS = int(os.environ.get("TOOL_AUDIT_RETENTION_DAYS", "30"))
SUMMARY_RETENTION_COUNT = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] purge-data: %(message)s",
)
log = logging.getLogger(__name__)


def purge_old_messages() -> int:
    """Delete message JSONL files older than retention period."""
    if not MESSAGES_DIR.exists():
        return 0

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=MESSAGE_RETENTION_DAYS)
    deleted = 0

    for file in MESSAGES_DIR.glob("messages-*.jsonl"):
        try:
            # Extract date from filename: messages-YYYY-MM-DD.jsonl
            date_str = file.stem.replace("messages-", "")
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

            if file_date < cutoff_date:
                file.unlink()
                deleted += 1
                log.info(f"Deleted old message file: {file.name}")
        except (ValueError, OSError) as e:
            log.warning(f"Failed to process {file.name}: {e}")
            continue

    if deleted:
        log.info(f"Purged {deleted} message files older than {MESSAGE_RETENTION_DAYS} days")
    return deleted


def purge_tool_audit() -> int:
    """Delete tool audit records older than retention period and run VACUUM."""
    if not TOOL_AUDIT_DB.exists():
        return 0

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=TOOL_AUDIT_RETENTION_DAYS)
    cutoff_str = cutoff_date.isoformat()

    try:
        conn = sqlite3.connect(str(TOOL_AUDIT_DB))
        cursor = conn.execute(
            "DELETE FROM tool_calls WHERE timestamp < ?",
            (cutoff_str,)
        )
        deleted = cursor.rowcount
        conn.commit()

        # Run VACUUM to reclaim space
        conn.execute("VACUUM")
        conn.close()

        if deleted:
            log.info(f"Purged {deleted} tool audit records older than {TOOL_AUDIT_RETENTION_DAYS} days")
        return deleted

    except sqlite3.Error as e:
        log.error(f"Failed to purge tool audit: {e}")
        return 0


def purge_old_session_summaries() -> int:
    """Keep only the last N session summaries per agent."""
    if not SESSION_SUMMARIES_DIR.exists():
        return 0

    # Group summaries by agent name (extract from filename pattern)
    agent_summaries = {}

    for file in SESSION_SUMMARIES_DIR.glob("*.md"):
        # Expected pattern: {agent}-{timestamp}.md or {timestamp}.md
        parts = file.stem.split("-")
        if len(parts) >= 2:
            # Assume first part is agent name (or 'summary' for generic)
            agent = parts[0]
            if agent not in agent_summaries:
                agent_summaries[agent] = []
            agent_summaries[agent].append(file)

    deleted = 0
    for agent, files in agent_summaries.items():
        # Sort by modification time (newest first)
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        # Delete all but the last N
        to_delete = files[SUMMARY_RETENTION_COUNT:]
        for file in to_delete:
            try:
                file.unlink()
                deleted += 1
            except OSError as e:
                log.warning(f"Failed to delete {file.name}: {e}")

    if deleted:
        log.info(f"Purged {deleted} old session summaries (kept {SUMMARY_RETENTION_COUNT} per agent)")
    return deleted


def main():
    log.info("Data purge starting")

    try:
        stats = {
            "messages": purge_old_messages(),
            "tool_audit": purge_tool_audit(),
            "session_summaries": purge_old_session_summaries(),
        }

        log.info(f"Purge complete: {stats}")

    except Exception as e:
        log.error(f"Purge failed: {e}")
        raise


if __name__ == "__main__":
    main()
