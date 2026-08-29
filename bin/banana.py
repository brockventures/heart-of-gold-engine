#!/usr/bin/env python3
"""
banana.py — the Speaking Banana: turn-claim signaling for shared multi-bot
channels. Design doc: specs/2026-08-28-speaking-banana.md.

Problem this solves: two bots sharing a channel (#agent-chat, #lounge —
Crab Cavern, where Marvin and Amos both post) can each independently
decide "this is mine to answer" and generate a reply to the same message
with no visibility into the other one doing the same. First raised by
Ryan in #lounge 2026-08-28, designed with Ian (#general) and Amos
(#agent-chat) the same night.

Mechanism: a bot claims the floor by prefixing a reply with 🍌. State
(who holds it, when, last activity) lives here rather than being
re-derived by parsing scrollback for the emoji — same reasoning
context_box.py already established for blocked/waiting-human state.

Release is explicit hand-back by default. Timeout exists only as a
backstop for a genuinely dead holder, not as normal pacing — a single
fixed number can't tell "still doing real tool work" from "hung," so
this uses two tiers:

  - GRACE_SECONDS: the claim is simply uncontested, no liveness question
    asked at all. Most replies land inside this window.
  - CEILING_SECONDS: past this with zero activity (no heartbeat, no
    release), the claim is treated as expired — auto-released without
    anyone having to explicitly hand it back.

Liveness in between the two is self-reported (heartbeat(), stamps
last_active_ts) — not a request/response ping. Amos's reasoning, agreed
2026-08-28: a holder that's mid-generation and can't answer a ping can't
answer a smart ping any better than a dumb one, so don't build one.

Enforcement posture (watch-first, agreed with Amos): this module never
blocks a claim or a reply. claim() logs a collision if it overwrites an
active, non-expired claim held by someone else, but still records the
new claim — visibility, not a gate. No caller today actually checks
get_status() before generating; wiring that in is a deliberate later
step once a real collision pattern shows up, not before.

Directed preempt ("I demand a reply from X") is in the design doc but
NOT implemented here yet — detection method (explicit field vs. parsing
the phrase out of free text) is still unscoped. claim() takes a
`preempt` flag for when that lands; until then nothing sets it.

Storage: data/banana_claims.json, one row per channel name. Written
atomically (tmp file + rename), same pattern as context_box.py/outbox.py.
This is local-only bookkeeping — see the shared API note below for what's
actually authoritative across both bots.

Shared claim API (2026-08-28, Amos + Arbiter): the local board above is
each side's own inference from watching Discord (what Amos's design doc
calls "each side reading an explicit signal as it arrives" — deterministic,
not judgment, but asynchronous, no ack). Arbiter flagged that as not a
real handoff; Amos built the actual fix, a synchronous claim/release API
at https://banana.mikecarmody.net, Postgres-backed with real row locking
(SELECT ... FOR UPDATE) — an actual compare-and-swap, not a best-effort
guess, hosted off Mike's box so it survives a Pi reboot. Bearer token at
~/.karakos/secrets/banana-claims-token (0600, outside the repo, same
convention as the agent-bridge tokens). `claim_self()`/`release_self()`
are the two functions that talk to it — for Marvin's *own* claims only,
since the token's identity is locked to "marvin" server-side (can't claim
as "amos", same as Amos's token can't claim as "marvin"). Falls back to
local-only recording if the API's unreachable, matching the same
degrade-instead-of-block posture Amos's own client uses. Claims observed
by *watching* another bot's Discord message (relay.py's inbound hook)
stay purely local — that's still just inference, and correctly so: only
the bot making a claim calls the API for it, on its own authority.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
BOARD_PATH = WORKSPACE_ROOT / "data" / "banana_claims.json"

CLAIM_EMOJI = "🍌"

API_BASE_URL = "https://banana.mikecarmody.net"
API_TOKEN_PATH = Path.home() / ".karakos" / "secrets" / "banana-claims-token"
API_TIMEOUT = aiohttp.ClientTimeout(total=5)
API_HOLDER_IDENTITY = "marvin"  # locked server-side to this token; can't claim as anyone else

# Picked from the top of Amos's stated ranges (60-90s grace, 5-10min
# ceiling) — generous on purpose, since nothing enforces against these
# yet and a false "expired" is more disruptive than a claim sitting
# uncontested a little longer than strictly needed. Not load-bearing
# today; revisit once something actually consults get_status().
GRACE_SECONDS = 90
CEILING_SECONDS = 600

log = logging.getLogger("banana")


def in_scope(channel_id: str, channels_config: dict) -> bool:
    """True if this channel shares its floor with other bots. Mirrors the
    quiet-mode guild check (agent-server.py's read_agent_response): Heart
    of Gold has one bot per channel, nothing to claim; any other guild a
    configured channel lives in (Crab Cavern today) can collide."""
    channel_cfg = next(
        (cfg for cfg in channels_config.get("channels", {}).values()
         if cfg.get("id") == channel_id),
        None,
    )
    if channel_cfg is None:
        return False
    primary_guild_ids = channels_config.get("server_ids", [])
    primary_guild_id = primary_guild_ids[0] if primary_guild_ids else None
    return channel_cfg.get("guild_id") != primary_guild_id


def _load_board() -> dict:
    if not BOARD_PATH.exists():
        return {}
    try:
        with open(BOARD_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        # A corrupt board shouldn't take down message handling — same
        # fail-open posture as context_box.py/handoff.py.
        return {}


def _save_board(board: dict) -> None:
    BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BOARD_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(board, f, indent=2, sort_keys=True)
    tmp.replace(BOARD_PATH)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def starts_with_claim(text: str) -> bool:
    """True if `text` opens with the claim emoji, allowing for leading
    whitespace. Deliberately strict about *leading* — a banana mentioned
    mid-sentence isn't a claim, matches the "posts 🍌 at the start of a
    message" design."""
    return bool(text) and text.lstrip().startswith(CLAIM_EMOJI)


def get_status(channel: str) -> dict:
    """Current claim state for `channel`, with expiry computed on read
    rather than by a background sweep — nothing here runs on a timer.
    Returns a dict always; `active` is False for an unclaimed, released,
    or expired channel."""
    board = _load_board()
    row = board.get(channel)
    if not row:
        return {"active": False, "holder": None}

    now = _now()
    last_active = _parse_ts(row.get("last_active_ts", "")) or now
    elapsed = (now - last_active).total_seconds()

    if row.get("released"):
        return {**row, "active": False, "seconds_since_activity": elapsed}

    if elapsed > CEILING_SECONDS:
        return {**row, "active": False, "expired": True, "seconds_since_activity": elapsed}

    claimed_at = _parse_ts(row.get("claimed_at", "")) or now
    past_grace = (now - claimed_at).total_seconds() > GRACE_SECONDS
    return {
        **row,
        "active": True,
        "past_grace": past_grace,
        "seconds_since_activity": elapsed,
    }


def claim(channel: str, holder: str, preempt: bool = False) -> dict:
    """Claim the floor in `channel` for `holder`. Always succeeds and
    always records — this is visibility, not a gate (watch-first
    enforcement, agreed with Amos 2026-08-28). If an active, non-expired
    claim held by someone else already exists, the collision is logged
    (and returned as `collision_with`) but the new claim still overwrites
    it, `preempt` or not — no blocking machinery here yet.

    `preempt` is accepted now so directed-pass callers have a stable
    signature to target once that detection is built; it doesn't change
    behavior today (claim() never blocks regardless)."""
    prior = get_status(channel)
    collision_with = None
    if prior.get("active") and prior.get("holder") != holder:
        collision_with = prior.get("holder")
        log.warning(
            f"[banana] {channel}: {holder} claimed over {collision_with}'s "
            f"active claim (held {prior.get('seconds_since_activity', 0):.0f}s) "
            f"— recorded, not blocked (watch-first)"
        )

    board = _load_board()
    now_iso = _now().isoformat()
    row = {
        "holder": holder,
        "claimed_at": now_iso,
        "last_active_ts": now_iso,
        "released": False,
    }
    board[channel] = row
    _save_board(board)
    log.info(f"[banana] {channel}: {holder} claimed the floor")
    return {**row, "collision_with": collision_with}


def heartbeat(channel: str, holder: str) -> bool:
    """Self-reported liveness — stamp last_active_ts for the current
    claim. No-op (returns False) if `holder` isn't the current holder or
    there's no active claim; a heartbeat from the wrong bot doesn't
    extend someone else's claim."""
    board = _load_board()
    row = board.get(channel)
    if not row or row.get("released") or row.get("holder") != holder:
        return False
    row["last_active_ts"] = _now().isoformat()
    board[channel] = row
    _save_board(board)
    return True


def release(channel: str, holder: Optional[str] = None) -> bool:
    """Explicit hand-back — the default release path (not the timeout
    backstop). No-op (returns False) if there's no row or it's already
    released. Logs, but does not refuse, a holder mismatch — enforcement
    is watch-first here too."""
    board = _load_board()
    row = board.get(channel)
    if not row or row.get("released"):
        return False
    if holder and row.get("holder") != holder:
        log.warning(
            f"[banana] {channel}: release from {holder} but "
            f"{row.get('holder')} holds the claim — releasing anyway"
        )
    row["released"] = True
    row["released_at"] = _now().isoformat()
    board[channel] = row
    _save_board(board)
    log.info(f"[banana] {channel}: released by {holder or 'unknown'}")
    return True


_api_token_cache: Optional[str] = None


def _load_api_token() -> Optional[str]:
    global _api_token_cache
    if _api_token_cache is not None:
        return _api_token_cache or None
    try:
        _api_token_cache = API_TOKEN_PATH.read_text().strip()
    except OSError as e:
        log.warning(f"[banana] couldn't read API token at {API_TOKEN_PATH}: {e}")
        _api_token_cache = ""
    return _api_token_cache or None


class BananaBlocked(Exception):
    """Raised by _api_post when the API deliberately rejects a request
    (409, body carries `blocked: true`) — never a transport failure.
    Added 2026-08-29 alongside Amos's claim.js fix (conflicting unexpired
    claim now returns 409 with the row left untouched, instead of a 200
    with a `conflict` field). Must stay a distinct signal from every other
    non-200: _api_post's normal fallback path ("couldn't reach the API,
    use local-only claim()/release()") always succeeds unconditionally,
    so folding a real "no" from the server into that same bucket would
    make the new server-side block silently undo itself on this end —
    exactly the failure mode Amos flagged live. Caught and handled inside
    claim_self()/release_self() only; must never escape banana.py's
    public functions."""
    def __init__(self, holder: Optional[str], state: dict):
        self.holder = holder
        self.state = state
        super().__init__(f"blocked by {holder}")


async def _api_post(path: str, payload: dict) -> Optional[dict]:
    """POST to the shared claim API. Returns the parsed response on a 200,
    None for every transport-level failure mode (bad token, network
    failure, timeout, an unexpected non-200) — that's still "couldn't
    reach the API," which is all the caller needs to know before falling
    back to local-only recording. Raises BananaBlocked instead for a 409
    carrying `blocked: true` — that's the API answering on purpose, not
    failing to answer at all, and the two must never be conflated (see
    BananaBlocked's docstring)."""
    token = _load_api_token()
    if not token:
        log.warning(f"[banana] no API token available, skipping {path}")
        return None
    try:
        async with aiohttp.ClientSession(timeout=API_TIMEOUT) as session:
            async with session.post(
                f"{API_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload,
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 409:
                    try:
                        body = await resp.json()
                    except Exception:
                        body = {}
                    if body.get("blocked"):
                        raise BananaBlocked(body.get("holder"), body.get("state") or {})
                    text = json.dumps(body)
                else:
                    text = await resp.text()
                log.warning(f"[banana] API {path} returned HTTP {resp.status}: {text}")
                return None
    except BananaBlocked:
        raise
    except Exception as e:
        log.warning(f"[banana] API {path} unreachable, falling back to local: {e}")
        return None


async def claim_self(channel: str, subject: Optional[str] = None) -> dict:
    """Claim the floor as Marvin, authoritatively — calls the shared API
    first (server-side compare-and-swap, real cross-bot synchronization),
    then records the result locally either way so get_status()/
    render_board() stay fast, local, and in sync. Falls back to the local-
    only claim() if the API's genuinely unreachable, same degrade-not-block
    posture Amos's own client uses — but a deliberate 409 (BananaBlocked)
    is handled separately below, precisely so it can't take that fallback
    path and quietly overwrite a real rejection. This is the only path
    that should ever call the API with holder="marvin" — it's Marvin's own
    claim, made on Marvin's own authority, same rule Amos's side follows
    for his."""
    try:
        result = await _api_post("/api/claim", {"holder": API_HOLDER_IDENTITY, "subject": subject or channel})
    except BananaBlocked as e:
        # The API said no, on the record — do NOT fall through to the
        # local-only claim() below, that path always succeeds regardless
        # of who holds the floor and would silently manufacture a claim
        # the server just refused. Mirror the real holder into the local
        # board instead, so get_status() here agrees with the API's own
        # /status rather than lying that Marvin holds it.
        log.warning(
            f"[banana] {channel}: Marvin's claim rejected by API — "
            f"{e.holder} holds it (409, not a network failure)"
        )
        board = _load_board()
        if e.state:
            board[channel] = {**e.state, "via_api": True}
            _save_board(board)
        return {"holder": e.holder, "blocked": True, "collision_with": e.holder, "via_api": True}

    if result is None:
        return claim(channel, "Marvin")

    state = result.get("state", {})
    conflict = result.get("conflict")
    if conflict:
        log.warning(f"[banana] {channel}: Marvin claimed over {conflict}'s active claim (via API) — recorded, not blocked")

    board = _load_board()
    row = {
        "holder": "Marvin",
        "claimed_at": datetime.fromtimestamp(state.get("claimed_at", _now().timestamp()), tz=timezone.utc).isoformat(),
        "last_active_ts": datetime.fromtimestamp(state.get("last_active_ts", _now().timestamp()), tz=timezone.utc).isoformat(),
        "released": state.get("released", False),
        "via_api": True,
    }
    board[channel] = row
    _save_board(board)
    log.info(f"[banana] {channel}: Marvin claimed the floor (via shared API)")
    return {**row, "collision_with": conflict}


async def release_self(channel: str) -> bool:
    """Explicit hand-back as Marvin, authoritatively — same API-first,
    local-fallback shape as claim_self(). No known case makes /api/release
    return a 409 today, but _api_post can raise BananaBlocked for any
    endpoint, so this handles it defensively rather than letting an
    unexpected one crash the caller (agent-server.py's turn-end release
    has no try/except around this call)."""
    try:
        result = await _api_post("/api/release", {"holder": API_HOLDER_IDENTITY})
    except BananaBlocked as e:
        log.warning(f"[banana] {channel}: Marvin's release rejected by API — {e.holder} holds it (409)")
        return False

    if result is None:
        return release(channel, "Marvin")

    released = bool(result.get("released"))
    if released:
        board = _load_board()
        row = board.get(channel, {})
        row["released"] = True
        row["released_at"] = _now().isoformat()
        row["via_api"] = True
        board[channel] = row
        _save_board(board)
        log.info(f"[banana] {channel}: Marvin released the floor (via shared API)")
    else:
        log.info(f"[banana] {channel}: release via API declined — Marvin wasn't the current holder")
    return released


def render_board() -> str:
    """Human-readable dump of every channel's current state, expired or
    not — for manual checking (CLI, or a future /sys banana command),
    same role render_board() plays in context_box.py."""
    board = _load_board()
    if not board:
        return "**banana**: no claims recorded."
    lines = ["**banana** — current claim state:"]
    for channel in sorted(board):
        status = get_status(channel)
        state = "active" if status["active"] else (
            "expired" if status.get("expired") else "released"
        )
        lines.append(
            f"- `{channel}`: {status.get('holder', '?')} [{state}] "
            f"({status.get('seconds_since_activity', 0):.0f}s since activity)"
        )
    return "\n".join(lines)


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", help="Render the current board")

    claim_p = sub.add_parser("claim", help="Claim a channel (mainly for testing)")
    claim_p.add_argument("channel")
    claim_p.add_argument("holder")

    hb_p = sub.add_parser("heartbeat", help="Stamp liveness for the current claim")
    hb_p.add_argument("channel")
    hb_p.add_argument("holder")

    rel_p = sub.add_parser("release", help="Explicitly release a claim")
    rel_p.add_argument("channel")
    rel_p.add_argument("--holder")

    status_p = sub.add_parser("status", help="Show one channel's computed status")
    status_p.add_argument("channel")

    args = parser.parse_args()

    if args.cmd == "show":
        print(render_board())
    elif args.cmd == "claim":
        print(claim(args.channel, args.holder))
    elif args.cmd == "heartbeat":
        print(heartbeat(args.channel, args.holder))
    elif args.cmd == "release":
        print(release(args.channel, args.holder))
    elif args.cmd == "status":
        print(get_status(args.channel))


if __name__ == "__main__":
    main()
