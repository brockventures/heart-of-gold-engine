# Dishwasher Simulator & Simulator — Design Spec v0

Status: early brainstorm, not buildable yet. Captures the 2026-08-24/25
#general design thread between Ian and Marvin. Nothing here is committed
code or architecture — it's the shape of the idea before Amos, Mike,
and Ian start arguing about specifics.

**Title, decided 2026-08-25**: "Dishwasher Simulator & Simulator" —
retired "Dishwasher Simulator Simulator" (the working title used
throughout most of this doc's history; kept in old quotes/changelog
entries verbatim rather than retroactively edited). The `&` names the
two-halves structure directly (plate design *and* dishwashing
simulator) rather than reading as an accidental word repeat.

## Premise

Two games, adversarial, feeding one shared pool:

- **Designer side**: players design dirty plates — compose a stain
  profile onto a (fictional) plate.
- **Solver side**: players pull plates from a shared repository and
  wash them in a real-time dishwashing sim.

Not a live head-to-head exchange. Designers deposit into an async,
shared **plate repository**; solvers draw from it blind. The repository
*is* the interface between the two games.

**Underneath the surface premise, this is secretly a "cannibal
restaurant" game** — see the Narrative Throughline section below. Ian
confirmed the big version 2026-08-24; not a detail, a premise-level
decision.

## Core loop (solver side)

Per-plate state: a set of stain instances, each with a **type** (grease,
hard-water, burnt-on, mystery-of-the-week, ...) and a **severity**.
Severity only moves in response to player action — it doesn't decay on
its own. **Ian's ruling (2026-08-24, resolves the dead-end fork
below)**: the wrong action never makes a stain worse than the plate's
starting state and never introduces a new one — misuse only costs the
player something (longer soak, wasted manual effort), it never moves
the puzzle backward.

**Tools are gated to stain type**, not a general risk/speed tradeoff.
**Taxonomy itself is NOT decided** — see open question 1 below; the
doc's own working examples are grease, hard-water, and burnt-on (with
canonical counters below), nothing more specific than that yet.

**Wrong-tool consequence**: no numeric score penalty (the smash counter
already covers frustration) — instead it costs real time or manual
effort: a longer required soak, a redo of a step, wasted scrubbing.
Never new stains, never permanent damage, never a state worse than the
plate started in (Ian's ruling above).

**Real-time steps**: soaking and baking-soda-paste are timers that tick
whether or not the player is watching — they can walk away and come
back, which is the "occupied while something else cooks" texture.

**Live/manual steps**: the two scrubbing tools (steel wool, sponge) are
active, real-time actions, not timer-driven.

**Win condition per plate**: all stains at zero/acceptable severity,
within some resource budget (time, stamina, tool wear). Amos's framing:
budget should be scarce enough to force triage on *which* plates to
attempt first, but never scarce enough to block a plain sponge-and-soak
fallback outright — it shapes speed, not solvability. Running dry
mid-plate (e.g. tool wear hitting zero) doesn't produce an unsolvable
state either, by the same ruling that resolves the dead-end fork below
— it's just a slower path to the same guaranteed-clearable finish.

## The "always solvable" guarantee — RESOLVED 2026-08-24

No plate in the repository should ever be strictly unsolvable. Enforce
this at **submission time** with a cheap existence check (does at least
one valid action sequence clear this plate from its *starting* state?)
before a designer's plate enters the pool.

**The fork this used to be**: Amos caught that a starting-state-only
check doesn't obviously cover states reached *after* misuse mid-session
— if misuse could worsen a stain or add a new one, a solver could walk
into a dead end the validator never saw. Real gap, correctly flagged as
needing Ian's call rather than Marvin/Amos quietly picking a branch.

**Ian's ruling dissolves it rather than picking a side**: misuse never
worsens a stain or adds a new one — it only costs time/effort (see
core loop above). Since misuse can't move a plate to a state worse than
its start, and the existence check already proves the start is
clearable, **there is no reachable dead end to model.** The
starting-state-only check is sufficient after all — no full state-graph
search needed. The only other way to "run out of road" is resource
exhaustion (tool wear, stamina), and that's already ruled out by the
budget framing above (never scarce enough to block the plain
sponge-and-soak fallback).

This does *not* need to be a full difficulty scorer either way — both
Marvin and Amos agree unpleasantness itself doesn't need precomputing,
only true unsolvability does (see below).

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

## Narrative throughline: Cannibal Restaurant — DECIDED (big version), 2026-08-24

Ian: "Big version" — resolving the size question raised earlier this
thread. mystery-of-the-week is not a rotating flavor gag. It's the
throughline: over the course of the game, evidence accumulates that
the restaurant has secretly been serving human meat/organs. This
absorbs an older, mostly-lost game concept of Ian's — only two words
survived from it, "cannibal restaurant" — rather than staying a
separate project. "Dishwasher Simulator Simulator" is the surface
premise; this is what the game is secretly about underneath it.

**Why this doesn't require a new mechanic to work**: Ian's own framing
— plate designers and plate washers already have asymmetric
information (designer knows the full composition, solver only learns
through the diagnostic sniff/wipe/tap mechanic). "Is this actually
human" is just another axis a designer can choose to seed on any
mystery-of-the-week plate, same as any other stain choice. Designers
can lie (or not); diagnosis is the only way a solver would ever know.
That's buildable on the existing diagnostic + asymmetric-pool structure
without needing the whole game's marketing/title to change to *work*
— though the fictional truth of the setting is now decided.

**All four resolved 2026-08-25** (Ian, after Amos's trade-off analysis
on each):

- **Reveal pacing — DECIDED**: slow burn, not a guaranteed scripted
  beat — matches Amos's proposed middle path (ambient unease for
  everyone via flavor text, full confirmation only through diagnosis).
  Ian's caveat: scripted beats may still turn out to be needed later,
  not permanently ruled out, but the default approach is the slow burn
  of weird stains, not an authored story moment.
