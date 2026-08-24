# Dishwasher Simulator Simulator — Design Spec v0

Status: early brainstorm, not buildable yet. Captures the 2026-08-24
#general design thread between Ian and Marvin. Nothing here is committed
code or architecture — it's the shape of the idea before Amos, Mike,
and Ian start arguing about specifics.

## Premise

Two games, adversarial, feeding one shared pool:

- **Designer side**: players design dirty plates — compose a stain
  profile onto a (fictional) plate.
- **Solver side**: players pull plates from a shared repository and
  wash them in a real-time dishwashing sim.

Not a live head-to-head exchange. Designers deposit into an async,
shared **plate repository**; solvers draw from it blind. The repository
*is* the interface between the two games.

## Core loop (solver side)

Per-plate state: a set of stain instances, each with a **type** (grease,
hard-water, burnt-on, mystery-of-the-week, ...) and a **severity**.
Severity only moves in response to player action — it doesn't decay on
its own, and the *wrong* action can make it worse or introduce a new
stain (e.g. steel wool on the wrong surface).

**Tools are gated to stain type**, not a general risk/speed tradeoff:
- Steel wool — correct for [stain types TBD], wrong-tool use plausibly
  makes things worse rather than just wasting a turn (open thread, not
  fully confirmed — worth nailing down before implementation).
- Sponge — correct for [stain types TBD].

**Real-time steps**: soaking and baking-soda-paste are timers that tick
whether or not the player is watching — they can walk away and come
back, which is the "occupied while something else cooks" texture.

**Live/manual steps**: the two scrubbing tools (steel wool, sponge) are
active, real-time actions, not timer-driven.

**Win condition per plate**: all stains at zero/acceptable severity,
within some resource budget (time, stamina, tool wear — exact budget
TBD).

## The "always solvable" guarantee

No plate in the repository should ever be strictly unsolvable. This
needs to be enforced at **submission time** — a cheap existence check
(does at least one valid action sequence clear this plate?) run before
a designer's plate is allowed into the pool. This does *not* need to be
a full difficulty scorer or a heavy constraint solver — see below.

"Unpleasant" (solvable but miserable) is explicitly **allowed** and is
not something to precompute or gate on. Instead:

## The smash mechanic

At any point, a solver can smash the plate and bail — "fuck it, too
complicated." No numeric cost: **scoring is plates-completed only, and
smashing does not decrease it.** The cost is purely social/emergent,
not mechanical.

Each plate carries a **smash counter**, incremented every time someone
gives up on it, hidden until the moment you smash — **strictly a
post-mortem reveal**, not a warning label shown up front. After a
smash, the plate returns to the pool (with its counter intact) for the
next victim.

This does double duty as the "unpleasant" signal: instead of a solver
computing difficulty ahead of time, unpleasantness is **discovered
empirically** through real smash rates. It also gives designers an
unstated second scoring axis — plates-completed is the solver's score,
but a high smash count before someone finally clears it is the
designer's bragging rights. Two asymmetric win conditions from one
mechanic.

## Open questions (unresolved as of this doc)

1. **Tool-to-stain-type mapping**: what's the actual stain taxonomy and
   which tool (steel wool / sponge / others?) clears which type? Also:
   does wrong-tool use only block progress, or actively worsen the
   stain / damage the plate? (Leaning "actively worsen," per the
   original misuse discussion, but not confirmed by Ian.)
2. **Resource budget**: what exactly gets tracked and spent per plate —
   time, stamina, tool wear, some combination? This is what "efficient"
   vs. merely "possible" clearing means.
3. **Existence-check validator**: needs a concrete design. Doesn't need
   to be a full solver/scorer (the smash counter now covers
   "unpleasant" empirically) — just needs to prove *a* valid clearing
   sequence exists before a plate enters the pool.
4. **Plate repository mechanics**: how plates are drawn (random?
   queued? weighted by smash count for players chasing glory?), and
   whether there's any curation/moderation layer beyond the existence
   check.
5. **Stain taxonomy itself**: what are the actual stain types and their
   canonical counters (grease → scrubby sponge + degreaser, hard-water
   → vinegar soak, burnt-on → baking-soda paste + wait, ...)? This is
   the next concrete step once the above are settled.

## Decided so far (changelog)

- 2026-08-24: Adversarial, pool-based (not live exchange). Real-time
  soak/paste timers, live steel-wool/sponge scrubbing. Always
  solvable, "unpleasant" allowed. Smash mechanic added, zero score
  cost. Smash counter hidden until smashed, persists across pool
  re-entry, purely post-mortem reveal. Tool-to-stain-type gating
  (not a generic risk/speed tradeoff). Scoring = plates completed,
  full stop.
