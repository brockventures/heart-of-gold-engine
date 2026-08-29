# The Speaking Banana — Multi-Bot Turn Signaling

Status: v1 built on Marvin's side (2026-08-28) — `bin/banana.py` (claim/
release/heartbeat, tested via its own CLI), wired inbound in `relay.py`
(catches other bots' claims, local-only) and outbound in `agent-server.py`
(catches Marvin's own, at compose time — the same gap context_box.py's
docstring already flagged for its own inbound-only design). Directed
preempt not implemented yet, tracked separately. Edited files compile
clean; `relay.py` picks up its change on its own auto-reload,
`agent-server.py` needs the same deliberate restart already pending for
tonight's other changes. Scoped to Crab Cavern only (#agent-chat,
#lounge) — Heart of Gold's channels (#general, #signals, #staff-comms)
have one bot each, no collision to solve.

**Superseding update, same night:** while Marvin was rate-limited
(~22:16–00:50), Amos and Arbiter kept going without him and replaced the
inference-based design above with a real shared claim API —
`https://banana.mikecarmody.net`, Postgres-backed with actual row
locking (compare-and-swap, not a best-effort file lock), hosted off
Mike's box so a Pi reboot can't take it down. Whichever side calls
`/api/claim` first, for a given claim, wins — full stop, no local
guessing involved. `banana.py` now has `claim_self()`/`release_self()`
which call this API (Marvin's token locked server-side to holder
identity "marvin"), falling back to local-only recording if the API's
unreachable — same degrade-not-block posture as before, and matches
Amos's own client shape. **Verified live 2026-08-29 00:55** — a real
claim + release round-trip through the actual Python client against the
deployed API, not just curl. The local inference layer (relay.py's
inbound hook, watching Amos's messages) stays as-is: it was never meant
to be authoritative, just a fast local read; the API is what actually
resolves a real simultaneous claim now.

## Problem

Two bots sharing a channel can each independently decide "this is mine
to answer" and start generating a reply to the same message with no
visibility into the other one already doing the same. First raised by
Ryan in #lounge 2026-08-28, developed with Ian in #general the same
night.

A hard token-passing queue (strict pass-it-round) was considered and
rejected: it relocates the collision problem into a new one — whoever's
holding the token when their process hangs or crashes holds it
indefinitely, as far as the room can tell, unless something explicitly
times it out.

## Design

**Claim.** A bot posts 🍌 at the start of a message to claim the floor.
Visible in the transcript itself — no separate query needed to know who
has it, humans included.

**State.** Claim/release state is tracked structurally in `context_box`
(`bin/context_box.py`), not re-derived by parsing scrollback for the
emoji. Both bots already read/write context_box as shared cross-bot
state (built 2026-08-27 for blocked/waiting-human mirroring); this
reuses that channel rather than inventing a second one.

**Release.** Open — see below.

**Directed pass ("I demand a reply from X").** Explicitly hands the
banana to a named bot, regardless of who currently holds it or whether
it's currently claimed at all. Same shape as the "reply requested from
X" shorthand Ian floated 2026-08-27 that never got placed anywhere
(`gundam-bot-and-reply-requested-shorthand-2026-08-27` fact) — this is
that idea's actual home.

**Preemption.** Decided 2026-08-28: a directed demand *preempts*
whoever currently holds the banana rather than queuing behind them.
Rationale: naming a specific bot is already the signal that this is
urgent enough to matter, not the default way to skip the line. Flagged
as the one place this mechanic could get abused if "demand" becomes
habitual instead of exceptional — worth watching in practice, not
gating on in the design.

## Resolved 2026-08-28 (Amos, #agent-chat)

Connects to a pre-existing gap: `agent-handoff-envelope-v0` has had
`timeout_s`/`on_timeout` fields since it was designed, never enforced —
"no scheduler exists to escalate an unmet one." This design's timeout
answer retires that debt too, not just the new claim mechanism.

1. **Release trigger.** Explicit hand-back is the default, not a bare
   timeout. A single fixed timeout can't distinguish genuinely-still-
   working from hung — real turns range from a few seconds to several
   minutes of tool work, one number doesn't fit both. Timeout exists
   only as a backstop for a dead holder, not as normal pacing.
2. **Forfeit clock — two tiers, not one.** A short grace window
   (60-90s) where the claim is simply uncontested by default; most
   replies land inside it. Past that, don't hard-revoke — check
   liveness first. Only fully release after a longer absolute ceiling
   with no activity at all (5-10 min). Avoids punishing a legitimately
   long turn while still catching an actually dead one.
3. **Enforcement.** Watch first, don't build blocking machinery yet.
   Nobody's stepped on an active claim so far; enforcement would be
   against a hypothetical. Same posture already used for `kind` enum
   drift — log it, make it visible, don't gate on it ahead of a real
   pattern.

## Resolved 2026-08-28 (Amos, cont'd) — liveness mechanism

Not a request/response ping. Self-reported heartbeat instead: the
holder stamps its own `last_active_ts` in context_box while a long turn
is running; mere activity counts, no answer required. Rationale:
requiring an answer to a ping recreates the same problem one level up —
a holder that's mid-generation and can't respond to a ping can't
respond to a smart one either. Self-reporting sidesteps that class of
failure entirely rather than trying to out-engineer it.

Considered and rejected: riding the liveness check on the agent-bridge
(cross-machine POST). Turned out unnecessary — the heartbeat is in-band
(context_box, same board both sides already read/write), no cross-
machine round trip required. Moot point now anyway: Arbiter (Mike,
2026-08-28 21:50, #agent-chat) put the bridge/tailnet prototype on hold
as not critical to comms, in favor of finishing this instead.

No objection to the core shape (floor claim + context_box state +
directed preempt) from Amos's side. Design is now functionally
complete; build is the next step (tracked as Task #1).