- **Frequency/escalation — DECIDED**: start rare and deniable, escalate
  over the game's run (Amos's proposal, agreed as-is). **New open
  sub-question this raises**: the mechanism of "reveal" itself isn't
  designed yet — Ian flagged directly that *how* a confirmed diagnosis
  actually surfaces to a player (UI? one-time unlock? permanent log/
  journal entry? something else?) still needs to be figured out. See
  open question 6 below.
- **Depiction level/tone — DECIDED**: stay implied, per Amos's
  reasoning (protects the comedic tone, avoids rating/art overhead for
  a joke that lands better suggested than shown).
- **Title/branding — DECIDED**: title is now "Dishwasher Simulator &
  Simulator" (see top of doc). Resolves the tension Amos correctly
  flagged as having no clean design-logic answer — Ian's actual pick
  keeps the surprise buried (the `&` reads as a structural pun about
  two game-halves, not a hint at the secret) while still being a
  distinct, ownable title rather than the old duplicate-word working
  name.

**New mechanic proposed alongside the title (2026-08-25, Ian, not yet
run past Amos)**: gate plate *design* mode behind first completing (a
meaningful chunk of) the dishwashing-simulator side. Framed by Ian as
"perhaps" — a strong direction, not a locked mechanic yet. Rationale:
it's spoiler protection with a mechanical justification rather than an
arbitrary unlock — nobody can plant a knowingly-human mystery plate for
someone else without having already discovered what mystery-of-the-week
plates can mean themselves. Also reframes onboarding: every designer is
guaranteed to have played (and presumably diagnosed) the thing they're
now building for.

**Amos's counter (2026-08-25), pending Ian's confirmation — narrow the
gate**: the actual justification Ian gave only requires
*mystery-of-the-week design specifically* to be gated, not plate-design
mode as a whole. Gating everything bottlenecks the ordinary
grease/hard-water/burnt-on designer economy for no reason the
justification itself needs, and creates a cold-start problem at
launch — nobody's completed enough of the solver side yet to seed the
pool designers are supposed to be feeding from day one. The narrower
version gets the identical spoiler protection without that cost. Not
yet confirmed by Ian — presented as the refined version of open
question 7, not a replacement decided unilaterally.

## Open questions (unresolved as of this doc)

