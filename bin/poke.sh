#!/usr/bin/env bash
# Poke Script — Inter-agent and system-to-agent messaging
#
# Usage: poke.sh [OPTIONS] MESSAGE
#
# Options:
#   --agent NAME           Target agent (default: primary agent from config)
#   --source LABEL         Source label shown as author (default: "system")
#   --reply-channel NAME   Channel where agent's response posts (default: "general")
#   --silent               Post to channel_id "0" — agent processes but no Discord post

set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-/workspace}"
AGENT_SERVER_TOKEN="${AGENT_SERVER_TOKEN:-}"
AGENT_SERVER_PORT="${AGENT_SERVER_PORT:-18791}"
CHANNELS_CONFIG="${WORKSPACE_ROOT}/config/channels.json"
AGENTS_CONFIG="${WORKSPACE_ROOT}/config/agents.json"

# Defaults
AGENT=""
SOURCE="system"
REPLY_CHANNEL="general"
SILENT=false
MESSAGE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --agent)
            AGENT="$2"
            shift 2
            ;;
        --source)
            SOURCE="$2"
            shift 2
            ;;
        --reply-channel)
            REPLY_CHANNEL="$2"
            shift 2
            ;;
        --silent)
            SILENT=true
            shift
            ;;
        *)
            MESSAGE="$1"
            shift
            ;;
    esac
done

# Validate message
if [ -z "$MESSAGE" ]; then
    echo "Error: No message provided" >&2
    echo "Usage: poke.sh [OPTIONS] MESSAGE" >&2
    exit 1
fi

# Get primary agent if not specified
if [ -z "$AGENT" ]; then
    if [ ! -f "$AGENTS_CONFIG" ]; then
        echo "Error: Agents config not found: $AGENTS_CONFIG" >&2
        exit 1
    fi
    AGENT=$(jq -r '.agents | keys[0]' "$AGENTS_CONFIG")
fi

# Get channel ID
CHANNEL_ID="0"
if [ "$SILENT" = false ]; then
    if [ ! -f "$CHANNELS_CONFIG" ]; then
        echo "Error: Channels config not found: $CHANNELS_CONFIG" >&2
        exit 1
    fi
    CHANNEL_ID=$(jq -r ".channels.\"$REPLY_CHANNEL\".id // \"0\"" "$CHANNELS_CONFIG")
fi

# Generate message ID
TIMESTAMP=$(date +%s)
PID=$$
RANDOM_SUFFIX=$(( RANDOM % 65536 ))
MESSAGE_ID="poke-${TIMESTAMP}-${PID}-${RANDOM_SUFFIX}"

# Build payload
PAYLOAD=$(jq -n \
    --arg agent "$AGENT" \
    --arg channel "$REPLY_CHANNEL" \
    --arg channel_id "$CHANNEL_ID" \
    --arg source "$SOURCE" \
    --arg content "$MESSAGE" \
    --arg message_id "$MESSAGE_ID" \
    '{
        agent: $agent,
        channel: $channel,
        channel_id: $channel_id,
        server: "local",
        author: $source,
        author_id: "0",
        is_bot: true,
        content: $content,
        message_id: $message_id,
        mentions_agent: true
    }')

# Send to agent server
RESPONSE=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Authorization: Bearer ${AGENT_SERVER_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" \
    "http://localhost:${AGENT_SERVER_PORT}/message")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$HTTP_CODE" != "202" ]; then
    echo "Error: HTTP $HTTP_CODE" >&2
    echo "$BODY" >&2
    exit 1
fi

echo "Poked $AGENT (message_id: $MESSAGE_ID)"
