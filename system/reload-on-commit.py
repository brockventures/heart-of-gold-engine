#!/usr/bin/env python3
"""
reload-on-commit.py — Auto-bounce the relay/scheduler supervisor
processes when a commit touches the files that govern their behavior.

Problem this fixes (2026-08-08): bin/relay.py picked up the 📨
attention-marker gate override in commit fa031a9 earlier tonight, but the
running relay process kept executing the pre-change code until someone
noticed and manually called reload_agent — Amos's own adoption message,
which itself contained the marker, went unnoticed because of exactly
this gap. Same shape of bug as the one bin/outbox.py fixes for
cross-channel relays, just at deploy time instead of message time: code
landing on disk is not the same as code running.

Called from .git/hooks/post-commit, which is installed from
system/install-post-commit-hook.sh by bin/entrypoint.sh — mirrors how
system/install-hooks.sh installs the pre-commit protected-paths check.
Maps files changed in the commit that just happened to the supervisor
process that needs bouncing, and shells out to bin/safe-pkill.sh to send
it SIGTERM; supervisor's autorestart brings it back running the new code.

Deliberately excludes bin/agent-server.py. A git commit made from a Bash
tool call is very likely a child of agent-server.py, i.e. this script's
own ancestry — bin/safe-pkill.sh already refuses to signal its own
ancestry, so attempting it would just fail, but this script doesn't even
try. Self-bouncing needs a deliberate, visible action (the reload_agent
admin tool, or a fresh turn), not a silent side effect of committing. It
only prints a reminder for that case.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
EVENTS_LOG = WORKSPACE_ROOT / "logs" / "git-events.jsonl"
SAFE_PKILL = WORKSPACE_ROOT / "bin" / "safe-pkill.sh"

# Changed file -> (safe-pkill.sh match pattern, process label).
# reply_gate.py has no process of its own; it's imported by relay.py, so
# a change there bounces relay just like a direct relay.py change would.
WATCHED = {
    "bin/relay.py": ("bin/relay.py", "relay"),
    "bin/reply_gate.py": ("bin/relay.py", "relay"),
    "bin/scheduler.py": ("bin/scheduler.py", "scheduler"),
}

SELF_PROCESS_WARN = {
    "bin/agent-server.py": (
        "agent-server.py changed (Marvin's own process) — not "
        "auto-restarted, that needs a deliberate reload_agent call or a "
        "fresh turn, not a silent side effect of a commit."
    ),
}


def get_committed_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        capture_output=True, text=True, cwd=str(WORKSPACE_ROOT),
    )
    return [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]


def plan_reloads(changed_files: list[str]) -> tuple[dict[str, str], list[str]]:
    """Pure mapping from changed files to (process label -> pkill pattern)
    plus any self-process warnings. Kept side-effect-free for testing."""
    to_bounce: dict[str, str] = {}
    warnings: list[str] = []
    for f in changed_files:
        if f in WATCHED:
            pattern, label = WATCHED[f]
            to_bounce[label] = pattern
        if f in SELF_PROCESS_WARN:
            warnings.append(SELF_PROCESS_WARN[f])
    return to_bounce, warnings


def _log(event: dict) -> None:
    try:
        EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_LOG, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def main():
    changed = get_committed_files()
    to_bounce, warnings = plan_reloads(changed)

    if not to_bounce and not warnings:
        return

    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(WORKSPACE_ROOT)
    ).stdout.strip()

    for label, pattern in to_bounce.items():
        print(f"reload-on-commit: {label} code changed in {commit_sha[:8]} — bouncing via safe-pkill.sh")
        result = subprocess.run(
            ["bash", str(SAFE_PKILL), "-TERM", pattern],
            capture_output=True, text=True, cwd=str(WORKSPACE_ROOT),
        )
        _log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "auto_reload",
            "commit": commit_sha,
            "process": label,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        })
        if result.returncode != 0:
            print(
                f"reload-on-commit: WARNING — bounce of {label} may not "
                f"have succeeded: {result.stderr.strip()}",
                file=sys.stderr,
            )

    for w in warnings:
        print(f"reload-on-commit: NOTE — {w}")
        _log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "auto_reload_skipped_self",
            "commit": commit_sha,
            "detail": w,
        })


if __name__ == "__main__":
    main()