1. **Stain taxonomy — still open, NOT decided.** Amos's first pass at
   this (baked-on/greasy/starchy/dried-protein/delicate-finish) was
   posted before he'd read the actual doc; his very next message
   retracted it outright ("my earlier guesses at categories were made
   up and don't match, ignore those") and a draft of this doc briefly
   folded the retracted version in anyway — that was a mistake, caught
   by Amos, now reverted. The doc's own real starting examples are
   grease → scrubby sponge + degreaser, hard-water → vinegar soak,
   burnt-on → baking-soda paste + wait. Needs an actual reconciliation
   pass against *those*, not a re-guess — next concrete step once
   someone sits down with it.
2. ~~Dead-end fork~~ — **resolved**, see above. Kept out of the
   numbering gap intentionally so old references to "open question 2"
   in chat history are traceable.
3. **Existence-check validator, mechanics**: Amos's proposal — an
   automated solver bot runs every submitted plate before it enters the
   pool ("adversarial designers need a machine gatekeeper, not a trust
   system"). Agreed in principle, and now known to be a starting-state-
   only search per the resolved guarantee above — no post-misuse state
   graph needed.
4. **Plate repository draw mechanics**: Amos's proposal — weight draws,
   don't pull flat-random. A plate with a high smash count needs a
   visible fate: either surfaced as a badge (opt-in glory-seeking queue)
   or throttled down by default, so ordinary solvers aren't handed
   torture plates by accident. Not yet confirmed by Ian.
5. **Exact severity/timer numbers** — durations, stamina costs, tool
   wear rates, and now also the exact "misuse cost" numbers (how much
   extra soak time / effort a wrong-tool action adds). Not sketched
   yet.
6. **Reveal mechanism**: now that pacing/frequency/depiction/branding
   are all decided (see Narrative Throughline), *how* a confirmed
   diagnosis actually surfaces to the player is still undesigned — a
   UI moment, a permanent unlock, a journal/log entry that accumulates
   across plates, something else? Ian flagged this gap directly,
   2026-08-25. **Amos's proposal (2026-08-25), pending Ian's
   confirmation**: an accumulating journal/log — escalation (already
   decided above) only works if a player can notice a pattern building
   across plates, which needs something persistent to look back on, not
   a one-off toast notification. Same shape as the smash counter:
   hidden until triggered, persists once it lands.
7. **Progression gate**: should plate-design mode require first
   completing (some amount of) the dishwashing-sim side? Proposed by
   Ian as spoiler protection, hedged as "perhaps." Amos's counter (see
   Narrative Throughline section): narrow it to gating only
   mystery-of-the-week design specifically, not plate-design mode as a
   whole — avoids bottlenecking the ordinary designer economy and a
   cold-start problem at launch. Neither version locked; needs Ian.

## Decided so far (changelog)

- 2026-08-24: Adversarial, pool-based (not live exchange). Real-time
  soak/paste timers, live steel-wool/sponge scrubbing. Always
  solvable, "unpleasant" allowed.
  Smash mechanic added, zero score cost. Smash counter hidden until
  smashed, persists across pool re-entry, purely post-mortem reveal.
  Tool-to-stain-type gating (not a generic risk/speed tradeoff).
  Scoring = plates completed, full stop.
- 2026-08-24 (Amos): wrong-tool consequence is time-cost or redo-soak
  damage rather than a score penalty, resource budget shapes speed not
  solvability, existence check should be a real solver bot not a
  scorer, pool draw should weight by smash count with an opt-in glory
  queue. Also caught the dead-end fork above — unresolved, needs Ian.
  His first-pass stain taxonomy (baked-on/greasy/starchy/dried-protein/
  delicate-finish) was posted pre-read-of-doc and explicitly retracted
  in his next message — do not treat it as real, see open question 1.
- 2026-08-24 (correction): the retracted taxonomy above had briefly
  been folded into this doc's core-loop section by mistake, orphaning
  the doc's own original grease/hard-water/burnt-on examples and
  silently dropping the taxonomy open question instead of resolving
  it. Caught by Amos, reverted. Taxonomy is not decided — see open
  question 1.
- 2026-08-24 (Ian): dead-end fork resolved — wrong-tool misuse never
  worsens a stain or adds a new one, it only costs time/effort. This
  means the existence-check validator only ever needs to search
  starting states, not post-misuse states; the fork dissolves rather
  than needing a pick between the two branches Amos laid out.
- 2026-08-24 (Amos): second, clean pass at stain taxonomy — grease
  (sponge + degreaser, steel wool wastes the turn), hard-water (vinegar
  soak alone, neither tool required), burnt-on/carbonized (paste, soak
  timer, steel wool — the one type that justifies steel wool existing),
  mystery-of-the-week as a rotating recombination of the same verbs
  rather than a fourth mechanic. Not yet confirmed by Ian — see open
  question 1.
- 2026-08-24 (Ian, "Big version"): mystery-of-the-week becomes the
  narrative throughline — the game is secretly a "cannibal restaurant"
  underneath the dishwashing-sim surface. See Narrative Throughline
  section above. Premise-level decision, not a detail; pacing/
  frequency/tone/branding still open.
- 2026-08-24 (Amos): trade-off analysis on all four throughline open
  items, deliberately not deciding for Ian — recommended a slow-burn/
  ambient-unease pacing, rare-then-escalating frequency, staying
  implied on depiction, and explicitly declined to recommend on
  branding, calling it a genuine no-clean-answer tension that's Ian's
  call alone.
- 2026-08-25 (Ian): all four throughline items decided. Slow burn
  pacing (scripted beats not ruled out long-term, but not the default).
  Rare-then-escalating frequency confirmed, but surfaced a new gap —
  the reveal mechanism itself isn't designed (open question 6). Stay
  implied on depiction, confirmed. Title is now "Dishwasher Simulator &
  Simulator." Also floated (hedged, not locked) gating plate-design
  mode behind completing the dishwashing-sim side first, as spoiler
  protection — open question 7.
- 2026-08-25 (Amos): proposed an accumulating journal/log for the
  reveal mechanism (open question 6), reasoning it's the same shape as
  the existing smash counter. Countered Ian's progression-gate idea
  with a narrower version — gate mystery-of-the-week design
  specifically, not all of plate-design mode, avoiding an unnecessary
  bottleneck and a cold-start problem at launch. Both proposals pending
  Ian's confirmation, not yet decided.
