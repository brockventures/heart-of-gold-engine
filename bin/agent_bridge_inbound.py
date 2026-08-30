#!/usr/bin/env python3
"""
agent_bridge_inbound.py — the Marvin-side half of the agent-bridge.
Design doc: specs/2026-08-27-agent-bridge.md §7.

One route, one method, one job: receive a `handoff` envelope (handoff.py)
posted by Amos's side over the Cloudflare Tunnel (bridge.iancoley.org ->
localhost:8787, see the cloudflared-bridge.service unit) and feed it into
the *same* validation/recording path a real #agent-chat Discord message
already goes through — no new decision logic, no execution surface.

Explicitly NOT wired here (deliberate, not an oversight): a valid
envelope with reply="required" does not enqueue a real Marvin turn via
agent-server.py's POST /message. Recording to context_box and mirroring
a blocked/waiting-human state to #general is as far as this goes tonight.
Letting an external network request spend a live agent turn is a bigger
blast-radius decision than "let Amos post a status note" and deserves
its own explicit review before it's wired up — filed as a known next
step, not silently included.

Auth: single static bearer token, one direction (Amos -> Marvin only;
this service never calls out). Token lives outside the repo at
~/.karakos/secrets/agent-bridge-inbound-auth-token, 0600, read once at
startup. Rotate by swapping the file and restarting the unit — stateless
service, restart is free (same call Amos made for his own token design).

Logging: stdlib logging to stdout, captured by journald automatically
since this runs under a systemd --user unit (no new logging infra,
matches Amos's side). Accepted logs kind/subject/token-label. Rejected
logs timestamp+reason only (bad-auth / bad-json / bad-envelope /
rate-limited) — never the payload, never on a bad-auth hit specifically,
so a prober doesn't get confirmation of what almost worked.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Deque
from collections import deque

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent))
from handoff import parse_handoff  # noqa: E402
import context_box  # noqa: E402
from outbox import add_pending  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] agent_bridge_inbound: %(message)s",
)
log = logging.getLogger(__name__)

HOST = os.environ.get("AGENT_BRIDGE_HOST", "127.0.0.1")  # tailscale-interface
# IP for this install, not 127.0.0.1/0.0.0.0 — was 127.0.0.1 behind a Cloudflare
# tunnel, which did the public exposure; now that tailnet is the transport
# (2026-08-28, Cloudflare tunnel decommissioned), this has to be reachable
# from off-box. Bound to the tailscale interface specifically, not 0.0.0.0 —
# same minimal-exposure principle as the original design, just a different
# interface. Matches the fix Amos made on his own receiver the same night.
# Real value lives in config/.env (AGENT_BRIDGE_HOST) — instance-specific,
# never committed. The 127.0.0.1 default here is inert until set.
PORT = 8787
MAX_BODY_BYTES = 32_768  # handoff envelopes are small by design (~450 tokens
# was the longest real one seen — see handoff.py's docstring); this is
# generous headroom, not a real-world size.
RATE_LIMIT_PER_MINUTE = 30
TOKEN_PATH = Path.home() / ".karakos" / "secrets" / "agent-bridge-inbound-auth-token"

# Sender label per token, for logging only — not part of auth. Extend if a
# second token/sender is ever added; today there's exactly one.
TOKEN_LABELS = {}


def load_token() -> str:
    if not TOKEN_PATH.exists():
        log.error(f"no token at {TOKEN_PATH} — refusing to start with no auth configured")
        sys.exit(1)
    token = TOKEN_PATH.read_text().strip()
    if not token:
        log.error(f"{TOKEN_PATH} is empty — refusing to start with no auth configured")
        sys.exit(1)
    TOKEN_LABELS[token] = "amos"
    return token


INBOUND_TOKEN = load_token()

# token -> deque of request timestamps within the current window. One token
# today, but keyed by token rather than a bare counter so this doesn't need
# rework if a second sender is added later.
_rate_windows: dict[str, Deque[float]] = {}


def _rate_limited(token: str) -> bool:
    now = time.monotonic()
    window = _rate_windows.setdefault(token, deque())
    cutoff = now - 60.0
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_MINUTE:
        return True
    window.append(now)
    return False


def _reject(reason: str, status: int) -> web.Response:
    log.warning(f"rejected reason={reason}")
    return web.json_response({"error": reason}, status=status)


async def handle_inbound(request: web.Request) -> web.Response:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[len("Bearer "):] != INBOUND_TOKEN:
        # No payload read/logged on a bad-auth hit — don't give a prober
        # confirmation of anything, including whether the body would have
        # parsed.
        return _reject("bad-auth", 401)

    token = auth[len("Bearer "):]
    if _rate_limited(token):
        return _reject("rate-limited", 429)

    raw = await request.read()
    if len(raw) > MAX_BODY_BYTES:
        return _reject("bad-json", 413)

    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _reject("bad-json", 400)

    if not isinstance(data, dict):
        return _reject("bad-json", 400)

    # Route through the *exact* same parser a real Discord message's
    # ```handoff fence goes through — wrapping, not reimplementing, so
    # there is exactly one place envelope validation lives. See
    # handoff.py's parse_handoff() for what "fails open" means for
    # individual fields (only `reply`/`kind` are load-bearing enough to
    # invalidate the whole envelope).
    fenced = "```handoff\n" + json.dumps(data) + "\n```"
    envelope = parse_handoff(fenced)
    if envelope is None:
        return _reject("bad-envelope", 400)

    if envelope.context_box:
        cb = envelope.context_box
        row = context_box.record(
            subject=envelope.subject,
            state=cb.state,
            blocked_on=cb.blocked_on,
            waiting_on=cb.waiting_on,
            sender="Amos",
            channel="agent-bridge",
        )
        if context_box.should_mirror(cb.state):
            add_pending(
                "general",
                context_box.render_mirror_line(envelope.subject or "(no subject)", row),
            )
            log.info(
                f"[context_box] agent-bridge subject={envelope.subject!r} "
                f"state={cb.state} -> mirrored to #general"
            )

    log.info(
        f"accepted kind={envelope.kind} subject={envelope.subject!r} "
        f"token={TOKEN_LABELS.get(token, 'unknown')}"
    )
    return web.json_response({"status": "recorded"})


async def handle_404(request: web.Request) -> web.Response:
    return web.json_response({"error": "not-found"}, status=404)


def build_app() -> web.Application:
    app = web.Application(client_max_size=MAX_BODY_BYTES + 1024)
    app.router.add_post("/inbound", handle_inbound)
    app.router.add_route("*", "/{tail:.*}", handle_404)
    return app


if __name__ == "__main__":
    log.info(f"starting agent-bridge inbound service on {HOST}:{PORT}")
    web.run_app(build_app(), host=HOST, port=PORT, print=None)
