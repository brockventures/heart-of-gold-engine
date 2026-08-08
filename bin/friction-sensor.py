#!/usr/bin/env python3
"""
friction-sensor.py — reads Marvin's own session transcripts, finds
commands (and command errors) that recur, and proposes them as
candidates for a skill. Design from Amos (Mike's Karakos instance),
described in detail 2026-08-06 in #agent-chat, not shared as code —
independent implementation, built from his description and tested
against his own stated examples (see friction_shell_split.py and
friction_signature.py).

Deliberately dumb by construction, per his framing: this file finds
patterns and writes them to a dated markdown file plus a poke. It does
NOT create skills itself, does not decide anything is worth building —
that judgement is a separate step, done by a human turn reading the
file. "A sensor that could install its own skills would be a sensor
whose false positives become permanent" — his words, and the reasoning
holds regardless of who implements it.

Two independent signals tracked, both over a rolling 7-day window:
  - error signatures: the same command shape failing repeatedly —
    usually the stronger signal something needs fixing.
  - general signatures: the same command shape run repeatedly,
    regardless of outcome — a weaker signal, more often "this could be
    a script" than "this is broken", surfaced separately so the two
    aren't conflated in one number.

Known false-positive trap this was built to avoid, from Amos's own
account: a search command (grep/rg/etc.) exiting 1 because it found
nothing is not a failure. Twice, ninety minutes apart, two sessions,
identical evidence — "the exact shape of a real recurring failure" that
wasn't one. Suppressed here via _is_suppressed_nonfailure().

Incremental: a per-transcript line-cursor state file means each run only
reads what's new since the last one.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from friction_signature import normalize_command

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
STATE_PATH = WORKSPACE_ROOT / "data" / "friction-sensor-state.json"
PROPOSALS_DIR = WORKSPACE_ROOT / "data" / "friction-proposals"

WINDOW_DAYS = 7
# Tunable, not specified by Amos — his account didn't give an exact
# number, just that repetition within the window is the signal. 3
# occurrences in 7 days is a starting point: low enough to catch a
# real weekly-ish pattern, high enough that two coincidental similar
# commands don't trigger a proposal.
ERROR_THRESHOLD = 3
GENERAL_THRESHOLD = 5

# Search-like tools whose exit 1 with empty stderr means "found nothing",
# not "failed". Amos's own example was grep specifically; this list
# covers the same family of tools.
SEARCH_TOOLS = {"grep", "egrep", "fgrep", "rg", "ag", "ack", "pgrep"}


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {"transcripts": {}, "error_signatures": {}, "general_signatures": {}}


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _is_suppressed_nonfailure(segments: List[str], stderr: str) -> bool:
    """True if this 'failure' is actually a search tool finding nothing —
    Amos's specific warning, see module docstring."""
    if not segments:
        return False
    last_tokens = segments[-1].split()
    if not last_tokens:
        return False
    prog = last_tokens[0].rstrip("/").split("/")[-1]
    return prog in SEARCH_TOOLS and not (stderr or "").strip()


