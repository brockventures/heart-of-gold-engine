#!/usr/bin/env bash
# Post-commit hook for Karakos repository
# Bounces relay/scheduler when a commit changes the files that govern
# their behavior, so code landing on disk doesn't silently diverge from
# code actually running. See system/reload-on-commit.py for the mapping
# and reasoning (deliberately excludes agent-server.py — see its docstring).
# Called automatically by git after each commit.

set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-.}"
PYTHON="${PYTHON:-python3}"

# Post-commit failures must never look like the commit itself failed —
# the commit has already happened by the time this runs. Swallow errors
# after logging rather than letting git print a scary non-zero exit.
"$PYTHON" "$WORKSPACE_ROOT/system/reload-on-commit.py" || true
