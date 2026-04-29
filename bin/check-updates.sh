#!/usr/bin/env bash
# Karakos Update Checker — Weekly task to check for new releases
# Runs automatically via scheduler on Monday at 05:00 UTC

set -euo pipefail

WORKSPACE_ROOT="${WORKSPACE_ROOT:-.}"
KARAKOS_REPO="${KARAKOS_REPO:-mcarmody/karakos-package}"
REPO_URL="${KARAKOS_RELEASES_URL:-https://api.github.com/repos/${KARAKOS_REPO}/releases/latest}"
CURRENT_VERSION=$(cat "$WORKSPACE_ROOT/package.json" | grep '"version"' | head -1 | sed 's/.*"version": "\([^"]*\)".*/\1/')

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }

log "Checking for Karakos updates (current: v$CURRENT_VERSION)..."

# Fetch latest release from GitHub
response=$(curl -s "$REPO_URL" 2>/dev/null || echo '{}')
latest_version=$(echo "$response" | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": "v\?\([^"]*\)".*/\1/')

if [ -z "$latest_version" ]; then
    log "Could not fetch latest version from GitHub"
    exit 0
fi

if [ "$latest_version" != "$CURRENT_VERSION" ]; then
    log "New version available: v$latest_version (current: v$CURRENT_VERSION)"
    log "To update, run: git -C $WORKSPACE_ROOT pull origin main"
else
    log "Already on latest version (v$CURRENT_VERSION)"
fi

exit 0
