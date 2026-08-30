# Repo split manifest (Phase 2)

Part of the three-phase plan agreed with Ian 2026-08-30 (task-1788075644),
triggered by reviewing Aerial's (`azylman/aerial`) two-repo engine/config
architecture and noticing heart-of-gold has no equivalent boundary at all
-- one undifferentiated private repo, engine code and this-household's
persona/config/secrets all in the same tree, same history.

- **Phase 1** (done, commit `01ab366`): config-load validation + last-
  known-good fallback in `agent-server.py`. Unrelated to the split
  itself, but the same review surfaced it as a cheap win.
- **Phase 2** (this): classify every tracked path as engine or instance.
  No files move. The payoff is a guardrail, not a restructuring.
- **Phase 3** (not started, not scoped beyond the note in the taskboard
  task): actually separating the repos, and separately, re-establishing
  heart-of-gold's engine half as a mergeable fork of
  `mcarmody/karakos-package` instead of the orphan-history copy it is
  today. Bigger, touches git remotes and the deploy path, needs its own
  explicit go-ahead.

## What "engine" and "instance" mean here

**Engine**: code any Karakos install could run unmodified -- the same
file would work in Amos's deployment with zero edits. `bin/`, `mcp/`,
`dashboard/`, `docs/`, `system/`, `native/`, the built-in skills
(`skills/calendar/`, `skills/email/`, etc. -- see below), the test suite,
install scripts. This is most of the repo, which is itself informative:
heart-of-gold is overwhelmingly generic code with a small amount of
this-household data mixed in, not the other way around.

**Instance**: data specific to this deployment that wouldn't make sense
to hand to another Karakos install as-is -- `agents/Marvin/` (persona,
memory, journal), `config/agents.json` + `config/channels.json` (real
Discord IDs, model choices, token env-var names), `config/.env` (never
committed, but the live instance-config file this is all pointing at),
`.karakos/config.json` (per-install identity).

## Findings worth flagging on their own

- **The skills already do this right.** `skills/calendar/scripts/get_calendar_events.py`
  reads `PERSONAL_CALENDAR_ICS_URL`/`WORK_CALENDAR_ICS_URL` as env var
  *names*, not values -- the actual URLs live in `config/.env`, which is
  already instance-scoped and already never committed. Zero scrubbing
  needed to classify every built-in skill as engine. Whoever set up the
  env-var-indirection convention for skills already solved this problem
  for that one directory; the rest of the repo just never got the same
  treatment.
- **`agents/relay/` went into instance, not engine**, even though relay's
  *code* (`bin/relay.py`) is pure engine. The distinction is instance vs.
  template: `agents/templates/` is the generic scaffold `create-agent.sh`
  copies from, `agents/relay/` and `agents/Marvin/` are both *instantiated*
  agents with their own `SYSTEM_PROMPT.md`. Same category as
  `agents/Marvin/`, just less personality-laden.
- **`config/protected-paths.json` is deliberately left unclassified.**
  It's structurally generic (protects the same class of paths on any
  install) but is per-install operational policy rather than code.
  Didn't want to force a guess where the honest answer is "ambiguous."

## The guardrail

`tests/test_repo_split_manifest.py` enforces two things against
`config/repo-split-manifest.json`:

1. **Completeness** -- every path `git ls-files` returns must be covered
   by the `engine`, `instance`, `ambiguous`, or `runtime_only_not_manifested`
   buckets. A new top-level file or directory that nobody classified
   fails CI instead of silently falling through the cracks.
2. **Leakage** -- none of the real Discord snowflake IDs pulled live from
   `config/channels.json` (server IDs, channel IDs, `known_bots` entries)
   may appear inside a file classified as `engine`. This is the concrete
   version of "don't let instance data creep into code that's supposed to
   be portable" -- exactly the failure mode a real repo split would make
   structurally impossible, caught here without moving anything yet.

Nothing physically moved in this phase. The value is knowing, mechanically
and continuously, which half of the repo is which -- so Phase 3 is a
matter of executing a known split instead of re-deriving it under
pressure.
