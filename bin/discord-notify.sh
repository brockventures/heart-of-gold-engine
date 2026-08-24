#!/usr/bin/env bash
# discord-notify.sh — Post a message to a Discord channel, optionally with
# file attachments.
#
# Usage:
#   discord-notify.sh general "System update complete"
#   discord-notify.sh signals "⚠️ Agent crashed"
#   discord-notify.sh agent-chat "spec attached" /path/to/spec.md [more files...]
#
# Attachments (2026-08-24): any args after the message are treated as local
# file paths and uploaded alongside the message via multipart/form-data
# (Discord's payload_json + files[n] convention). Missing files fail loudly
# (exit 1) rather than silently posting text-only — a caller asking for an
# attachment that didn't make it should know, not get a quiet partial send.

set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-/workspace}"

CHANNEL_NAME="${1:-}"
MESSAGE="${2:-}"
shift 2 2>/dev/null || true
FILES=("$@")

if [[ -z "$CHANNEL_NAME" || -z "$MESSAGE" ]]; then
    echo "Usage: discord-notify.sh CHANNEL_NAME \"message\" [file...]" >&2
    exit 1
fi

if [[ ${#FILES[@]} -gt 0 ]]; then
    for f in "${FILES[@]}"; do
        if [[ ! -f "$f" ]]; then
            echo "Error: attachment not found: $f" >&2
            exit 1
        fi
    done
fi

# Resolve channel name to ID
CHANNEL_ID="$CHANNEL_NAME"
if [[ -f "$WORKSPACE_ROOT/config/channels.json" ]]; then
    RESOLVED=$(python3 -c "
import json
cfg = json.load(open('$WORKSPACE_ROOT/config/channels.json'))
ch = cfg.get('channels', {}).get('$CHANNEL_NAME', {})
print(ch.get('id', '$CHANNEL_NAME'))
" 2>/dev/null || echo "$CHANNEL_NAME")
    CHANNEL_ID="$RESOLVED"
fi

# Get first available bot token
BOT_TOKEN=""
if [[ -f "$WORKSPACE_ROOT/config/agents.json" ]]; then
    BOT_TOKEN=$(python3 -c "
import json, os
cfg = json.load(open('$WORKSPACE_ROOT/config/agents.json'))
for name, info in cfg.get('agents', {}).items():
    env_var = info.get('discord_bot_token_env', '')
    if env_var:
        token = os.environ.get(env_var, '')
        if token:
            print(token)
            break
" 2>/dev/null || echo "")
fi

if [[ -z "$BOT_TOKEN" ]]; then
    echo "Error: no Discord bot token available" >&2
    exit 1
fi

# Post to Discord
if [[ ${#FILES[@]} -gt 0 ]]; then
    # Multipart upload: payload_json carries the message body, files[n]
    # carries each attachment. Discord's documented convention for
    # POST /channels/{id}/messages with attachments.
    CURL_ARGS=(-sf -X POST "https://discord.com/api/v10/channels/$CHANNEL_ID/messages"
        -H "Authorization: Bot $BOT_TOKEN"
        -F "payload_json=$(jq -n --arg content "$MESSAGE" '{content: $content}');type=application/json")
    i=0
    for f in "${FILES[@]}"; do
        CURL_ARGS+=(-F "files[$i]=@${f}")
        i=$((i + 1))
    done
    curl "${CURL_ARGS[@]}" > /dev/null
else
    curl -sf -X POST "https://discord.com/api/v10/channels/$CHANNEL_ID/messages" \
        -H "Authorization: Bot $BOT_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$(jq -n --arg content "$MESSAGE" '{content: $content}')" > /dev/null
fi

if [[ ${#FILES[@]} -gt 0 ]]; then
    echo "Posted to #$CHANNEL_NAME with ${#FILES[@]} attachment(s)"
else
    echo "Posted to #$CHANNEL_NAME"
fi
