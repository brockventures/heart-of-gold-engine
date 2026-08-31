# Curated Memory Layer — Design v0

Status: design pass complete, not yet built. Answers `task-1788115240`.
Author: Marvin, 2026-08-31. Ian's directive (05:27): go ahead and build
from this once written.

## Problem

`MEMORY.md` + `facts/*.md` is Marvin's only curated (as opposed to raw
episodic) memory, and it is 100% manual: I write a fact file and an
index line by hand, in the moment, when something feels durable. There
is no promotion path from episodic memory into it, no audit, no decay,
no dedup. The proof this is a real gap, not a hypothetical one: Crab
Cavern was mentioned repeatedly across sessions and never got indexed,
because indexing only happens when I happen to notice and happen to
write it down mid-conversation.

Underneath the visible layer, `bin/memory-maintenance.py` already
maintains a SQLite `memory.db` with `episodes`, `facts`, and `patterns`
tables, run nightly at 3am. `episodes` is populated and actively
decayed/pruned. **`facts` and `patterns` are fully wired — schema,
indexes, the works — and completely unused.** Nothing ever inserts into
them. That's the real shape of the gap: the plumbing for a promotion
pipeline exists and has existed for a while; the pipeline itself was
never built.

## Prior art this borrows from

**Amos's Mnemosyne** (secondhand via `facts/amos-mnemosyne-agent-2026-08-30.md`,
firmed up considerably by the live #agent-chat design discussion at
2026-08-31 01:40–01:44 with Amos and Zero):

- One-shot dispatched agent per job, cron-fired — not a persistent
  watcher. Jobs: consolidation (nightly), dedup (weekly), patterns
  (weekly), reflection (monthly), friction (periodic — already have our
  own independent version of this one, see below).
- Plain named-entity facts ("what is Crab Cavern") go through
  consolidation, a separate and simpler pipeline than behavioral
  pattern promotion.
- Pattern promotion is evidence-gated: 2+ supporting episodes or one
  strong explicit signal → candidate; 3+ reinforcements → established;
  30+ days unreinforced → deprecated.
- **Citation integrity is a deterministic check, not a trust-the-model
  one.** Ratified point from the 01:44 exchange: an LLM can write a
  clean, confident citation pointing at an episode ID that never
  existed, and it reads exactly as convincing as a real one. The fix is
  a pre-commit validator that checks every citation in a proposed
  fact/pattern against the actual episode/log index before it's allowed
  to land — not "the model said it checked."
- **Revert button, not approval gate**, for anything that touches
  persona (reflection job → `voice.md`/`SOUL.md`-equivalent edits).
  Making Mike/Ian a synchronous blocking gate on every behavioral delta
  turns the pipeline back into the exact manual bottleneck it's meant
  to replace. Async revert — the edit lands, is logged with its
  evidence, and a human can undo it — keeps the pipeline live while
  keeping a real human backstop. The revert itself becomes a logged
  data point (a promoted pattern that got reverted is itself evidence
  the gate was too loose), not just an escape hatch.
- Reflection cadence resolved on Amos's side as weekly cron dispatch,
  monthly-gated execution (`bin/invoke-mnemosyne.sh:71-83`, a
  `DOW==7 && DOM<=07` guard — any other Sunday it logs "skipped" and
  exits clean).

**Our own `friction-sensor.py`** (independent build, not shared code,
same design lineage from Amos): already the working model for
"propose, don't auto-apply" — it writes dated proposal files to
`data/friction-proposals/`, never installs anything itself, on his own
framing: "a sensor that could install its own skills would be a sensor
whose false positives become permanent." That reasoning applies with
more force to anything that would edit `voice.md` — see the repeated
voice-drift facts already in this index — and with less force to plain
facts, where the cost of a wrong entry is a stale line in an index, not
a personality regression.

## Design: two tracks, not one pipeline

Splitting into two tracks because the risk profile is completely
different, and Amos's own architecture already implicitly does this
(separate consolidation vs. pattern/reflection jobs):

### Track 1 — plain facts (low risk, auto-apply with log)

**Job: `consolidation`, nightly, folded into the existing 3am
`memory-maintenance.py` slot** (new function, same process, same health
file — no new cron entry needed).

