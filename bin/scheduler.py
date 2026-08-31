#!/usr/bin/env python3
"""
Python-based Scheduler — Replaces cron inside Docker

Runs scheduled tasks with full environment variable access.
Health heartbeat confirms liveness.
"""

import fcntl
import schedule
import subprocess
import os
import json
import sys
import time
import logging
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
HEALTH_FILE = WORKSPACE_ROOT / "data" / "health" / "scheduler.json"

# Logging
log = logging.getLogger("scheduler")
log.setLevel(logging.INFO)
handler = RotatingFileHandler(
    WORKSPACE_ROOT / "logs" / "scheduler.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=7
)
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(handler)

# Also log to console
console = logging.StreamHandler()
console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(console)

# Singleton-instance guard (2026-08-07) — see the matching guard in
# relay.py for the full incident writeup (agent-server-duplicate-process-
# incident.md). scheduler.py was one of the two process types that
# silently duplicated during that incident — it doesn't bind a listening
# port either, so a rogue duplicate supervisord's copy just ran
# undetected alongside the real one. flock rather than a PID file: the OS
# releases it automatically on any process exit, including a hard kill,
# so there's no stale-lock cleanup step to itself get skipped.
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

def write_health_timestamp():
    """Write health heartbeat timestamp"""
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HEALTH_FILE, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "status": "healthy"
        }, f)

