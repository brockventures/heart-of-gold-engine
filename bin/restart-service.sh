#!/usr/bin/env bash
# restart-service.sh — narrow, allowlisted wrapper around `systemctl` so
# Marvin (running as the unprivileged `karakos` user) can recover a handful
# of specific services without needing interactive sudo.
#
# SECURITY: this script is meant to be invoked via a sudoers NOPASSWD entry
# scoped to this exact path. That only stays safe if this file is owned by
# root and NOT writable by `karakos` (chown root:root, chmod 755) — if the
# `karakos` user can edit this script, granting sudo to run it is equivalent
# to granting unrestricted root. The unit and action allowlists below are
# the entire security boundary; don't loosen them without thinking hard.
#
# Deliberately excludes karakos-agent-server.service — that's Marvin's own
# process. Restarting yourself mid-conversation, accidentally or otherwise,
# is a different risk category than recovering a sibling service and needs
# a human in the loop regardless. Same reasoning system/reload-on-commit.py
# already applies to itself.
#
# Usage: restart-service.sh <start|restart> <unit-name>

set -euo pipefail

SYSTEMCTL=/usr/bin/systemctl

ALLOWED_ACTIONS=(start restart)
ALLOWED_UNITS=(
  karakos-dashboard.service
  karakos-relay.service
  karakos-scheduler.service
  karakos-recovery-agent.service
)

log() {
  logger -t restart-service.sh -- "$1" 2>/dev/null || true
  echo "[restart-service.sh] $1" >&2
}

if [ "$#" -ne 2 ]; then
  log "REJECTED: expected exactly 2 args (action, unit), got: $*"
  echo "usage: $0 <start|restart> <unit-name>" >&2
  exit 2
fi

action="$1"
unit="$2"

action_ok=0
for a in "${ALLOWED_ACTIONS[@]}"; do
  [ "$action" = "$a" ] && action_ok=1 && break
done
if [ "$action_ok" -ne 1 ]; then
  log "REJECTED: action '$action' not in allowlist (${ALLOWED_ACTIONS[*]})"
  exit 3
fi

unit_ok=0
for u in "${ALLOWED_UNITS[@]}"; do
  [ "$unit" = "$u" ] && unit_ok=1 && break
done
if [ "$unit_ok" -ne 1 ]; then
  log "REJECTED: unit '$unit' not in allowlist (${ALLOWED_UNITS[*]}) — note karakos-agent-server.service is deliberately excluded"
  exit 4
fi

log "OK: running '$action' on '$unit'"
exec "$SYSTEMCTL" "$action" "$unit"