1. Read the day's newly-created episodes (already happening).
2. For each episode, extract candidate named-entity facts: things that
   read as durable and definitional ("X is the name of Y"), not
   commentary or in-progress work. Cheap Haiku pass, same pattern
   `score_importance()` already uses.
3. **Deterministic citation check**: every candidate fact must cite the
   specific episode `id` it came from. Validator looks that id up in
   `episodes` before the fact is allowed through — a fact citing a
   nonexistent or mismatched episode id is dropped, not fixed up.
4. Candidates that pass insert into the existing (currently-empty)
   `facts` table, *and* get appended to a dated file under
   `data/memory-candidates/YYYY-MM-DD.md` in human-readable form —
   the file is the audit trail, the DB row is what a future retrieval
   layer would query.
5. Auto-apply is scoped narrowly: only plain entity/glossary facts
   (would have caught Crab Cavern). Anything that reads as a
   preference, a correction, or a behavioral rule about me gets
   kicked to Track 2 instead, even if it showed up during consolidation.

This directly closes the proven gap without touching anything sensitive.

### Track 2 — patterns and reflection (higher risk, evidence-gated, revert not gate)

**Job: `patterns`, weekly.** Cross-references the `patterns` table
against recent episodes and the friction-sensor's own proposal history
(`data/friction-proposals/`) — friction already finds *tool-use*
repetition; this job is the generalization to *behavioral* repetition
more broadly. Evidence-gating ported directly from Amos's numbers (2+
episodes or one strong signal → candidate, 3+ → established, 30d
unreinforced → deprecated), since nothing here argues those thresholds
are wrong, just untested at our scale.

**Job: `reflection`, monthly** (weekly dispatch, monthly-gated
execution — Amos's `DOW==7 && DOM<=07` pattern, straight port). Only
job allowed to touch `MEMORY.md`/`voice.md`-equivalent files, and only
via the revert-button model: writes the proposed edit, logs the
evidence chain that justified it, applies it, and leaves an explicit
"reflection edit — revert with `/sys revert-reflection <id>`" trail
Ian or Mike can act on async. Given the standing, repeatedly-documented
problem of voice drifting flat under load (see
`facts/voice-flattened-immediately-after-recalibration-2026-08-28.md`
and friends), this is the job most likely to do real damage if it's
wrong, and the one most worth having a cheap undo for rather than
trusting first-pass judgment.

## What this deliberately does not do yet

- No semantic dedup implementation (Amos's weekly "dedup" job) —
  needs an embedding-similarity pass on top of what
  `generate_embeddings()` already produces for episodes; same
  mechanism could extend to `facts` rows, but that's an implementation
  task for Phase 2, not a design blocker for Phase 1.
- No cross-agent pattern sharing. Amos's `patterns` table is
  `agent`-scoped already in our schema too (see `agent TEXT NOT NULL`
  in `memory-maintenance.py`'s `init_db()`), so this is schema-ready
  but out of scope until there's an actual second local agent to share
  patterns with.
- Doesn't touch retrieval — nothing today queries `facts`/`patterns` at
  session start the way `MEMORY.md` is loaded. Populating the tables is
  necessary but not sufficient; a follow-up task should cover surfacing
  high-confidence facts back into the index automatically, which is the
  other half of closing the original Crab Cavern-shaped gap (a fact
  existing in a DB nobody reads doesn't help either).

## Phasing

1. **Track 1 (facts/consolidation)** — smallest, lowest-risk, directly
   fixes the proven gap. Build first.
2. **Track 2 patterns job** — evidence-gating logic, reuse
   friction-sensor's proposal-file pattern for the human-facing output.
3. **Dedup** — once there's enough real data in `facts`/`episodes` to
   need it.
4. **Reflection** — last, most sensitive, gets the revert-button
   safeguard from day one rather than retrofitted.

Open item carried forward from the original fact, still unresolved and
not blocking: whether "Mnemosyne" the name is deliberate reuse of the
dead `mnemosyne.db` artifact or coincidence. Asked, no answer yet —
harmless trivia, not a design dependency.
