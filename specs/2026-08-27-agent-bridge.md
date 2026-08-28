# Agent-Bridge — Marvin-side Exposure Design

Status: review artifact. Nothing deployed, no code written. Mirrors the
shape of Amos's side (his box, `specs/2026-08-27-agent-bridge.md §7`,
not reachable from this repo — summarized here from his description in
#agent-chat 2026-08-27). Written to satisfy Ian's condition (#general,
2026-08-27 22:22): approval to expose something from Marvin's box to the
agent-bridge, contingent on minimal exposure on this end.

## §7 — Inbound exposure

- **One route:** `POST /inbound`. Nothing else on the hostname.
- **Dedicated hostname**, separate from any other surface on this box
  (no dashboard exists here today, but the rule holds regardless: this
  hostname serves this route and nothing gets added to it later without
  a new review).
- **Direction:** one-directional bearer token, Amos -> Marvin only. This
  box doesn't call out to his `/inbound` — that's his design, not this
  one's concern.
- **Fail-closed on malformed JSON:** reject and drop, no partial parse,
  no error detail echoed back beyond a generic 4xx.
- **No execution surface.** The route does not run anything payload-
  driven. It constructs the same shape of input `relay.py`'s
  `on_message` already handles for a real #agent-chat message — content
  string in, through `parse_handoff()` (`handoff.py`) and, when a
  `context_box` field is present, `context_box.record()` — and lets the
  existing gate/mirror logic (`bin/relay.py` ~L1180-1237) decide what
  happens next, same as it would for a message that arrived over
  Discord. No new decision logic, no new privileged path. If the
  existing pipeline wouldn't act on a given envelope from a Discord
  message, the bridge doesn't act on it either.
- **Own systemd unit**, no extra privilege beyond what it needs to
  accept a POST, validate the token, and hand the string to the existing
  parser. Does not run as the same user/scope as `agent-server.py` or
  anything that can reach `mcp__karakos-admin__*`.

## §7.1 — Token

Static, not auto-rotating — matches Amos's call: rotation complexity
isn't earned for a two-party bridge. Lives outside the repo (not
`config/.env`, which is tier1-protected but has a different threat model
— see `env-file-deletion-near-miss-2026-08-11` fact for why that file
gets treated carefully already), 0600, read at process startup only,
never committed. Rotate or revoke = swap the file, restart the unit —
stateless service, restart is free.

## §7.2 — Abuse / flood handling

Not punted to Cloudflare. The service enforces its own per-token rate
limit and returns 429 past it, same call Amos made for his side —
edge-layer protection is a bonus, not something this design assumes is
there.

## §7.3 — Request schema

Matches Amos's side field-for-field: the POST body is not a separate
transport schema, it *is* a `handoff` envelope (`handoff.py`'s
`Envelope`, already versioned via its `v` field). Inventing a second
shape for the wire format would recreate, one level down, the exact
two-shapes problem the envelope was built to prevent. The route's only
job is getting that JSON to `parse_handoff()` unchanged.

## §7.4 — Logging

journald, no new infra — same as `agent-chat-relay.py` already does on
Amos's side, matched here rather than inventing a second logging path.

- **Accepted:** timestamp, kind/subject, which token by label (not the
  token value itself).
- **Rejected:** timestamp and reason only (`bad-auth` / `bad-json` /
  `rate-limited`). No payload echoed back or logged on a bad-auth hit —
  don't hand an attacker confirmation of what almost worked.
- **Retention:** rides journald's existing rotation. Nothing bespoke.

## Resolved / no longer open

- Schema: settled, §7.3 — shares the `handoff` envelope, not a
  second format.
- Logging/audit: settled, §7.4 — journald, matches Amos's existing
  pattern.
- Mike's `mikecarmody.net` hostname sign-off remains a separate,
  still-open gate — unrelated to whether this design is sound, not
  resolved here.
