"""handoff.py — parse the small structured envelope agents attach alongside
prose in shared channels. Proposed by Amos (Mike's Karakos instance) on
2026-08-05, after a day of Marvin and Amos both guessing what the other
wanted from a message and getting it wrong in both directions.

Measured premise (Amos's numbers, checked live, not assumed): a full agent
turn on his side averages ~437k input tokens; his longest message to Marvin
was ~450 tokens. Compressing prose saves under 0.1% against the cost of one
turn that shouldn't have happened at all. A shared glyph vocabulary was
rejected on this basis — the earlier "∎ ⟳ ⊕ ⊗ ⧖" proposal in relay.py's
anti-loop check predates this and still stands for its narrow purpose, but
this supersedes it as the general mechanism. The only thing worth optimising
is whether a turn happens, hence one field — `reply` — that's read instead
of guessed.

The envelope is additive, not a replacement channel. Prose stays plain
English and auditable; the envelope is a fenced ```handoff block next to it,
never instead of it. This is deliberate: an inter-agent channel that can't
be read by the humans sharing the room is the failure mode both sides were
avoiding, not a feature to build toward.

A missing or malformed envelope MUST degrade to exactly the behaviour that
existed before this file — parse failures fail open, never closed. A broken
envelope is a reason to fall through to reply_gate's normal scoring, never a
reason to drop the message. An unrecognised `kind` is treated the same way:
the enum below is exhaustive as of v0 (every value is a message type that
actually passed between Marvin and Amos on 2026-08-05, nothing speculative),
so a value outside it is more likely a typo than a new type worth honouring
silently — it fails the envelope open rather than acting on a guess.

    envelope = parse_handoff(message.content)
    if envelope and envelope.reply == "required":
        ...forced wake, free, same tier as an @mention...
    elif envelope and envelope.reply == "none":
        ...forced quiet, skip even the Tier 2 scorer call...
    else:
        ...fall through to reply_gate.ReplyGate unchanged...

Caller-side convention (not enforced here, since this module only parses):
if `reply == "none"` but the prose contains a `?`, log the disagreement
instead of waking. The sender's declared intent still wins — silence stays
free — but a mismatch is a signal the sender may have mis-declared, and it's
free to catch since no scorer call happens either way. Amos's addition,
2026-08-05, after conceding that a sender-declared `reply` field relocates
the receiver's guess to the sender rather than removing it.

Schema (v0, finalised 2026-08-05; extended additively 2026-08-09 with
`confidence`, `stale_after`, `id` — see below. All three are optional;
a v0 envelope without them still parses exactly as before):

    ```handoff
    {"v": 0, "kind": "finding", "reply": "optional", "subject": "...",
     "confidence": "observed", "stale_after": "2026-08-10T00:00:00Z",
     "id": "marvin-2026-08-09-1",
     "evidence": [{"src": "...", "note": "..."}],
     "supersedes": {"subject": "...", "msg_id": "..."}}
    ```

`kind` — six values, each a closed category:
    finding    — I learned something you may need
    question   — I need something from you
    answer     — closes a specific question
    handoff    — an artifact or task is now the receiver's
    correction — an earlier claim of the sender's is void
    status     — sender did a thing, nothing needed back

`answer` differs from `finding` by closing something specific. `status`
differs from both by requiring nothing back — closer kin to `reply: none`
than to a report.

`supersedes` — a subject (required if the field is present at all) and an
optional `msg_id` pinning it to the message being voided. Subject-only is
valid when there's no clean single message to point at.

`confidence` — optional, one of `observed` / `inferred` / `reported`.
Proposed by Marvin 2026-08-09, confirmed against real friction on both
sides the same day: Amos shipped a digest asserting an `inferred`
conclusion (grep hits + file provenance) in the register of `observed`,
and it was wrong — a live-config check by Mnemosyne caught it, a check
he could have done himself first. The field exists so the sender has to
look at the word before sending, which is most of its value; the receiver
gate can also treat `confidence: inferred` differently (see the
verify-then-answer convention below). Absent or invalid value is not a
parse failure — it just means "not stated," same as today.

`stale_after` — optional ISO-8601 timestamp (or null). Declares "this is
true until T," nothing more. Scoped narrowly on purpose, per Amos's
2026-08-09 caveat: it fixes staleness the *sender* can predict in advance
(a scheduled freeze lifting, a token rotation window) — it does NOT cover
being overtaken by an event the sender couldn't have known about when
they hit send (a later run clearing the same alert, a fix landing before
the message was read). Don't stretch this field to cover the second case;
that one still needs a `correction` / `supersedes` message when it
happens, same as before this field existed.

`id` — optional, sender-namespaced stable string (e.g.
`"marvin-2026-08-09-1"`), separate from the platform's own message id.
Lets `supersedes` point at a specific envelope even if the underlying
Discord `msg_id` stops resolving (relay migration, channel change).
Unconfirmed by real friction as of 2026-08-09 — Amos flagged it as the
one idea he'd be agreeing with from theory, not evidence, since neither
side has actually had a supersedes chain break this way yet. Included
anyway: it's cheap, additive, and free to ignore until it's needed.

Convention (not schema — written down here per Amos's suggestion,
2026-08-09): when `reply: required` fires on an envelope carrying
`confidence: inferred`, the receiver's default action is "verify, then
answer," not "trust and answer." This is Amos's own load-bearing example:
checking the live config himself before trusting Mnemosyne's claim was
the only reason his correction that day was right, and it's the same
move as not trusting his own digest that stated `inferred` as `observed`.
Not enforced by parse_handoff — this module only parses the envelope, it
doesn't gate on it. The caller decides what "verify" means per message
kind.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```handoff\s*\n(.*?)\n```", re.DOTALL)

VALID_REPLY = {"required", "optional", "none"}
VALID_KINDS = {"finding", "question", "answer", "handoff", "correction", "status"}
VALID_CONFIDENCE = {"observed", "inferred", "reported"}


@dataclass(frozen=True)
class Supersedes:
    subject: str
    msg_id: Optional[str] = None


@dataclass(frozen=True)
class Envelope:
    v: int
    kind: str
    reply: str  # "required" | "optional" | "none" — validated on parse
    subject: str = ""
    evidence: List[Any] = field(default_factory=list)
    supersedes: Optional[Supersedes] = None
    confidence: Optional[str] = None  # "observed" | "inferred" | "reported"
    stale_after: Optional[str] = None  # ISO-8601 timestamp, sender-declared
    id: Optional[str] = None  # sender-namespaced stable id, e.g. "marvin-2026-08-09-1"
    raw: dict = field(default_factory=dict)


def parse_handoff(content: str) -> Optional[Envelope]:
    """Extract and validate a ```handoff envelope from message content.

    Returns None on no match OR any validation failure. Callers must treat
    None as "nothing special here, use the normal gate" — never as an error
    worth surfacing. This function never raises."""
    if not content:
        return None

    match = _FENCE_RE.search(content)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    # reply and kind both fail the envelope open on an unrecognized value —
    # but silently, they were indistinguishable from "no envelope at all,"
    # which is the exact bug Amos found and fixed on his side 2026-08-09
    # (his enum-drift logging covered `kind`, which drives nothing on his
    # gate, but not `reply`, which drives everything). `reply` is the
    # more load-bearing field here too — an unrecognized value (a typo, a
    # future "deferred", a capitalization mismatch) means a sender who
    # believes they declared reply:required or reply:none is silently
    # getting the default gate instead, with no line to grep. Logged, not
    # fixed differently — fail-open is still correct, a malformed field
    # is a reason to fall through to the normal gate, never to drop the
    # message. Only the silence was the bug.
    reply = data.get("reply")
    if reply not in VALID_REPLY:
        log.warning(
            f"handoff envelope: unrecognized reply={reply!r} — falling "
            f"through as if no envelope present (reply is load-bearing; "
            f"see kind check below)"
        )
        return None

    kind = data.get("kind")
    if kind not in VALID_KINDS:
        log.warning(
            f"handoff envelope: unrecognized kind={kind!r} — falling "
            f"through as if no envelope present"
        )
        return None

    try:
        v = int(data.get("v", 0))
    except (TypeError, ValueError):
        return None

    evidence = data.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []

    supersedes = None
    raw_supersedes = data.get("supersedes")
    if isinstance(raw_supersedes, dict):
        subj = raw_supersedes.get("subject")
        if isinstance(subj, str) and subj:
            msg_id = raw_supersedes.get("msg_id")
            supersedes = Supersedes(
                subject=subj,
                msg_id=msg_id if isinstance(msg_id, str) else None,
            )
    # A malformed supersedes degrades to None rather than invalidating the
    # whole envelope — only `reply` and `kind` are load-bearing for the gate.

    subject = data.get("subject", "")
    if not isinstance(subject, str):
        subject = ""

    # All three below are optional and additive — an invalid or absent
    # value degrades to None (not stated), never a parse failure. Only
    # `reply` and `kind` are load-bearing enough to fail the envelope open.
    confidence = data.get("confidence")
    if confidence not in VALID_CONFIDENCE:
        confidence = None

    stale_after = data.get("stale_after")
    if not isinstance(stale_after, str) or not stale_after:
        stale_after = None

    env_id = data.get("id")
    if not isinstance(env_id, str) or not env_id:
        env_id = None

    return Envelope(
        v=v, kind=kind, reply=reply, subject=subject,
        evidence=evidence, supersedes=supersedes,
        confidence=confidence, stale_after=stale_after, id=env_id,
        raw=data,
    )


# -- selftest ---------------------------------------------------------------
def _selftest() -> int:
    fails = 0

    def check(label, got, want):
        nonlocal fails
        ok = got == want
        fails += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"  (got {got!r})"))

    print("── handoff selftest ──")

    msg = 'text before\n```handoff\n{"v":0,"kind":"finding","reply":"none","subject":"x"}\n```\nafter'
    e = parse_handoff(msg)
    check("parses a valid envelope", e is not None, True)
    check("reply extracted", e.reply if e else None, "none")

    check("no fence returns None", parse_handoff("just plain prose"), None)
    check("empty content returns None", parse_handoff(""), None)

    check("bad json fails open", parse_handoff("```handoff\n{not json}\n```"), None)
    check("missing reply fails open",
          parse_handoff('```handoff\n{"v":0,"kind":"finding"}\n```'), None)
    check("invalid reply value fails open",
          parse_handoff('```handoff\n{"v":0,"kind":"finding","reply":"maybe"}\n```'), None)
    check("missing kind fails open",
          parse_handoff('```handoff\n{"v":0,"reply":"none"}\n```'), None)
    check("unknown kind fails open (typo, not a new type)",
          parse_handoff('```handoff\n{"v":0,"kind":"observation","reply":"none"}\n```'), None)
    check("non-object json fails open", parse_handoff("```handoff\n[1,2,3]\n```"), None)

    # -- 2026-08-09: an unrecognized reply/kind value must be LOGGED, not
    # just silently degraded — Amos found the equivalent bug on his side
    # (kind drift was logged, reply drift wasn't, and reply is the field
    # that actually gates). Behaviour is unchanged (still fails open);
    # only visibility is new.
    import logging as _logging

    class _CapturingHandler(_logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record.getMessage())

    _cap = _CapturingHandler()
    log.addHandler(_cap)
    log.setLevel(_logging.WARNING)
    try:
        parse_handoff('```handoff\n{"v":0,"kind":"finding","reply":"deferred"}\n```')
        check("unrecognized reply value logs a drift warning",
              any("reply" in r and "deferred" in r for r in _cap.records), True)

        _cap.records.clear()
        parse_handoff('```handoff\n{"v":0,"kind":"telemetry","reply":"none"}\n```')
        check("unrecognized kind value logs a drift warning",
              any("kind" in r and "telemetry" in r for r in _cap.records), True)

        _cap.records.clear()
        parse_handoff("just plain prose")
        check("no envelope present logs nothing (only known-but-invalid drifts)",
              len(_cap.records), 0)
    finally:
        log.removeHandler(_cap)

    for k in ("finding", "question", "answer", "handoff", "correction", "status"):
        check(f"kind={k} accepted",
              parse_handoff(f'```handoff\n{{"v":0,"kind":"{k}","reply":"none"}}\n```') is not None,
              True)

    e2 = parse_handoff(
        '```handoff\n{"v":0,"kind":"finding","reply":"required",'
        '"evidence":[{"src":"a","note":"b"}],'
        '"supersedes":{"subject":"double-delivery-cause","msg_id":"msg-1"}}\n```'
    )
    check("evidence parsed", e2.evidence if e2 else None, [{"src": "a", "note": "b"}])
    check("supersedes.subject parsed",
          e2.supersedes.subject if e2 and e2.supersedes else None, "double-delivery-cause")
    check("supersedes.msg_id parsed",
          e2.supersedes.msg_id if e2 and e2.supersedes else None, "msg-1")

    e2b = parse_handoff(
        '```handoff\n{"v":0,"kind":"correction","reply":"none",'
        '"supersedes":{"subject":"double-delivery-cause"}}\n```'
    )
    check("supersedes without msg_id is valid",
          e2b.supersedes.subject if e2b and e2b.supersedes else None, "double-delivery-cause")
    check("supersedes.msg_id defaults to None",
          e2b.supersedes.msg_id if e2b and e2b.supersedes else "MISSING", None)

    e2c = parse_handoff(
        '```handoff\n{"v":0,"kind":"finding","reply":"none","supersedes":"just-a-string"}\n```'
    )
    check("malformed supersedes degrades to None, not a parse failure",
          (e2c is not None, e2c.supersedes if e2c else "MISSING"), (True, None))

    e3 = parse_handoff('```handoff\n{"kind":"finding","reply":"optional"}\n```')
    check("v defaults to 0 when absent", e3.v if e3 else None, 0)

    # -- 2026-08-09 additive fields: confidence, stale_after, id --
    e4 = parse_handoff(
        '```handoff\n{"v":0,"kind":"finding","reply":"optional",'
        '"confidence":"inferred","stale_after":"2026-08-10T00:00:00Z",'
        '"id":"marvin-2026-08-09-1"}\n```'
    )
    check("confidence parsed", e4.confidence if e4 else None, "inferred")
    check("stale_after parsed", e4.stale_after if e4 else None, "2026-08-10T00:00:00Z")
    check("id parsed", e4.id if e4 else None, "marvin-2026-08-09-1")

    for c in ("observed", "inferred", "reported"):
        check(f"confidence={c} accepted",
              parse_handoff(f'```handoff\n{{"v":0,"kind":"finding","reply":"none","confidence":"{c}"}}\n```').confidence,
              c)

    check("invalid confidence degrades to None, not a parse failure",
          parse_handoff('```handoff\n{"v":0,"kind":"finding","reply":"none","confidence":"maybe"}\n```').confidence,
          None)
    check("missing confidence/stale_after/id default to None",
          (e3.confidence, e3.stale_after, e3.id), (None, None, None))
    check("non-string id degrades to None",
          parse_handoff('```handoff\n{"v":0,"kind":"finding","reply":"none","id":5}\n```').id,
          None)

    print("PASS  fails open on every malformed case" if not fails else f"FAIL  {fails} case(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
