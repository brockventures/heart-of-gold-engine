#!/usr/bin/env bash
# Check for Karakos updates against GitHub releases.
# Called weekly by scheduler. Posts to #signals if update available.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${WORKSPACE_ROOT:-$(dirname "$SCRIPT_DIR")}"

# Load current version
CONFIG_FILE="$WORKSPACE/.karakos/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Config not found: $CONFIG_FILE" >&2
    exit 1
fi

CURRENT_VERSION=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('version', '0.0.0'))")

# Check GitHub for latest release
REPO="mcarmody/karakos"
LATEST=$(curl -s "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tag_name','').lstrip('v'))" 2>/dev/null || echo "")

if [ -z "$LATEST" ]; then
    echo "Could not fetch latest version from GitHub" >&2
    exit 0  # Don't fail — network issues are expected
fi

# Compare versions (simple string comparison — works for semver)
if [ "$LATEST" = "$CURRENT_VERSION" ]; then
    echo "Up to date: v$CURRENT_VERSION"
    exit 0
fi

# Check if latest is newer (using sort -V for version comparison)
NEWER=$(printf '%s\n%s\n' "$CURRENT_VERSION" "$LATEST" | sort -V | tail -n1)

if [ "$NEWER" = "$LATEST" ] && [ "$NEWER" != "$CURRENT_VERSION" ]; then
    MESSAGE="Karakos v${LATEST} available (you're on v${CURRENT_VERSION}). See upgrade instructions: \`docs/UPGRADING.md\`"
    echo "$MESSAGE"

    # Post to signals channel if poke.sh is available
    POKE="$WORKSPACE/bin/poke.sh"
    if [ -x "$POKE" ]; then
        "$POKE" --reply-channel signals "$MESSAGE"
    fi
else
    echo "Up to date: v$CURRENT_VERSION (latest: v$LATEST)"
fi
