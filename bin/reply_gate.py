"""reply_gate.py — decide whether an inbound message deserves a full agent turn.

Ported from Amos's design (Mike's Karakos instance), shared in #agent-chat on
2026-08-05 after two agents in a shared channel demonstrated the problem live:
messages narrating each other rather than addressing each other, each costing
a full model turn on both sides. Adapted here for Marvin with credit intact —
this is his design, not a reinvention.

No dependencies. No discord.py, no LLM SDK, no async. It decides; the caller
executes.

    gate = ReplyGate(self_id=MY_BOT_ID, names=("marvin",))

    d = gate.evaluate(GateMessage(...))
    if d.needs_score:                       # your classifier, your model
        d = gate.resolve(d, await score(...))
    if d.wake:
        ...

Two tiers, and the split is the whole design.

  Tier 1 — free and deterministic. An @mention or a reply to something you
           wrote. These are unambiguous: a human or an agent pointed at you.
           No cooldown, ever, or you will drop the one message that mattered.

  Tier 2 — everything else, scored by a cheap fast model, with a per-channel
           cooldown, biased toward silence.

BEING NAMED IS NOT BEING ADDRESSED. This is the trap, and it is worth stating
plainly because it looks like a Tier 1 signal and is not one. "Marvin, thoughts
on this?" wants you. "Marvin is investigating", "watching for Marvin's reply",
"that's Marvin's tool output" want nothing — they are commentary, and answering
them produces more commentary to answer. A bare name goes to Tier 2 as a HINT
for the classifier, which can read the surrounding sentence. Amos's gate got
this wrong on day one and burned three turns learning it; ours inherits the
fix rather than relearning it.

The other rule, which is not code: post when the other side has something to
act on. Not to acknowledge, not to announce that you are waiting, not to
describe what the other is doing. Silence should be the normal state between
two agents — it means nothing is needed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


@dataclass(frozen=True)
class GateMessage:
    """One inbound message, normalised. Fill this from your own client."""
    channel_id: str
    author_id: str
    content: str
    mentions_self: bool = False
    is_reply_to_self: bool = False
    author_is_bot: bool = False
    # A ping to a shared role Marvin belongs to (e.g. @robots), as opposed
    # to a direct @mention of Marvin himself. Added 2026-08-30 after Mike
    # flagged (agent-chat, 05:13 UTC) that a @Robot broadcast fell through
    # to normal Tier 2 scoring instead of force-waking like a real
    # @mention -- Amos and Zero both had (and fixed) the identical gap on
    # their own sides same night. Caller resolves which role IDs count as
    # "self" from config; this field is just the pre-resolved bool.
    mentions_role: bool = False


@dataclass(frozen=True)
class Decision:
    wake: bool
    tier: str          # "self" | "tier1" | "cooldown" | "tier2"
    reason: str
    needs_score: bool = False
    named: bool = False
    score: Optional[float] = None
    channel_id: str = ""


class ReplyGate:
    def __init__(
        self,
        *,
        self_id: str,
        names: Iterable[str] = (),
        threshold: float = 0.5,
        cooldown_sec: float = 300.0,
        attention_marker: Optional[str] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.self_id = str(self_id)
        self.threshold = threshold
        self.cooldown_sec = cooldown_sec
        self._clock = clock
        # Attention marker (2026-08-08, per Ian): a channel-agreed literal
        # (e.g. an emoji unlikely to appear in ordinary technical prose)
        # that forces Tier 1 exactly like an @mention, without needing a
        # real Discord mention or the full handoff.py envelope. Added after
        # a real miss -- Amos wrote "Marvin -- ..." in plain prose (a bare
        # name, deliberately Tier 2 per the docstring above) and it sat
        # unread. A real @mention already solves this, but relies on both
        # sides remembering to type <@id> instead of a name every time; a
        # single agreed glyph is cheaper to get right consistently than
        # remembering Discord mention syntax, especially across two
        # separate Karakos instances that don't share code. None disables
        # it (default) -- existing deployments/tests are unaffected.
        self.attention_marker = attention_marker
        self._name_re = (
            re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.I)
            if names else None
        )
        # Per channel, not global: a busy room must not mute a quiet one.
        self._last_tier2_wake: dict[str, float] = {}

    # -- step 1 -------------------------------------------------------------
    def evaluate(self, msg: GateMessage) -> Decision:
        """Cheap pass. Returns a final Decision, or one with needs_score=True."""
        named = bool(self._name_re and self._name_re.search(msg.content or ""))

        if str(msg.author_id) == self.self_id:
            return Decision(False, "self", "own message", channel_id=msg.channel_id)

        has_marker = bool(self.attention_marker) and self.attention_marker in (msg.content or "")

        if msg.mentions_self or msg.mentions_role or msg.is_reply_to_self or has_marker:
            reason = (
                "@mention" if msg.mentions_self else
                "@role mention" if msg.mentions_role else
                "reply to you" if msg.is_reply_to_self else
                "attention marker"
            )
            return Decision(
                True, "tier1", reason,
                named=named, channel_id=msg.channel_id,
            )

        last = self._last_tier2_wake.get(msg.channel_id, 0.0)
        remaining = self.cooldown_sec - (self._clock() - last)
        if remaining > 0:
            return Decision(
                False, "cooldown", f"{int(remaining)}s left",
                named=named, channel_id=msg.channel_id,
            )

        return Decision(
            False, "tier2", "needs scoring",
            needs_score=True, named=named, channel_id=msg.channel_id,
        )

    # -- step 2 -------------------------------------------------------------
    def resolve(
        self, decision: Decision, score: Optional[float], *, fallback: Optional[bool] = None
    ) -> Decision:
        """Apply your classifier's 0-1 score. Only call on needs_score decisions.

        A wake here starts the cooldown; a decline does not. Declining is the
        cheap path and must stay free.

        `score=None` means the classifier itself failed (timeout, bad
        output, missing binary) — NOT a real low score. Fixed 2026-08-09
        after Amos hit this exact bug on his side: a prior version of this
        caller collapsed every scorer failure into 0.0, the same value a
        genuine confident-no produces, so a dead classifier and a real
        decline were indistinguishable in the logs (`score=0.00` either
        way). The caller must not do that anymore — pass `score=None` and
        supply `fallback` (its own decision from, e.g., a handoff envelope
        plus a substance floor) instead of coercing to a number. The
        resulting Decision is tagged "tier2-fallback" with `score=None` so
        the two cases stay distinguishable downstream.
        """
        if not decision.needs_score:
            return decision
        if score is None:
            wake = bool(fallback)
            if wake:
                self._last_tier2_wake[decision.channel_id] = self._clock()
            return Decision(
                wake, "tier2-fallback",
                f"scorer failed, fallback={'wake' if wake else 'quiet'}",
                named=decision.named, score=None, channel_id=decision.channel_id,
            )
        wake = score >= self.threshold
        if wake:
            self._last_tier2_wake[decision.channel_id] = self._clock()
        return Decision(
            wake, "tier2", f"scored {score:.2f} vs {self.threshold}",
            named=decision.named, score=score, channel_id=decision.channel_id,
        )

    def note_human_message(self, channel_id: str) -> None:
        """Clear the cooldown when a human speaks — they reset the room."""
        self._last_tier2_wake.pop(channel_id, None)


# A prompt that encodes the lesson. Adapt the name and the role; keep the
# narration paragraph, it is the part that earns its place.
SCORER_PROMPT = """\
You are the wake-gate for {agent}, an agent in a Discord channel shared with \
other agents and their humans. Waking {agent} costs a full expensive turn, so \
the bar is real. Score how much the latest message needs {agent} SPECIFICALLY.

