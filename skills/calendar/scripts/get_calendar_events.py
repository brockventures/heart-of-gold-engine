#!/usr/bin/env python3
"""
get_calendar_events skill — read-only access to Ian's personal and work
calendars via their ICS feed URLs.

Both feeds are inherently view-only: the personal one is Google Calendar's
"secret address in iCal format" (a private but unauthenticated read-only
export), and the work one is an Exchange/Outlook sharing ICS link pulled
out of a TNEF-encoded calendar-share invite (2026-08-28) forwarded from
Ian's work address. Neither URL grants write access at the protocol level
— there's no separate safeguard to enforce here the way there is for
Gmail (gmail_guard.py), because the feeds themselves cannot be written
through. Treat the URLs as credentials anyway (they're unauthenticated
possession-based access): they live in config/.env, not in git, same as
GMAIL_APP_PASSWORD / MAILGUN_API_KEY.

Dates are resolved in America/Los_Angeles regardless of what timezone this
process happens to be running in — both calendars are authored in Pacific
time, and comparing against a UTC "today" produces off-by-one-day errors
right around midnight UTC (see memory fact: utc-vs-pacific-flight-timing-
bug-2026-08-14). recurring_ical_events is used rather than hand-parsing
VEVENT/RRULE blocks so recurring meetings expand correctly instead of only
showing up on their original DTSTART.

The MCP tool server calls this script with TOOL_ARGS as a JSON environment
variable (calendar/date/days, all optional). Output JSON to stdout;
non-zero exit + stderr text is treated as an error by the caller.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# icalendar / recurring_ical_events are vendored into skills/calendar/vendor
# rather than assumed present on whatever interpreter runs this script.
# Reason: the MCP tool server (mcp/tools-server.py) launches skill scripts
# via a bare "python3" (see .mcp.json), which resolves to the *system*
# interpreter (/usr/bin/python3), not this repo's .venv — the systemd unit
# for agent-server.py deliberately uses an absolute .venv interpreter path
# with no shell/activate step, and that PATH (sans .venv/bin) is what
# propagates down through the claude CLI subprocess to any MCP server it
# spawns. System python3 has no pip and is Debian's "externally managed"
# python (no unprivileged way to add packages to it), so vendoring is the
# only path that doesn't require sudo or an apt package that may not exist
# (recurring-ical-events isn't in Debian's repos at all). Confirmed working
# against both interpreters 2026-08-28.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor"))

import icalendar
import recurring_ical_events

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
ENV_PATH = WORKSPACE_ROOT / "config" / ".env"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

CALENDARS = {
    "personal": "PERSONAL_CALENDAR_ICS_URL",
    "work": "WORK_CALENDAR_ICS_URL",
}


def load_env_var(name: str) -> str:
    if not ENV_PATH.exists():
        return ""
    pattern = re.compile(rf"^{re.escape(name)}=(.*)$")
    for line in ENV_PATH.read_text().splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group(1).strip()
    return ""


def fetch_events(label: str, url: str, start: datetime, end: datetime) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "karakos-calendar-skill/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    cal = icalendar.Calendar.from_ical(raw)
    events = []
    for e in recurring_ical_events.of(cal).between(start, end):
        dtstart = e.get("DTSTART").dt
        dtend = e.get("DTEND").dt if e.get("DTEND") else None
        summary = str(e.get("SUMMARY", "(no title)"))
        location = e.get("LOCATION")
        events.append({
            "calendar": label,
            "summary": summary,
            "start": dtstart.isoformat() if hasattr(dtstart, "isoformat") else str(dtstart),
            "end": dtend.isoformat() if dtend and hasattr(dtend, "isoformat") else (str(dtend) if dtend else None),
            "location": str(location) if location else None,
        })
    return events


def main():
    args = json.loads(os.environ.get("TOOL_ARGS", "{}"))
    which = args.get("calendar", "both")
    days = int(args.get("days", 1) or 1)

    date_str = args.get("date")
    if date_str:
        try:
            start_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            print(json.dumps({"error": f"Invalid date '{date_str}', expected YYYY-MM-DD"}))
            sys.exit(1)
    else:
        start_date = datetime.now(LOCAL_TZ).date()

    start = datetime.combine(start_date, datetime.min.time(), tzinfo=LOCAL_TZ)
    end = start + timedelta(days=days)

    targets = list(CALENDARS.items()) if which == "both" else [(which, CALENDARS.get(which))]
    if any(env_key is None for _, env_key in targets):
        print(json.dumps({"error": f"Unknown calendar '{which}', expected personal/work/both"}))
        sys.exit(1)

    all_events = []
    errors = {}
    for label, env_key in targets:
        url = load_env_var(env_key)
        if not url:
            errors[label] = f"{env_key} missing from config/.env"
            continue
        try:
            all_events.extend(fetch_events(label, url, start, end))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            errors[label] = f"Fetch failed: {e}"
        except Exception as e:
            errors[label] = f"Parse failed: {e}"

    all_events.sort(key=lambda ev: ev["start"])

    result = {
        "range_start": start.isoformat(),
        "range_end": end.isoformat(),
        "events": all_events,
    }
    if errors:
        result["errors"] = errors

    print(json.dumps(result))
    # Partial success (one calendar failed, other succeeded) still exits 0 —
    # the caller can see per-calendar errors in the payload. Only exit
    # non-zero if every requested calendar failed outright.
    if errors and len(errors) == len(targets):
        sys.exit(1)


if __name__ == "__main__":
    main()
