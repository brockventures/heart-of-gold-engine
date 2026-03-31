#!/usr/bin/env bash
set -euo pipefail

export WORKSPACE_ROOT=/workspace

# Ensure data directories exist
mkdir -p \
    "$WORKSPACE_ROOT/data/messages" \
    "$WORKSPACE_ROOT/data/memory" \
    "$WORKSPACE_ROOT/data/health" \
    "$WORKSPACE_ROOT/logs/agent-streams" \
    "$WORKSPACE_ROOT/logs/session-summaries" \
    "$WORKSPACE_ROOT/inbox"

# Create inbox dirs for each configured agent
if [ -f "$WORKSPACE_ROOT/config/agents.json" ]; then
    for agent in $(python3 -c "import json; print(' '.join(json.load(open('$WORKSPACE_ROOT/config/agents.json'))['agents'].keys()))"); do
        mkdir -p "$WORKSPACE_ROOT/inbox/$agent"
        mkdir -p "$WORKSPACE_ROOT/agents/$agent/inbox"
        mkdir -p "$WORKSPACE_ROOT/agents/$agent/journal"
    done
fi

# Initialize git if not already
if [ ! -d "$WORKSPACE_ROOT/.git" ]; then
    cd "$WORKSPACE_ROOT"
    git init
    git add -A
    git commit -m "Initial commit" --allow-empty
fi

# Install protected paths git hook
if [ -f "$WORKSPACE_ROOT/system/check-protected-paths.py" ]; then
    cp "$WORKSPACE_ROOT/system/install-hooks.sh" "$WORKSPACE_ROOT/.git/hooks/pre-commit" 2>/dev/null || true
    chmod +x "$WORKSPACE_ROOT/.git/hooks/pre-commit" 2>/dev/null || true
fi

exec supervisord -c "$WORKSPACE_ROOT/config/supervisord.conf"