WAKE for: a question or request aimed at {agent}; a continuation of an exchange \
{agent} is already in; someone stuck on something {agent} would know; a direct \
opening for {agent}'s view.

STAY ASLEEP for: chatter between other parties; pleasantries needing no answer; \
an agent thinking out loud; anything {agent} would add nothing to.

Being NAMED is not being addressed. Another agent narrating what {agent} is \
doing — "{agent} is investigating", "watching for {agent}'s reply", "that's \
{agent}'s tool output" — is commentary, wants nothing back, and scores LOW. \
"{agent}, thoughts?" scores high. Judge the sentence, not the name.

Recent conversation:
{context}

The latest message is from {author}. Output ONLY a number 0.0-1.0. Bias toward \
silence: a missed message is still readable later, a needless wake is not \
refundable.
"""


# -- selftest ---------------------------------------------------------------
def _selftest() -> int:
    """A gate that cannot decline is not a gate."""
    t = {"now": 1000.0}
    g = ReplyGate(self_id="me", names=("marvin",), cooldown_sec=300,
                  clock=lambda: t["now"])
    M = lambda **kw: GateMessage(channel_id="c1", author_id="them", **kw)
    fails = 0

    def check(label, got, want):
        nonlocal fails
        ok = got == want
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  (got {got!r})"))

    print("── reply_gate selftest ──")
    check("@mention wakes",
          g.evaluate(M(content="hi", mentions_self=True)).wake, True)
    check("reply-to-you wakes",
          g.evaluate(M(content="hi", is_reply_to_self=True)).wake, True)
    check("own message never wakes",
          g.evaluate(GateMessage(channel_id="c1", author_id="me",
                                 content="x", mentions_self=True)).wake, False)

    d = g.evaluate(M(content="Marvin is investigating the relay"))
    check("bare name does NOT wake on its own", d.wake, False)
    check("bare name is flagged for the scorer", (d.needs_score, d.named), (True, True))

    check("low score declines", g.resolve(d, 0.2).wake, False)

    d2 = g.evaluate(M(content="anyone know how the cascade works?"))
    check("high score wakes", g.resolve(d2, 0.9).wake, True)

    d3 = g.evaluate(M(content="another ambient line"))
    check("cooldown holds after a tier-2 wake", d3.tier, "cooldown")

    check("tier 1 ignores the cooldown",
          g.evaluate(M(content="hey", mentions_self=True)).wake, True)

    d_role = g.evaluate(M(content="crew, status?", mentions_role=True))
    check("@role mention wakes like a real @mention", d_role.wake, True)
    check("@role mention reason is distinct from @mention", d_role.reason, "@role mention")
    check("@role mention ignores the cooldown", d_role.tier, "tier1")

    g.note_human_message("c1")
    check("a human resets the cooldown",
          g.evaluate(M(content="ambient again")).needs_score, True)

    g_marker = ReplyGate(self_id="me", names=("marvin",), cooldown_sec=300,
                          attention_marker="\U0001F4E8", clock=lambda: t["now"])
    check("attention marker wakes without a real @mention",
          g_marker.evaluate(M(content="\U0001F4E8 marvin, need this today")).wake, True)
    check("attention marker ignores the cooldown, like @mention",
          g_marker.evaluate(M(content="ambient")).needs_score, True)
    g_marker.resolve(g_marker.evaluate(M(content="ambient")), 0.9)  # start a cooldown
    check("marker still wakes during an active cooldown",
          g_marker.evaluate(M(content="\U0001F4E8 urgent")).wake, True)
    check("no marker configured means plain prose never force-wakes",
          g.evaluate(M(content="\U0001F4E8 marvin, need this today")).wake, False)

    d4 = g.evaluate(GateMessage(channel_id="c2", author_id="them", content="other room"))
    check("cooldown is per-channel", d4.needs_score, True)

    t["now"] += 301
    check("cooldown expires", g.evaluate(M(content="later")).needs_score, True)

    # -- 2026-08-09: scorer-failure fallback must not collapse to 0.0 --
    d5 = g.evaluate(M(content="scorer will fail on this one"))
    r5 = g.resolve(d5, None, fallback=True)
    check("score=None with fallback=True wakes", r5.wake, True)
    check("score=None result is tagged tier2-fallback, not tier2", r5.tier, "tier2-fallback")
    check("score=None result keeps score=None (not coerced to a number)", r5.score, None)

    t["now"] += 301
    d6 = g.evaluate(M(content="scorer will fail on this one too"))
    r6 = g.resolve(d6, None, fallback=False)
    check("score=None with fallback=False stays quiet", r6.wake, False)
    check("score=None fallback=False does NOT start a cooldown",
          g.evaluate(M(content="right after")).needs_score, True)

    print("PASS  the gate declines when it should" if not fails else f"FAIL  {fails} case(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
