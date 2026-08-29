#!/usr/bin/env python3
"""
recovery-agent.py — deterministic operational watchdog.

Spec: agents/Marvin/inbox/recovery-agent-spec-2026-08-08.md (Ian, relayed
via an external diagnostic session that had docker exec access, 2026-08-08
— corroborated live by Ian in #general the same night, and its account of
that session's live agent-server.py patch matches what Marvin
independently verified in git history).

Why this exists: every operational bug caught so far (rate-limit freeze,
duplicate-process zombies, the channel-leak bug, tonight's seven_day
pause bug) got fixed by someone getting shell access and hand-diagnosing
it. That's slow, and unexplained external edits are indistinguishable
from tampering to anything already primed to be cautious (see the
Moon-Problem / GitHub-token incidents in memory — same reasoning shape).
This replaces "route routine recovery through an outside shell" with a
small, deterministic, in-band, attributable watchdog.

Deliberately NOT an LLM persona — every check is a plain predicate, every
remediation is a pre-approved, narrow, reversible playbook. New failure
shapes are logged/proposed to data/recovery-proposals/, never
improvised. Deliberately NOT a code patcher — it only touches process
state (kill an orphan, bounce a wedged subprocess), never bin/, system/,
or anything in tier1_protected/tier2_review_required.

First-PR scope only (spec's "Suggested first PR scope"): dry-run mode,
two playbooks —
  - duplicate_process: >1 process matching a known supervised program's
    command line (mirrors the singleton-lock precedent already in
    relay.py/scheduler.py — see facts/agent-server-duplicate-process-
    incident.md, this exact scenario already happened once, found by
    hand via /proc).
  - wedged_subprocess: a message_queue row stuck STATUS_IN_PROGRESS
    longer than a generous timeout.

RECOVERY_DRY_RUN defaults to true: detect and log/propose, remediate
nothing, until playbooks have run clean for a while. Every finding and
action (dry-run or real) is posted to #signals via bin/outbox.py — reuses
the durable cross-channel queue built earlier tonight rather than a
direct Discord call, so a signals post from an independent process
survives even if delivery has a transient hiccup.
"""

import fcntl
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))

# Logging — mirrors relay.py/agent-server.py's pattern. Previously this
# module only used bare print()/print(..., file=sys.stderr), which
# supervisord routes straight to the container's own stdout/stderr
# (stdout_logfile=/dev/stdout in supervisord.conf) and nowhere under
# logs/. That meant a crash here left no trace reachable from inside the
# container — found the hard way 2026-08-10 when recovery-agent hit a
# rapid exit-status-2 crash-loop during a container migration and
# supervisord gave up after 6 deaths in 13s, with no way to recover the
# actual traceback short of `docker logs` on the host. A dedicated file
# handler here means the next occurrence is diagnosable in-band.
log = logging.getLogger("recovery-agent")
log.setLevel(logging.INFO)
# Guard against duplicate handlers — same reasoning as agent-server.py's/
# relay.py's identical guard. KARAKOS_LOG_DIR mirrors their override so
# tests (and any environment without a real WORKSPACE_ROOT/logs, e.g. CI)
# don't crash trying to open a log file under a directory that doesn't
# exist. This block previously didn't actually mirror the other two
# despite the comment above claiming it did — missing the env override,
# the mkdir, and the handler guard — which is why importing this module
# hard-crashed in GitHub Actions with no WORKSPACE_ROOT set (falls back
# to the container-era "/workspace" default, which doesn't exist there).
if not log.handlers:
    _log_dir = Path(os.environ.get("KARAKOS_LOG_DIR", str(WORKSPACE_ROOT / "logs")))
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(
        _log_dir / "recovery-agent.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=7,
    )
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(_handler)

    _console = logging.StreamHandler()
    _console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(_console)

