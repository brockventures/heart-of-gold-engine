#!/usr/bin/env python3
"""
friction_signature.py — normalise a shell command into a repetition
signature. Design from Amos (Mike's Karakos instance), described
2026-08-06, not shared as code — independent implementation built from
his description and tested against his own stated examples.

Rules, in order:
  1. A leading `cd <path> &&` is stripped from the signature but the
     target is remembered as scope — for git it folds back in
     (`[path] git status`), so per-repo flows stay distinguishable from
     generic git use elsewhere.
  2. Leading `VAR=value` assignments are skipped — keeps literal secret
     values out of signatures too.
  3. Shell preamble (`set`, `shopt`, `export`, `umask` with no other
     purpose) is skipped when choosing which segment represents "the
     real command" — Amos's own bug: `set -euo pipefail` on line 1 used
     to sign as the bare builtin `set`, hiding the actual command on
     line 2. Sixty calls in a week, all invisible, in his account of it.
  4. Known wrappers recurse into what they wrap: `timeout 300 npx vitest
     run` signs as `npx vitest`; `ssh -o X host cmd` signs as `ssh host`.
  5. Otherwise: program basename + first non-flag argument. `git status
     -s` and `git status --short` both sign as `git status`.

Crude on purpose (his framing, and it's the right one) — this finds *the
same thing done again*, not a semantically perfect classification.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import List, Optional

from friction_shell_split import split_shell_command

ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
PREAMBLE_BUILTINS = {"set", "shopt", "umask"}
SSH_VALUE_FLAGS = {"-o", "-i", "-p", "-l", "-F", "-J", "-L", "-R", "-D",
                    "-W", "-B", "-b", "-c", "-m", "-Q", "-E"}


@dataclass
class Normalized:
    signature: str
    scope: Optional[str]
    segments: List[str]


def _safe_split(text: str) -> List[str]:
    try:
        return shlex.split(text)
    except ValueError:
        # Unbalanced quote or similar — fall back to naive whitespace
        # split rather than crashing the whole scan over one odd line.
        return text.split()


def _is_preamble(segment: str) -> bool:
    tokens = _safe_split(segment)
    return bool(tokens) and tokens[0] in PREAMBLE_BUILTINS


def _strip_assignments(tokens: List[str]) -> List[str]:
    tokens = list(tokens)
    while tokens and ASSIGNMENT_RE.match(tokens[0]):
        tokens.pop(0)
    return tokens


def _normalize_tokens(tokens: List[str]) -> Optional[str]:
    tokens = _strip_assignments(tokens)
    if not tokens:
        return None

    prog = tokens[0]
    prog_base = prog.rstrip("/").split("/")[-1]

    if prog_base == "timeout":
        rest = tokens[1:]
        # skip flags and the numeric duration
        while rest and (rest[0].startswith("-") or rest[0].replace(".", "", 1).isdigit()):
            rest.pop(0)
        return _normalize_tokens(rest) if rest else prog_base

    if prog_base == "ssh":
        rest = tokens[1:]
        while rest and rest[0].startswith("-"):
            flag = rest.pop(0)
            if flag in SSH_VALUE_FLAGS and rest:
                rest.pop(0)
        host = rest[0] if rest else ""
        return f"ssh {host}" if host else "ssh"

    first_nonflag = next((t for t in tokens[1:] if not t.startswith("-")), None)
    return f"{prog_base} {first_nonflag}" if first_nonflag else prog_base


def normalize_command(command: str) -> Normalized:
    segments = split_shell_command(command)
    if not segments:
        return Normalized(signature="", scope=None, segments=[])

    working = list(segments)
    scope: Optional[str] = None

    first_tokens = _safe_split(working[0])
    if len(first_tokens) >= 2 and first_tokens[0] == "cd":
        scope = first_tokens[1]
        working = working[1:]

    real_segments = [s for s in working if not _is_preamble(s)]
    if not real_segments:
        real_segments = working  # nothing but preamble — rare, don't crash

    first_real = real_segments[0] if real_segments else ""
    sig = _normalize_tokens(_safe_split(first_real)) if first_real else None
    sig = sig or ""

    if scope and sig.startswith("git "):
        sig = f"[{scope}] {sig}"

    return Normalized(signature=sig, scope=scope, segments=segments)


# -- selftest -------------------------------------------------------------
def _selftest() -> int:
    fails = 0

    def check(label, got, want):
        nonlocal fails
        ok = got == want
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n        got:  {got!r}\n        want: {want!r}"))

    print("── friction_signature selftest ──")

    check("git status -s / --short collapse to the same signature",
          normalize_command("git status -s").signature,
          normalize_command("git status --short").signature)
    check("...and the signature is what's expected",
          normalize_command("git status -s").signature, "git status")

    check("timeout wrapper recurses",
          normalize_command("timeout 300 npx vitest run").signature,
          "npx vitest")

    check("ssh wrapper collapses to program + host",
          normalize_command("ssh -o StrictHostKeyChecking=no myhost 'do a thing'").signature,
          "ssh myhost")

    check("leading VAR=value assignment stripped",
          normalize_command("FOO=bar git status").signature, "git status")

    check("set preamble on line 1 does not hide the real command on line 2",
          normalize_command("set -euo pipefail\ngit status").signature,
          "git status")

    check("cd target remembered as scope, folds into git signature",
          normalize_command("cd /workspace/repo-a && git status").signature,
          "[/workspace/repo-a] git status")

    check("cd scope does NOT fold into non-git commands",
          normalize_command("cd /workspace/repo-a && npm test").signature,
          "npm test")

    check("Amos's own repo-path example",
          normalize_command("bin/repo-path.sh nautilus && gh repo view --json nameWithOwner").signature,
          "repo-path.sh nautilus")

    print("PASS" if not fails else f"FAIL  {fails} case(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