def iter_bash_events(transcript_path: Path, start_line: int):
    """Yield (line_number, tool_use_id, command, description) for Bash
    tool_use events, and separately (line_number, tool_use_id, is_error,
    stderr) for their paired tool_result — as a single pass, since
    results reference the preceding tool_use by id within the same
    transcript."""
    pending_calls: Dict[str, Dict[str, Any]] = {}
    with open(transcript_path, "r", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            if lineno <= start_line:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = data.get("message", {}) or {}
            # `content` is a list of typed blocks for tool calls/results,
            # but can also be a bare string for a simple text-only turn —
            # hit this against real transcripts, not just the synthetic
            # test data.
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue

            if data.get("type") == "assistant":
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use" and block.get("name") == "Bash":
                        tool_input = block.get("input", {}) or {}
                        pending_calls[block.get("id")] = {
                            "command": tool_input.get("command", ""),
                            "timestamp": data.get("timestamp", ""),
                        }
            elif data.get("type") == "user":
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    call = pending_calls.pop(block.get("tool_use_id"), None)
                    if not call:
                        continue
                    is_error = bool(block.get("is_error", False))
                    tool_result = data.get("toolUseResult", {})
                    stderr = ""
                    if isinstance(tool_result, dict):
                        stderr = tool_result.get("stderr", "") or ""
                    yield lineno, call["command"], call["timestamp"], is_error, stderr


def prune_old(occurrences: List[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    cutoff = now - timedelta(days=WINDOW_DAYS)
    kept = []
    for occ in occurrences:
        try:
            ts = datetime.fromisoformat(occ["ts"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts >= cutoff:
            kept.append(occ)
    return kept


def scan() -> Dict[str, Any]:
    state = load_state()
    now = datetime.now(timezone.utc)

    if not CLAUDE_PROJECTS_DIR.exists():
        return state

    for transcript_path in CLAUDE_PROJECTS_DIR.rglob("*.jsonl"):
        key = str(transcript_path)
        start_line = state["transcripts"].get(key, {}).get("last_line", 0)
        last_line = start_line

        for lineno, command, ts, is_error, stderr in iter_bash_events(transcript_path, start_line):
            last_line = lineno
            if not command:
                continue
            normalized = normalize_command(command)
            if not normalized.signature:
                continue

            occ = {"ts": ts or now.isoformat(), "transcript": key, "line": lineno}

            # General repetition — every Bash call, success or failure.
            bucket = state["general_signatures"].setdefault(
                normalized.signature, {"occurrences": []}
            )
            bucket["occurrences"].append(occ)

            # Error repetition — only real failures, with the
            # non-failure trap suppressed.
            if is_error:
                if _is_suppressed_nonfailure(normalized.segments, stderr):
                    continue
                failed_segment = normalized.segments[-1] if normalized.segments else command
                err_occ = dict(occ)
                err_occ["failed_segment"] = failed_segment
                err_bucket = state["error_signatures"].setdefault(
                    normalized.signature, {"occurrences": []}
                )
                err_bucket["occurrences"].append(err_occ)

        state["transcripts"][key] = {"last_line": last_line}

    # Prune both buckets to the rolling window.
    for sig, bucket in list(state["error_signatures"].items()):
        bucket["occurrences"] = prune_old(bucket["occurrences"], now)
        if not bucket["occurrences"]:
            del state["error_signatures"][sig]
    for sig, bucket in list(state["general_signatures"].items()):
        bucket["occurrences"] = prune_old(bucket["occurrences"], now)
        if not bucket["occurrences"]:
            del state["general_signatures"][sig]

    return state


def write_proposals(state: Dict[str, Any]) -> Optional[Path]:
    error_hits = {
        sig: b for sig, b in state["error_signatures"].items()
        if len(b["occurrences"]) >= ERROR_THRESHOLD
    }
    general_hits = {
        sig: b for sig, b in state["general_signatures"].items()
        if len(b["occurrences"]) >= GENERAL_THRESHOLD
    }
    if not error_hits and not general_hits:
        return None

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = PROPOSALS_DIR / f"{date_str}.md"
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)

    lines = [f"# Friction sensor proposals — {date_str}", ""]
    if error_hits:
        lines.append("## Recurring errors (stronger signal)")
        lines.append("")
        for sig, bucket in sorted(error_hits.items(), key=lambda kv: -len(kv[1]["occurrences"])):
            occs = bucket["occurrences"]
            last = occs[-1]
            lines.append(f"- `{sig}` — {len(occs)}x in the last {WINDOW_DAYS} days")
            lines.append(f"  - last failed segment: `{last.get('failed_segment', sig)}`")
            lines.append(f"  - last seen: {last['ts']}")
        lines.append("")
    if general_hits:
        lines.append("## Recurring commands (weaker signal — maybe a script, maybe nothing)")
        lines.append("")
        for sig, bucket in sorted(general_hits.items(), key=lambda kv: -len(kv[1]["occurrences"])):
            occs = bucket["occurrences"]
            lines.append(f"- `{sig}` — {len(occs)}x in the last {WINDOW_DAYS} days, last seen {occs[-1]['ts']}")
        lines.append("")

    out_path.write_text("\n".join(lines))
    return out_path


def main():
    state = scan()
    save_state(state)
    proposal_path = write_proposals(state)
    if proposal_path:
        print(json.dumps({"status": "proposals_written", "path": str(proposal_path)}))
    else:
        print(json.dumps({"status": "no_proposals", "reason": "nothing crossed threshold"}))


if __name__ == "__main__":
    main()
