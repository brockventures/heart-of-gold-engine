# Fix: agent subprocesses crash-loop with "Session ID ... is already in use"

## Symptom

After any container restart (crash, `docker compose restart`, host reboot,
etc.), both agent subprocesses failed to come up:

```
[WARNING] relay stderr: Error: Session ID 4806d89a-8509-4c81-bb44-a67c558201a4 is already in use.
[WARNING] Marvin stderr: Error: Session ID ac32c10c-7221-4796-9804-c794d092e119 is already in use.
```

Every subsequent message queued for either agent then failed:

```
[ERROR] Error sending to Marvin: Connection lost
```

The bot appeared "up" (supervisord, agent-server, dashboard, relay, scheduler
all reported RUNNING) but neither agent could actually process messages.

## Root cause

`start_agent_subprocess()` in `bin/agent-server.py` always launched the
Claude CLI with:

```
claude -p ... --session-id <id> ...
```

`--session-id` only succeeds when creating a **brand-new** session. Each
agent's session ID is persisted in `agent-server.db` (`sessions` table) and
reused on every start, specifically so the agent's conversation history
carries across restarts. But once the CLI has written a transcript for that
ID (`~/.claude/projects/<slug>/<session-id>.jsonl` — which happens on the
very first successful run), it refuses to reuse `--session-id` for the same
ID and exits with the "already in use" error instead.

So the first-ever run of a fresh install worked fine; every restart after
that failed, because the transcript file already existed.

Notably, `reload_agent()`'s own docstring already described the intended
behavior — *"The respawn calls `--resume` on the existing session_id"* — but
the code never actually did this; both fresh starts and respawns funneled
through the same `start_agent_subprocess()`, which unconditionally used
`--session-id`.

## Fix

`bin/agent-server.py`:

- Added `session_transcript_exists(session_id)`, which checks whether
  `~/.claude/projects/*/<session_id>.jsonl` already exists.
- `start_agent_subprocess()` now passes `--resume <id>` when a transcript
  already exists, and `--session-id <id>` only when the session is genuinely
  new (first boot, or after `clear_session()` generates a fresh UUID).
- The startup log line now records whether the subprocess is resuming or
  starting a new session, e.g.:

  ```
  [INFO] Starting Marvin subprocess (model=sonnet, session=ac32c10c, resuming)
  ```

**Diff summary:** `bin/agent-server.py`, +14/-2.

```python
def session_transcript_exists(session_id: str) -> bool:
    """Check whether the Claude CLI already has a transcript for this
    session ID. `--session-id` only works for brand-new sessions; once a
    transcript file exists the CLI refuses with "Session ID ... is already
    in use", so callers must switch to `--resume` for that ID.
    """
    claude_projects_dir = Path.home() / ".claude" / "projects"
    return any(claude_projects_dir.glob(f"*/{session_id}.jsonl"))
```

```python
    # --session-id only works for a brand-new session; once the CLI has
    # written a transcript for this ID (i.e. on every restart after the
    # first), it must be resumed instead or the CLI exits with
    # "Session ID ... is already in use".
    resuming = session_transcript_exists(session_id)

    cmd = [
        "claude", "-p",
        ...
        "--resume" if resuming else "--session-id", session_id,
        ...
    ]
```

## Verification

Rebuilt the image locally and recreated the container. Post-fix logs on
restart:

```
[INFO] Starting Marvin subprocess (model=sonnet, session=ac32c10c, resuming)
[INFO] Marvin subprocess started (PID 37)
[INFO] Starting relay subprocess (model=haiku, session=4806d89a, resuming)
[INFO] relay subprocess started (PID 40)
...
[INFO] Discord bot ready as Marvin (ID: 000000000000000000)
```

No "already in use" errors, no `Connection lost` errors. Container stayed up
with 0 restarts through the soak check.

## Unrelated, harmless warning also present in the logs

```
⚠ "next start" does not work with "output: standalone" configuration. Use "node .next/standalone/server.js" instead.
```

The dashboard's `next.config.js` sets `output: standalone`, but the
container starts it via `next start` instead of
`node .next/standalone/server.js`. It still serves correctly — this is just
Next.js flagging that the standalone build's own optimized server isn't
being used. Not touched by this fix; worth a follow-up if startup time /
image size matters, but out of scope here.
