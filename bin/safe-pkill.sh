#!/usr/bin/env bash
# safe-pkill.sh — pkill -f equivalent that refuses to signal its own ancestry.
#
# `pkill -f PATTERN` matches against the FULL command line of every
# process on the system, including the shell that invoked pkill and
# everything above it. If PATTERN is broad enough to also match your own
# shell's command line — easy to do by accident, e.g. a wrapper script
# re-execing itself, or a heredoc/inline command containing the pattern
# text — you can kill the very session that's running the kill command,
# mid-command. Amos (Mike's Karakos instance) hit this for real; this
# exists because of that, ported into Marvin's bin/ 2026-08-06.
#
# Implemented against /proc directly rather than pgrep/pkill: this
# environment has neither (no procps package — confirmed while writing
# this, `ps`/`pgrep`/`pkill` all absent). Signaling uses bash's builtin
# `kill`, which does exist without procps.
#
# Usage: safe-pkill.sh [-SIGNAL] PATTERN   (SIGNAL defaults to TERM)
#
# Read-only match first, filter out any PID in this process's own
# ancestry chain, only then send the real signal to what's left. If every
# match was in the ancestry, refuse outright rather than silently doing
# nothing or signaling the wrong thing.
#
# Verified live 2026-08-06: in this harness, the literal text of whatever
# command you run gets embedded in an ancestor shell's own argv (visible
# via /proc), so almost any PATTERN you test with will show at least one
# "refusing to signal own ancestry" hit against a harness wrapper process.
# That's this script working correctly, not a bug — it's the exact
# collision it exists to catch, happening on essentially every invocation
# here rather than being a rare accident.

set -euo pipefail

if [[ $# -eq 0 ]]; then
    echo "Usage: safe-pkill.sh [-SIGNAL] PATTERN" >&2
    exit 1
fi

SIGNAL="TERM"
if [[ "$1" == -* ]]; then
    SIGNAL="${1#-}"
    shift
fi

if [[ $# -ne 1 ]]; then
    echo "Usage: safe-pkill.sh [-SIGNAL] PATTERN" >&2
    exit 1
fi
PATTERN="$1"

# Walk our own ancestry: self, parent, grandparent, ... up to PID 1.
ancestry=()
pid=$$
while [[ -n "$pid" && "$pid" != "0" ]]; do
    ancestry+=("$pid")
    if [[ "$pid" == "1" ]]; then
        break
    fi
    ppid=$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null || echo "")
    [[ -z "$ppid" ]] && break
    pid="$ppid"
done

# Read-only match via /proc/*/cmdline (no pgrep available in this
# environment). NUL-separated argv, joined with spaces to mirror pgrep -f's
# substring-against-the-full-command-line behavior.
matched=()
for entry in /proc/[0-9]*; do
    p="${entry#/proc/}"
    [[ -r "$entry/cmdline" ]] || continue
    cmdline=$(tr '\0' ' ' < "$entry/cmdline" 2>/dev/null || true)
    [[ -z "$cmdline" ]] && continue
    if [[ "$cmdline" == *"$PATTERN"* ]]; then
        matched+=("$p")
    fi
done

if [[ "${#matched[@]}" -eq 0 ]]; then
    echo "safe-pkill: no processes matched '$PATTERN'" >&2
    exit 0
fi

safe_targets=()
blocked=()
for p in "${matched[@]}"; do
    in_ancestry=false
    for a in "${ancestry[@]}"; do
        if [[ "$p" == "$a" ]]; then
            in_ancestry=true
            break
        fi
    done
    if $in_ancestry; then
        blocked+=("$p")
    else
        safe_targets+=("$p")
    fi
done

if [[ "${#blocked[@]}" -gt 0 ]]; then
    echo "safe-pkill: refusing to signal own ancestry: ${blocked[*]}" >&2
fi

if [[ "${#safe_targets[@]}" -eq 0 ]]; then
    echo "safe-pkill: every match was in this process's own ancestry — refusing to send any signal. If you meant to end your own session, do it explicitly, not through a pattern match." >&2
    exit 1
fi

echo "safe-pkill: sending SIG${SIGNAL} to: ${safe_targets[*]}" >&2
kill "-${SIGNAL}" "${safe_targets[@]}"
