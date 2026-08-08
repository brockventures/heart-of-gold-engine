#!/usr/bin/env bash
# Heartbeat Script — Periodic health check and task reminder for agents

set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-/workspace}"
AGENT="${1:-}"

if [ -z "$AGENT" ]; then
    echo "Usage: heartbeat.sh AGENT_NAME" >&2
    exit 1
fi

TIMESTAMP=$(date '+%H:%M')
MESSAGE="[HEARTBEAT] ${TIMESTAMP} — Check system health, inbox, and pending tasks."

# Low-cost mail check: read-only IMAP poll against the Marvin Gmail label,
# a few seconds of network I/O, no LLM call involved. Only adds to the
# heartbeat message when there's actually something new, and only a
# compact from/subject summary — not full bodies — so an empty inbox
# costs nothing extra and a full one doesn't bloat the heartbeat context.
# Added 2026-08-06 per Ian, wired at the script level rather than left to
# the agent to remember, so it happens every time regardless.
MAIL_CHECK=$(WORKSPACE_ROOT="$WORKSPACE_ROOT" TOOL_ARGS='{}' \
    python3 "${WORKSPACE_ROOT}/skills/email/scripts/read_marvin_folder.py" 2>/dev/null || echo '{}')
MAIL_COUNT=$(echo "$MAIL_CHECK" | jq -r '.new_message_count // 0' 2>/dev/null || echo 0)
if [ "$MAIL_COUNT" -gt 0 ]; then
    MAIL_SUMMARY=$(echo "$MAIL_CHECK" | jq -r '.messages[] | "  - from \(.from): \(.subject)"' 2>/dev/null)
    MESSAGE="${MESSAGE}
New mail (${MAIL_COUNT}) in the Marvin folder:
${MAIL_SUMMARY}"
fi

# Context-window fill %, added 2026-08-07 per Ian — "keep a tab on it to see
# if we're hitting limits too regularly." Reads agent-server's own /agents
# endpoint (same estimate_context_tokens() the compaction trigger uses).
# Empty until this agent's subprocess has completed at least one turn since
# agent-server last started — silently omitted rather than shown as 0%,
# so a fresh restart doesn't read as "context is empty."
#
# warning_level (soft/hard/critical, added same day as the tiered warnings)
# gets a visible tag when non-"none" — hard/critical should be rare given
# compaction fires at the hard tier, so seeing either in a heartbeat is
# worth a human noticing without digging into #signals separately.
AGENT_SERVER_PORT="${AGENT_SERVER_PORT:-18791}"
CONTEXT_CHECK=$(curl -s -H "Authorization: Bearer ${AGENT_SERVER_TOKEN:-}" \
    "http://localhost:${AGENT_SERVER_PORT}/agents" 2>/dev/null || echo '{}')
CONTEXT_PCT=$(echo "$CONTEXT_CHECK" | jq -r --arg agent "$AGENT" \
    '.agents[]? | select(.name == $agent) | .context_usage.pct // empty' 2>/dev/null)
CONTEXT_LEVEL=$(echo "$CONTEXT_CHECK" | jq -r --arg agent "$AGENT" \
    '.agents[]? | select(.name == $agent) | .context_usage.warning_level // empty' 2>/dev/null)
if [ -n "$CONTEXT_PCT" ]; then
    LEVEL_TAG=""
    case "$CONTEXT_LEVEL" in
        soft) LEVEL_TAG=" [soft warning]" ;;
        hard) LEVEL_TAG=" [hard warning]" ;;
        critical) LEVEL_TAG=" [CRITICAL]" ;;
    esac
    MESSAGE="${MESSAGE}
Context: ${CONTEXT_PCT}% of window${LEVEL_TAG}"
fi

"${WORKSPACE_ROOT}/bin/poke.sh" \
    --agent "$AGENT" \
    --source "heartbeat" \
    --reply-channel signals \
    "$MESSAGE"