def run_heartbeat(agent: str):
    """Trigger heartbeat for agent"""
    log.info(f"Running heartbeat for {agent}")
    try:
        subprocess.run(
            [f"{WORKSPACE_ROOT}/bin/heartbeat.sh", agent],
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        log.error(f"Heartbeat failed for {agent}: {e.stderr}")

def run_memory_maintenance():
    """Run memory consolidation"""
    log.info("Running memory maintenance")
    try:
        subprocess.run(
            ["python3", f"{WORKSPACE_ROOT}/bin/memory-maintenance.py"],
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        log.error(f"Memory maintenance failed: {e.stderr}")

def run_memory_dedup():
    """Track 1b (weekly) of the curated memory layer — merges
    near-duplicate `facts` rows via embedding similarity. See
    bin/memory-dedup.py and docs/design/curated-memory-layer.md."""
    log.info("Running memory dedup job")
    try:
        result = subprocess.run(
            ["python3", f"{WORKSPACE_ROOT}/bin/memory-dedup.py"],
            check=True,
            capture_output=True,
            text=True
        )
        log.info(f"Memory dedup: {result.stdout.strip()}")
    except subprocess.CalledProcessError as e:
        log.error(f"Memory dedup job failed: {e.stderr}")

def run_memory_patterns():
    """Track 2 (weekly) of the curated memory layer — evidence-gated
    behavioral pattern promotion. See bin/memory-patterns.py and
    docs/design/curated-memory-layer.md. Deliberately separate from the
    nightly memory-maintenance.py run: different risk profile, own
    cadence, per the design doc's phasing."""
    log.info("Running memory patterns job")
    try:
        result = subprocess.run(
            ["python3", f"{WORKSPACE_ROOT}/bin/memory-patterns.py"],
            check=True,
            capture_output=True,
            text=True
        )
        log.info(f"Memory patterns: {result.stdout.strip()}")
    except subprocess.CalledProcessError as e:
        log.error(f"Memory patterns job failed: {e.stderr}")

def run_memory_reflection():
    """Track 2b (weekly dispatch, monthly-gated) of the curated memory
    layer. See bin/memory-reflection.py — proposes fact-file drafts
    from established patterns for human review; does not write
    voice.md/MEMORY.md directly (v1, deliberately conservative)."""
    log.info("Running memory reflection job")
    try:
        result = subprocess.run(
            ["python3", f"{WORKSPACE_ROOT}/bin/memory-reflection.py"],
            check=True,
            capture_output=True,
            text=True
        )
        log.info(f"Memory reflection: {result.stdout.strip()}")
    except subprocess.CalledProcessError as e:
        log.error(f"Memory reflection job failed: {e.stderr}")

def run_health_monitor():
    """Run health monitor"""
    log.info("Running health monitor")
    try:
        subprocess.run(
            ["python3", f"{WORKSPACE_ROOT}/bin/health-monitor.py"],
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        log.error(f"Health monitor failed: {e.stderr}")

def run_friction_sensor():
    """Scan session transcripts for recurring command/error patterns and
    write proposals if anything crosses threshold. Design from Amos
    (Mike's Karakos instance), 2026-08-06 — see bin/friction-sensor.py
    docstring. Scheduled at 03:46 to match his own stated timing."""
    log.info("Running friction sensor")
    try:
        result = subprocess.run(
            ["python3", f"{WORKSPACE_ROOT}/bin/friction-sensor.py"],
            check=True,
            capture_output=True,
            text=True
        )
        log.info(f"Friction sensor: {result.stdout.strip()}")
    except subprocess.CalledProcessError as e:
        log.error(f"Friction sensor failed: {e.stderr}")

def run_outbox_flush():
    """Deliver any messages queued via bin/outbox.py to channels outside
    the turn that queued them. Fix for 'Marvin knows he owes a channel a
    message but has no turn scoped there to send it' (2026-08-08) — see
    bin/outbox.py docstring for the full incident. Runs every minute so a
    queued relay lands within a minute instead of waiting on the next
    turn that happens to be routed correctly."""
    try:
        result = subprocess.run(
            ["python3", f"{WORKSPACE_ROOT}/bin/outbox.py", "flush"],
            check=True,
            capture_output=True,
            text=True
        )
        if result.stdout.strip() and "Nothing to deliver" not in result.stdout:
            log.info(f"Outbox flush: {result.stdout.strip()}")
    except subprocess.CalledProcessError as e:
        log.error(f"Outbox flush failed: {e.stderr}")

def check_updates():
    """Check for Karakos updates"""
    log.info("Checking for updates")
    try:
        subprocess.run(
            ["bash", f"{WORKSPACE_ROOT}/bin/check-updates.sh"],
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        log.error(f"Update check failed: {e.stderr}")

def purge_old_data():
    """Purge old logs and data"""
    log.info("Purging old data")
    try:
        subprocess.run(
            ["python3", f"{WORKSPACE_ROOT}/bin/purge-data.py"],
            check=True,
            capture_output=True,
            text=True
        )
    except subprocess.CalledProcessError as e:
        log.error(f"Data purge failed: {e.stderr}")

def main():
    """Main scheduler loop"""
    _acquire_singleton_lock("scheduler")
    log.info("Scheduler starting")

    # Load agents config to get agent names
    agents_config_path = WORKSPACE_ROOT / "config" / "agents.json"
    if agents_config_path.exists():
        with open(agents_config_path) as f:
            config = json.load(f)
            agents = list(config.get("agents", {}).keys())
    else:
        agents = []
        log.warning("No agents config found")

    # Schedule heartbeats for each agent (staggered by 15 minutes)
    if agents:
        primary_agent = agents[0]
        schedule.every(30).minutes.do(lambda: run_heartbeat(primary_agent))
        log.info(f"Scheduled heartbeat for primary agent: {primary_agent}")

        # Schedule relay agent if exists
        if "relay" in agents:
            schedule.every(30).minutes.at(":15").do(lambda: run_heartbeat("relay"))
            log.info("Scheduled heartbeat for relay agent")

    # Schedule maintenance tasks
    schedule.every().day.at("03:00").do(run_memory_maintenance)
    schedule.every().sunday.at("03:15").do(run_memory_dedup)     # Track 1b, weekly
    schedule.every().sunday.at("03:30").do(run_memory_patterns)  # Track 2, weekly
    schedule.every().sunday.at("03:45").do(run_memory_reflection)  # Track 2b, monthly-gated
    schedule.every().day.at("03:46").do(run_friction_sensor)
    schedule.every().day.at("04:00").do(run_health_monitor)
    schedule.every().day.at("04:30").do(purge_old_data)
    schedule.every().monday.at("05:00").do(check_updates)  # Weekly update check
    schedule.every(1).minutes.do(run_outbox_flush)

    log.info("Scheduler configured, entering main loop")

    # Main loop
    while True:
        try:
            schedule.run_pending()
            write_health_timestamp()
            time.sleep(60)
        except KeyboardInterrupt:
            log.info("Scheduler shutting down")
            break
        except Exception as e:
            log.error(f"Scheduler error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
