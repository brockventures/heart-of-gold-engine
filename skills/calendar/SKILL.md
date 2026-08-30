# Calendar Skill

Read-only access to the owner's personal and work calendars via their ICS
feed URLs. No write path exists on either feed — both are view-only exports.

## What It Does

Provides `get_calendar_events`: lists events on a given date (or a run of
consecutive days from that date) from the personal calendar, the work
calendar, or both. Recurring events are expanded properly via
`recurring_ical_events`, not just shown on their original start date.

## Data Sources

- **Personal**: Google Calendar's private "secret address in iCal format"
  for `user@example.com`.
- **Work**: an Exchange/Outlook sharing ICS URL, recovered 2026-08-28 from
  a TNEF-encoded calendar-share invite forwarded into the Marvin Gmail
  folder (the sharing link was buried in an `application/ms-tnef`
  attachment's `sharing.xml`, not in the visible email body).

Both URLs live in `config/.env` (`PERSONAL_CALENDAR_ICS_URL`,
`WORK_CALENDAR_ICS_URL`) — not in git, treated as credentials even though
they're unauthenticated, since possession of the URL is possession of
read access.

## Usage

```json
{"calendar": "both", "date": "2026-08-28", "days": 1}
```

All fields optional — `calendar` defaults to `"both"`, `date` defaults to
today (resolved in `America/Los_Angeles`, not the process's own
timezone), `days` defaults to `1`.

Returns:

```json
{
  "range_start": "2026-08-28T00:00:00-07:00",
  "range_end": "2026-08-29T00:00:00-07:00",
  "events": [
    {
      "calendar": "personal",
      "summary": "Dentist",
      "start": "2026-08-28T08:50:00-07:00",
      "end": "2026-08-28T09:50:00-07:00",
      "location": "Example Dental Group, 123 Main St Suite 100, Anytown, CA 00000"
    }
  ]
}
```

If one calendar's feed fails to fetch or parse, its error is reported
under an `errors` key rather than failing the whole call — the other
calendar's events still come back.

## Files

```
calendar/
├── SKILL.md
├── tools.json
└── scripts/
    └── get_calendar_events.py
```

## Dependencies

`icalendar` and `recurring-ical-events` (pinned in `requirements.txt`).
