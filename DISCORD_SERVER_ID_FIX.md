# Fix: relay silently drops every Discord message (`server_id` vs `server_ids`)

## Symptom

The bot showed online with a green status in Discord, `Discord bot ready as
Marvin` logged cleanly, the agent subprocesses were healthy (`IDLE`,
`alive: true`), and the Discord gateway connection was confirmed established
— but messages sent in any guild channel (e.g. `#general`) produced **no
activity whatsoever**: no entry in the message capture archive
(`data/messages/*.jsonl`), no row in `message_queue`, no log line, no error.
Total silence, which made it look like a connection problem even though the
connection was fine.

Scheduled internal heartbeats (posted by `scheduler.py` into `#signals`)
worked the entire time, which was misleading — it made the system look
"partially alive" when actually the heartbeat path never goes through
`on_message`/guild-filtering at all, so it couldn't have shown this bug
either way.

## Root cause

`config/channels.json` stores the connected Discord guild ID(s) under
**`server_ids`** (a list — added when a second server, "Crab Cavern", was
connected):

```json
{
  "server_ids": ["111111111111111111", "222222222222222222"],
  "channels": { ... }
}
```

But `bin/relay.py`'s `DiscordAdapter.setup_hook()` was still reading the
older singular key:

```python
self.server_id = channels_config.get("server_id")   # -> None, key doesn't exist
```

`on_message` then does:

```python
if message.guild and str(message.guild.id) != self.server_id:
    return
```

With `self.server_id == None`, `str(guild.id) != None` is `True` for every
real guild, so **every guild message returns immediately**, before
`capture_message()` ever runs. No exception, no log line — a clean early
return that looks identical to "nothing arrived."

This was a config/code mismatch introduced when multi-server support
(`server_id` → `server_ids`) was added to the config schema but the adapter
code wasn't updated to match.

## How it was found

Diagnosing this required actually seeing discord.py's own gateway traffic —
by default `bin/relay.py` only attaches handlers to its own `"relay"`
logger; the `"discord"` logger (and root logger) have no handler anywhere in
the codebase, so discord.py's internal warnings/errors go nowhere visible.
Temporarily attaching a `StreamHandler` to the `"discord"` logger and adding
an unconditional trace line at the top of `on_message` showed the gateway
was receiving `GUILD_CREATE`/`MESSAGE_CREATE` events fine — the bot just
wasn't acting on them. That pointed straight at the guild-ID filter.

**Takeaway for next time:** if messages vanish with zero log output anywhere
(not even an error), suspect a silent early-return in `on_message`/routing
logic before reaching for infra-level explanations (gateway, intents,
network). A `str(x) != None` comparison is always `True` — any code path
where a filter value can silently resolve to `None` is worth checking first.

## Fix

`bin/relay.py`:

- `setup_hook()` now reads `server_ids` (list) from `channels_config`, with
  a fallback to the legacy singular `server_id` for older configs:

  ```python
  server_ids = channels_config.get("server_ids")
  if server_ids is None:
      single = channels_config.get("server_id")
      server_ids = [single] if single else []
  self.server_ids = [str(s) for s in server_ids]
  ```

- `on_message()`'s guild filter now checks membership in that list:

  ```python
  if message.guild and str(message.guild.id) not in self.server_ids:
      return
  ```

**Diff summary:** `bin/relay.py`, +9/-4.

## Verification

Hot-patched into the running container and confirmed via the message
capture log: a real Discord message sent in `#general` was captured,
queued, routed to Marvin, and answered in-channel — full round trip working.

## Known follow-up (not yet done as of this fix)

While debugging this, temporary verbose logging was added directly to the
running container's `relay.py` (full message content + discord.py gateway
debug output) to get visibility into the gateway. **That patch also
accidentally overwrote an anti-loop safety check** (an `∎` termination-token
filter in `on_message`) that the live agent had added at runtime to prevent
it and another agent ("Amos") from looping forever in `#agent-chat` — that
check only ever existed in the container's live file, never in this
source tree, so it isn't preserved here. It needs to be re-added (the
running agent has full context on what it wrote and is the best source for
restoring it exactly). The temporary debug logging itself should also be
stripped from the live container if still present — it logs raw Discord
message content, which shouldn't be persisted long-term.
