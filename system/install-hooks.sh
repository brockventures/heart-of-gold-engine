#!/usr/bin/env bash
# Pre-commit hook for Karakos repository
# Checks staged files against protected paths configuration
# Called automatically by git before each commit

set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-.}"
PYTHON="${PYTHON:-python3}"

# Run protected paths checker
"$PYTHON" "$WORKSPACE_ROOT/system/check-protected-paths.py" --staged
