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

"${WORKSPACE_ROOT}/bin/poke.sh" \
    --agent "$AGENT" \
    --source "heartbeat" \
    --reply-channel signals \
    "$MESSAGE"