DB_PATH = WORKSPACE_ROOT / "data" / "memory" / "agent-server.db"
PROPOSALS_DIR = WORKSPACE_ROOT / "data" / "recovery-proposals"
RATE_LIMIT_STATE_PATH = WORKSPACE_ROOT / "data" / "recovery-agent-rate-limits.json"
LOCK_PATH = WORKSPACE_ROOT / "data" / "recovery-agent.lock"
SAFE_PKILL = WORKSPACE_ROOT / "bin" / "safe-pkill.sh"
OUTBOX = WORKSPACE_ROOT / "bin" / "outbox.py"

SWEEP_INTERVAL_SEC = int(os.environ.get("RECOVERY_SWEEP_INTERVAL_SEC", "60"))
DRY_RUN = os.environ.get("RECOVERY_DRY_RUN", "true").lower() != "false"
WEDGED_TIMEOUT_SEC = int(os.environ.get("RECOVERY_WEDGED_TIMEOUT_SEC", str(20 * 60)))
MAX_ACTIONS_PER_HOUR = int(os.environ.get("RECOVERY_MAX_ACTIONS_PER_HOUR", "3"))

# message_queue.processed values — kept in sync with agent-server.py by
# hand (STATUS_IN_PROGRESS = 1). Duplicated rather than imported: this
# process must be independently importable/runnable without booting
# agent-server.py's event loop/DB connection at module load time.
STATUS_IN_PROGRESS = 1

# Supervised single-instance processes this watchdog knows about.
WATCHED_PROCESSES = {
    "Marvin": "bin/agent-server.py",
    "relay": "bin/relay.py",
    "scheduler": "bin/scheduler.py",
}


@dataclass
class Finding:
    signature: str
    detail: str
    context: dict = field(default_factory=dict)


@dataclass
class ActionLog:
    signature: str
    action: str
    detail: str
    dry_run: bool


# ---------------------------------------------------------------------------
# /proc helpers — no ps/pgrep in this environment (see bin/safe-pkill.sh's
# own docstring); read /proc directly, same approach.
# ---------------------------------------------------------------------------

def list_processes() -> list[dict]:
    """Return [{pid, ppid, argv, cmdline}] for every readable /proc/<pid>.

    `argv` keeps the real argv boundaries (NUL-separated in /proc, as the
    kernel gives them) — matching against individual tokens rather than
    the whole joined string matters: a `claude -p` subprocess passes its
    entire system prompt as one argv element, and that text can contain
    a watched file path as plain prose (e.g. this very conversation
    mentioning "bin/relay.py") without that process being anything like
    a duplicate supervised instance. `cmdline` (joined, for logging only)
    is kept for human-readable detail strings, never for matching.
    """
    procs = []
    proc_root = Path("/proc")
    if not proc_root.exists():
        return procs
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            raw = (entry / "cmdline").read_bytes()
        except (OSError, FileNotFoundError):
            continue
        argv = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
        if not argv:
            continue
        ppid = None
        try:
            stat = (entry / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[-1].split()[1])
        except (OSError, FileNotFoundError, IndexError, ValueError):
            pass
        procs.append({"pid": pid, "ppid": ppid, "argv": argv, "cmdline": " ".join(argv)})
    return procs


def supervisord_pid() -> Optional[int]:
    pid_file = Path("/tmp/supervisord.pid")
    try:
        return int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Checks — read-only, pure given their inputs, for testability.
# ---------------------------------------------------------------------------

def _is_watched_invocation(argv: list[str], pattern: str) -> bool:
    """Structural match against real argv, not a substring scan of the
    whole command line — see list_processes() docstring for why that
    matters. A real supervised invocation is `python3 /path/bin/X.py`:
    a short interpreter token plus a script-path token, not a script
    path buried inside a much larger unrelated argument."""
    has_script = any(tok == pattern or tok.endswith("/" + pattern) for tok in argv)
    has_interpreter = any(tok in ("python", "python3") or tok.endswith(("/python", "/python3")) for tok in argv)
    return has_script and has_interpreter


def check_duplicate_processes(procs: list[dict]) -> list[Finding]:
    findings = []
    for label, pattern in WATCHED_PROCESSES.items():
        matches = [p for p in procs if _is_watched_invocation(p["argv"], pattern)]
        if len(matches) > 1:
            findings.append(Finding(
                signature="duplicate_process",
                detail=f"{len(matches)} processes match {pattern} ({label})",
                context={"label": label, "pattern": pattern, "pids": [p["pid"] for p in matches]},
            ))
    return findings


