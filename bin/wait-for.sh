#!/usr/bin/env bash
# wait-for.sh — poll a real condition instead of guessing a fixed sleep.
#
# Usage: wait-for.sh [--timeout SECONDS] [--interval SECONDS] -- COMMAND [ARGS...]
#
# Runs COMMAND repeatedly (via `bash -c`, so shell syntax and pipes work)
# until it exits 0, or until --timeout elapses. Exits 0 as soon as the
# condition is met, exits 1 on timeout, exits 2 on bad usage. Default
# interval 2s, default timeout 60s.
#
# Why this exists: a fixed `sleep N` before checking something is a guess
# — too short and you race the thing you're waiting for, too long and you
# waste time on every run that didn't need it. Polling a real condition
# removes the guess. Ported from Amos (Mike's Karakos instance) 2026-08-06;
# this is the general form of the "until <check>; do sleep 2; done" pattern.
#
# Example: wait-for.sh --timeout 30 -- 'curl -sf http://localhost:8080/health'

set -euo pipefail

TIMEOUT=60
INTERVAL=2

while [[ $# -gt 0 ]]; do
    case "$1" in
        --timeout)
            TIMEOUT="$2"; shift 2 ;;
        --interval)
            INTERVAL="$2"; shift 2 ;;
        --)
            shift; break ;;
        *)
            break ;;
    esac
done

if [[ $# -eq 0 ]]; then
    echo "Usage: wait-for.sh [--timeout SECONDS] [--interval SECONDS] -- COMMAND [ARGS...]" >&2
    exit 2
fi

deadline=$(( $(date +%s) + TIMEOUT ))
attempt=0

while true; do
    attempt=$(( attempt + 1 ))
    if bash -c "$*" >/dev/null 2>&1; then
        echo "wait-for: condition met after ${attempt} attempt(s)" >&2
        exit 0
    fi
    now=$(date +%s)
    if (( now >= deadline )); then
        echo "wait-for: timed out after ${TIMEOUT}s (${attempt} attempt(s)): $*" >&2
        exit 1
    fi
    sleep "$INTERVAL"
done
