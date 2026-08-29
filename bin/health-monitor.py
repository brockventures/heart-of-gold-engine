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

# git sync check timeouts (seconds) — short enough that a network hiccup
# can't hang the daily health-monitor run.
GIT_FETCH_TIMEOUT = 15
GIT_CMD_TIMEOUT = 10
GIT_PUSH_TIMEOUT = 30

# CI-status-after-push polling (seconds). Real CI runs observed 2026-08-29
# completed in ~20-40s, so poll every 15s up to a 4-minute cap — enough
# headroom for a slow runner without letting a hung/never-triggered
# workflow block the health-monitor run indefinitely.
GH_RUN_LIST_TIMEOUT = 15
CI_POLL_INTERVAL = 15
CI_POLL_TIMEOUT = 240

def check_ci_status_after_push(sha: str) -> tuple[str, str]:
    """Poll GitHub Actions for the run triggered by the push check_git_sync()
    just made, and report whether it passed.

    Returns a ("success" | "failure" | "pending", message) pair — "pending"
    means no terminal run showed up within CI_POLL_TIMEOUT (informational,
    not a failure: the workflow may simply not have triggered yet on
    GitHub's side). Only ever called right after this process's own push;
    it isn't a general-purpose CI-status poller for arbitrary commits.
    """
    deadline = time.monotonic() + CI_POLL_TIMEOUT
    while True:
        try:
            result = subprocess.run(
                ["gh", "run", "list", "--commit", sha, "--branch", "main",
                 "--limit", "1", "--json", "status,conclusion,url"],
                cwd=WORKSPACE_ROOT, check=True, capture_output=True, text=True,
                timeout=GH_RUN_LIST_TIMEOUT,
            )
            runs = json.loads(result.stdout or "[]")
        except Exception as e:
            log.warning(f"git sync check: gh run list for {sha} failed: {e}")
            runs = []

        if runs and runs[0].get("status") == "completed":
            conclusion = runs[0].get("conclusion")
            url = runs[0].get("url", "")
            if conclusion == "success":
                return "success", ""
            return "failure", (
                f"CI run for pushed commit {sha} finished with conclusion "
                f"'{conclusion}': {url}"
            )

        if time.monotonic() >= deadline:
            return "pending", (
                f"no CI run found for {sha} after {CI_POLL_TIMEOUT}s, "
                "may not have triggered yet"
            )

        time.sleep(CI_POLL_INTERVAL)

def check_git_sync() -> tuple[bool, str]:
    """Check that local main hasn't silently diverged from origin/main.

    Incident 2026-08-29: local main drifted 43 commits / 18 days ahead of
    origin/main with zero visible errors. Root cause was a GITHUB_TOKEN
    missing the 'workflow' OAuth scope — GitHub rejects (with a clear
    stderr message) any push of a range touching .github/workflows/*
    without it, and nothing was reading `git push`'s exit code or stderr.
    This check fetches origin/main, and if local is ahead, attempts a
    plain fast-forward `git push origin main` right here — if that
    succeeds, the drift was same-session and self-heals silently (no
    alert). Only a genuine push *failure* is worth alerting on, and it's
    reported every run until fixed (not just once) since the underlying
    cause needs a human (regenerating the token) to resolve. Never
    force-pushes, rebases, or touches history — read-only except for the
    push itself, which git already refuses unless it's a fast-forward.
    """
    try:
        subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=WORKSPACE_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_FETCH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, "git sync check: fetch from origin timed out"
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        return False, f"git sync check: fetch from origin failed: {stderr}"
    except Exception as e:
        return False, f"git sync check: fetch from origin errored: {e}"

    try:
        ahead = int(subprocess.run(
            ["git", "rev-list", "--count", "origin/main..main"],
            cwd=WORKSPACE_ROOT, check=True, capture_output=True, text=True,
            timeout=GIT_CMD_TIMEOUT,
        ).stdout.strip())
        behind = int(subprocess.run(
            ["git", "rev-list", "--count", "main..origin/main"],
            cwd=WORKSPACE_ROOT, check=True, capture_output=True, text=True,
            timeout=GIT_CMD_TIMEOUT,
        ).stdout.strip())
    except Exception as e:
        return False, f"git sync check: rev-list comparison failed: {e}"

    problems = []

    if ahead > 0:
        try:
            subprocess.run(
                ["git", "push", "origin", "main"],
                cwd=WORKSPACE_ROOT, check=True, capture_output=True, text=True,
                timeout=GIT_PUSH_TIMEOUT,
            )
            log.info(f"git sync check: pushed {ahead} commit(s) to origin/main")

            # Incident 2026-08-11 through ~2026-08-29: CI on GitHub Actions
            # had been red for weeks with nobody noticing, because nothing
            # after a push ever looked at whether the resulting run passed.
            # Only check the run *this push* triggered — not a general
            # retroactive CI auditor for commits already on origin before
            # this function ran.
            try:
                sha = subprocess.run(
                    ["git", "rev-parse", "main"],
                    cwd=WORKSPACE_ROOT, check=True, capture_output=True, text=True,
                    timeout=GIT_CMD_TIMEOUT,
                ).stdout.strip()
                ci_status, ci_message = check_ci_status_after_push(sha)
                if ci_status == "failure":
                    problems.append(ci_message)
                elif ci_status == "pending":
                    log.info(f"git sync check: {ci_message}")
                else:
                    log.info(f"git sync check: CI passed for {sha}")
            except Exception as e:
                log.warning(f"git sync check: could not verify CI status after push: {e}")
        except subprocess.TimeoutExpired:
            problems.append(
                f"local main is {ahead} commit(s) ahead of origin/main and push timed out"
            )
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            problems.append(
                f"local main is {ahead} commit(s) ahead of origin/main and push failed: {stderr}"
            )

    if behind > 0:
        problems.append(
            f"local main is {behind} commit(s) behind origin/main "
            "(informational — someone/something else pushed; may need a "
            "manual merge, not auto-handled)"
        )

    if problems:
        return False, "; ".join(problems)
    return True, ""

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

    git_healthy, git_reason = check_git_sync()
    if not git_healthy:
        log.warning(f"Health check failed: {git_reason}")
        issues.append(git_reason)

    if issues:
        alert = "⚠️ Health check failures:\n" + "\n".join(f"• {issue}" for issue in issues)
        poke_signals(alert)
        log.info("Alert sent to signals channel")
    else:
        log.info("All components healthy")

if __name__ == "__main__":
    main()
