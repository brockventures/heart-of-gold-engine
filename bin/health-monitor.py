#!/usr/bin/env python3
"""
Health Monitor — Checks component health and alerts on staleness
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
HEALTH_DIR = WORKSPACE_ROOT / "data" / "health"

# Logging
log = logging.getLogger("health-monitor")
log.setLevel(logging.INFO)
handler = RotatingFileHandler(
    WORKSPACE_ROOT / "logs" / "health-alerts.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=3
)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(handler)

# Component health thresholds (in seconds)
THRESHOLDS = {
    "mcp-tools.json": 600,               # 10 minutes
    "relay.json": 300,                    # 5 minutes
    "memory-maintenance.json": 172800,    # 48 hours — was "memory.json",
                                           # a filename that memory-maintenance.py
                                           # never actually wrote. Fixed 2026-08-06.
    "scheduler.json": 300,                 # 5 minutes
}

# mcp-tools.json is written by mcp/tools-server.py, which isn't a
# supervisord-managed daemon — it's an MCP stdio server spawned fresh per
# Claude Code session and only writes its health file once it actually
# receives a tools/list or tools/call RPC. Some MCP clients discover
# tools lazily, so a session that never happens to invoke a
# mcp__karakos-admin__* tool may never trigger that RPC at all. Found
# 2026-08-07: the alert had fired daily with the file simply absent, and
# mcp-tools-audit.db (created early in that server's own startup, before
# any RPC handling) didn't exist either — confirms the process has never
# run this RPC, not that it crashed after running. "Missing" here means
# "never used yet," not "down" — don't alert on it. A file that exists
# and goes stale is still a real problem and still alerts normally.
OPTIONAL_UNTIL_FIRST_USE = {"mcp-tools.json"}

def check_health_file(component: str, threshold: int) -> tuple[bool, str]:
    """Check if health file is fresh"""
    health_file = HEALTH_DIR / component

    if not health_file.exists():
        if component in OPTIONAL_UNTIL_FIRST_USE:
            return True, ""
        return False, f"{component} health file missing"

    try:
        with open(health_file) as f:
            data = json.load(f)
            timestamp_str = data.get("timestamp", "")

        if not timestamp_str:
            return False, f"{component} has no timestamp"

        # Normalize both sides to aware UTC before subtracting. Writers
        # aren't consistent about including an offset (relay.py's
        # write_health_heartbeat() writes naive datetime.now(), which
        # under this container's TZ=UTC is UTC values without the label;
        # mcp/tools-server.py's write_health() writes properly
        # tz-aware datetime.now(timezone.utc)) — subtracting a naive
        # datetime.now() from an aware one raises "can't subtract
        # offset-naive and offset-aware datetimes", which is exactly the
        # error this alerted with 2026-08-07. Handle both without
        # needing every writer to agree on a convention.
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age = (now - timestamp).total_seconds()

        if age > threshold:
            return False, f"{component} stale ({age/60:.1f} min, threshold {threshold/60:.1f} min)"

        return True, ""

    except Exception as e:
        return False, f"{component} error: {e}"

def poke_signals(message: str):
    """Send alert to signals channel"""
    try:
        subprocess.run(
            [
                f"{WORKSPACE_ROOT}/bin/poke.sh",
                "--reply-channel", "signals",
                "--source", "health-monitor",
                message
            ],
            check=True,
            capture_output=True
        )
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to poke signals: {e}")

def main():
    """Check all components and alert on issues"""
    log.info("Running health monitor")

    issues = []

    for component, threshold in THRESHOLDS.items():
        healthy, reason = check_health_file(component, threshold)
        if not healthy:
            log.warning(f"Health check failed: {reason}")
            issues.append(reason)

    if issues:
        alert = "⚠️ Health check failures:\n" + "\n".join(f"• {issue}" for issue in issues)
        poke_signals(alert)
        log.info("Alert sent to signals channel")
    else:
        log.info("All components healthy")

if __name__ == "__main__":
    main()