def check_wedged_subprocess(db_path: Path = DB_PATH, now: Optional[float] = None) -> list[Finding]:
    now = now if now is not None else time.time()
    findings: list[Finding] = []
    if not db_path.exists():
        return findings
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT agent, id, processing_started_at FROM message_queue "
            "WHERE processed = ? AND processing_started_at IS NOT NULL",
            (STATUS_IN_PROGRESS,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        started = _parse_timestamp(row["processing_started_at"])
        if started is None:
            continue
        age = now - started
        if age > WEDGED_TIMEOUT_SEC:
            findings.append(Finding(
                signature="wedged_subprocess",
                detail=f"{row['agent']} message id={row['id']} STATUS_IN_PROGRESS for {int(age / 60)}min",
                context={"agent": row["agent"], "message_id": row["id"], "age_sec": age},
            ))
    return findings


def _parse_timestamp(value: str) -> Optional[float]:
    """SQLite CURRENT_TIMESTAMP is naive UTC ('YYYY-MM-DD HH:MM:SS')."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Playbooks — the only place that takes action, and only when not dry-run.
# ---------------------------------------------------------------------------

def remediate_duplicate_process(finding: Finding, dry_run: bool = True, procs: Optional[list[dict]] = None) -> ActionLog:
    pids = finding.context["pids"]
    sup_pid = supervisord_pid()
    procs = procs if procs is not None else list_processes()
    procs_by_pid = {p["pid"]: p for p in procs}

    keep = None
    if sup_pid is not None:
        direct_children = [pid for pid in pids if procs_by_pid.get(pid, {}).get("ppid") == sup_pid]
        if len(direct_children) == 1:
            keep = direct_children[0]

    if keep is None and len(pids) > 3:
        # Spec: >2 duplicates (i.e. 3+ total matches with no clear
        # canonical instance) is ambiguous — investigate, don't guess.
        return ActionLog(
            signature=finding.signature,
            action="escalate",
            detail=f"{finding.detail} — could not identify the canonical instance and there are "
                   f"more than 2 duplicates, needs a human",
            dry_run=dry_run,
        )

    orphans = [pid for pid in pids if pid != keep] if keep is not None else pids[1:]

    if dry_run:
        return ActionLog(
            signature=finding.signature,
            action="would_kill_orphans",
            detail=f"{finding.detail} — would SIGTERM {orphans}, keep {keep if keep is not None else pids[0]}",
            dry_run=True,
        )

    killed = []
    for pid in orphans:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except (ProcessLookupError, PermissionError):
            pass
    return ActionLog(
        signature=finding.signature,
        action="killed_orphans",
        detail=f"{finding.detail} — SIGTERM sent to {killed}, kept {keep if keep is not None else pids[0]}",
        dry_run=False,
    )


def remediate_wedged_subprocess(finding: Finding, dry_run: bool = True) -> ActionLog:
    agent = finding.context["agent"]
    pattern = WATCHED_PROCESSES.get(agent)
    if pattern is None:
        return ActionLog(
            signature=finding.signature,
            action="escalate",
            detail=f"{finding.detail} — unknown agent {agent!r}, no known process to bounce",
            dry_run=dry_run,
        )
    if dry_run:
        return ActionLog(
            signature=finding.signature,
            action="would_restart",
            detail=f"{finding.detail} — would SIGTERM {pattern} via safe-pkill.sh",
            dry_run=True,
        )
    result = subprocess.run(
        ["bash", str(SAFE_PKILL), "-TERM", pattern],
        capture_output=True, text=True,
    )
    return ActionLog(
        signature=finding.signature,
        action="restarted" if result.returncode == 0 else "restart_failed",
        detail=f"{finding.detail} — {(result.stderr or result.stdout).strip()}",
        dry_run=False,
    )


KNOWN_PLAYBOOKS = {
    "duplicate_process": remediate_duplicate_process,
    "wedged_subprocess": remediate_wedged_subprocess,
}


# ---------------------------------------------------------------------------
# Rate limiting — per-signature, so one flapping check can't mask a real
# recurring bug by endlessly papering over it.
# ---------------------------------------------------------------------------

def _load_rate_state(path: Path = RATE_LIMIT_STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_rate_state(state: dict, path: Path = RATE_LIMIT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))


def rate_limit_exceeded(signature: str, state: dict, now: Optional[float] = None) -> bool:
    now = now if now is not None else time.time()
    recent = [t for t in state.get(signature, []) if now - t < 3600]
    state[signature] = recent
    return len(recent) >= MAX_ACTIONS_PER_HOUR


def record_action(signature: str, state: dict, now: Optional[float] = None) -> None:
    now = now if now is not None else time.time()
    state.setdefault(signature, []).append(now)


# ---------------------------------------------------------------------------
# Reporting — proposals for unmatched signatures, #signals for everything
# a known playbook did or would do.
# ---------------------------------------------------------------------------

def log_and_propose(finding: Finding, proposals_dir: Path = PROPOSALS_DIR) -> None:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    path = proposals_dir / f"{datetime.now(timezone.utc):%Y-%m-%d}.md"
    with open(path, "a") as f:
        f.write(f"- {datetime.now(timezone.utc).isoformat()} [{finding.signature}] {finding.detail}\n")


def post_to_signals(text: str) -> None:
    """Durable and in-band: routed through bin/outbox.py rather than a
    direct Discord call, so an alert from this independent process
    survives a transient delivery hiccup the same way any other
    cross-channel relay now does."""
    subprocess.run(
        ["python3", str(OUTBOX), "add", "signals", text],
        capture_output=True, text=True,
    )


# ---------------------------------------------------------------------------
# Sweep + main loop
# ---------------------------------------------------------------------------

def run_sweep(dry_run: bool = DRY_RUN, rate_state: Optional[dict] = None) -> list[ActionLog]:
    rate_state = rate_state if rate_state is not None else _load_rate_state()
    procs = list_processes()
    findings = check_duplicate_processes(procs) + check_wedged_subprocess()

    actions = []
    for finding in findings:
        playbook = KNOWN_PLAYBOOKS.get(finding.signature)
        if playbook is None:
            log_and_propose(finding)
            continue
        if rate_limit_exceeded(finding.signature, rate_state):
            post_to_signals(
                f"⚠️ recovery-agent: {finding.signature} playbook rate-limited "
                f"({MAX_ACTIONS_PER_HOUR}/hr) — needs a human. {finding.detail}"
            )
            continue
        action = playbook(finding, dry_run=dry_run)
        record_action(finding.signature, rate_state)
        actions.append(action)
        prefix = "[DRY RUN] " if action.dry_run else ""
        post_to_signals(f"{prefix}recovery-agent: {action.action} — {action.detail}")

    _save_rate_state(rate_state)
    return actions


def _acquire_singleton_lock():
    """Mirrors the flock-based singleton guard in scheduler.py/relay.py —
    an unmonitored recovery-agent duplicating itself would be exactly the
    class of bug it exists to catch."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.error(
            "another instance already holds the lock — refusing to start "
            "as a duplicate."
        )
        sys.exit(1)
    fd.write(str(os.getpid()))
    fd.flush()
    return fd


def main():
    _lock_fd = _acquire_singleton_lock()
    log.info(f"starting, dry_run={DRY_RUN}, sweep_interval={SWEEP_INTERVAL_SEC}s")
    while True:
        try:
            run_sweep()
        except Exception:
            # log.exception (not str(e)) so a future sweep-time crash
            # leaves a full traceback in logs/recovery-agent.log instead
            # of just a one-line message — the gap that made tonight's
            # exit-status-2 crash-loop undiagnosable from in-container.
            log.exception("sweep error")
        time.sleep(SWEEP_INTERVAL_SEC)


if __name__ == "__main__":
    main()
