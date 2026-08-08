#!/usr/bin/env python3
"""
friction_shell_split.py — split a shell command line into segments on
;, &&, ||, | and newlines, respecting quotes, escapes, $(...), and
heredocs.

This is the part of the friction sensor Amos (Mike's Karakos instance)
warned about hardest: a naive split on those operators treats a heredoc
body's internal punctuation as real shell structure. `python3 - <<'PY'
...some text with a semicolon; here... PY` is ONE command; a naive
splitter sees the semicolon inside the heredoc body and cuts it in half.
His fix, described 2026-08-06, not shared as code — this is an
independent implementation of the same requirement, built from his
description and tested against his own stated examples.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class Segment:
    text: str


def split_shell_command(command: str) -> List[str]:
    """Split on top-level ;, &&, ||, |, and newlines. Returns segments in
    order, each stripped. Never splits inside single quotes, double
    quotes, $(...), or a heredoc body."""
    segments: List[str] = []
    current: List[str] = []

    i = 0
    n = len(command)
    paren_depth = 0
    in_single = False
    in_double = False
    heredoc_delim = None  # active heredoc terminator we're scanning for
    heredoc_strip_tabs = False

    def flush():
        text = "".join(current).strip()
        if text:
            segments.append(text)
        current.clear()

    while i < n:
        ch = command[i]

        # -- inside an active heredoc body: only look for the terminator
        # line, everything else is opaque text, not shell structure.
        if heredoc_delim is not None:
            current.append(ch)
            if ch == "\n":
                line_start = i + 1
                line_end = command.find("\n", line_start)
                line = command[line_start:line_end if line_end != -1 else n]
                candidate = line.strip() if heredoc_strip_tabs else line
                if candidate == heredoc_delim:
                    consumed = line if line_end == -1 else line + "\n"
                    current.append(consumed)
                    i = n if line_end == -1 else line_end + 1
                    heredoc_delim = None
                    heredoc_strip_tabs = False
                    continue
            i += 1
            continue

        if in_single:
            current.append(ch)
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            current.append(ch)
            if ch == "\\" and i + 1 < n:
                current.append(command[i + 1])
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            current.append(ch)
            i += 1
            continue

        if ch == '"':
            in_double = True
            current.append(ch)
            i += 1
            continue

        if ch == "\\" and i + 1 < n:
            current.append(ch)
            current.append(command[i + 1])
            i += 2
            continue

        if command[i:i + 2] == "$(":
            paren_depth += 1
            current.append(command[i:i + 2])
            i += 2
            continue

        if ch == "(":
            paren_depth += 1
            current.append(ch)
            i += 1
            continue

        if ch == ")" and paren_depth > 0:
            paren_depth -= 1
            current.append(ch)
            i += 1
            continue

        # Heredoc start: <<DELIM, <<-DELIM, <<'DELIM', <<"DELIM" — only
        # recognised at top level (not inside quotes/parens, handled
        # above since those branches already consumed their chars).
        if paren_depth == 0 and command[i:i + 2] == "<<":
            m = re.match(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", command[i:])
            if m:
                heredoc_delim = m.group(2)
                heredoc_strip_tabs = command[i:i + 3] == "<<-"
                current.append(m.group(0))
                i += len(m.group(0))
                continue

        if paren_depth == 0:
            if command[i:i + 2] == "&&":
                flush()
                i += 2
                continue
            if command[i:i + 2] == "||":
                flush()
                i += 2
                continue
            if ch == ";":
                flush()
                i += 1
                continue
            if ch == "|":
                flush()
                i += 1
                continue
            if ch == "\n":
                flush()
                i += 1
                continue

        current.append(ch)
        i += 1

    flush()
    return segments


# -- selftest -------------------------------------------------------------
def _selftest() -> int:
    fails = 0

    def check(label, got, want):
        nonlocal fails
        ok = got == want
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n        got:  {got!r}\n        want: {want!r}"))

    print("── friction_shell_split selftest ──")

    check("simple &&",
          split_shell_command("cd /x && ls"), ["cd /x", "ls"])

    check("Amos's own example",
          split_shell_command("bin/repo-path.sh nautilus && gh repo view --json nameWithOwner"),
          ["bin/repo-path.sh nautilus", "gh repo view --json nameWithOwner"])

    check("pipe",
          split_shell_command("crontab -l | grep -i strand"),
          ["crontab -l", "grep -i strand"])

    check("semicolons inside a heredoc body do not split",
          split_shell_command("python3 - <<'PY'\nprint(1); print(2)\nPY"),
          ["python3 - <<'PY'\nprint(1); print(2)\nPY"])

    check("semicolon inside single quotes does not split",
          split_shell_command("echo 'a; b' && echo done"),
          ["echo 'a; b'", "echo done"])

    check("semicolon inside double quotes does not split",
          split_shell_command('echo "a; b" && echo done'),
          ['echo "a; b"', "echo done"])

    check("$(...) with an internal ; does not split at top level",
          split_shell_command("echo $(echo a; echo b) && echo done"),
          ["echo $(echo a; echo b)", "echo done"])

    check("multi-segment && chain",
          split_shell_command("a && b && c"),
          ["a", "b", "c"])

    check("mixed operators",
          split_shell_command("a; b && c || d"),
          ["a", "b", "c", "d"])

    check("newline-separated commands split like semicolons",
          split_shell_command("echo a\necho b"),
          ["echo a", "echo b"])

    check("escaped semicolon does not split",
          split_shell_command(r"echo a\; b && echo c"),
          [r"echo a\; b", "echo c"])

    print("PASS" if not fails else f"FAIL  {fails} case(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
