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

Schema (v0, finalised 2026-08-05):

    ```handoff
    {"v": 0, "kind": "finding", "reply": "optional", "subject": "...",
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
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

_FENCE_RE = re.compile(r"```handoff\s*\n(.*?)\n```", re.DOTALL)

VALID_REPLY = {"required", "optional", "none"}
VALID_KINDS = {"finding", "question", "answer", "handoff", "correction", "status"}


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

    reply = data.get("reply")
    if reply not in VALID_REPLY:
        return None

    kind = data.get("kind")
    if kind not in VALID_KINDS:
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

    return Envelope(
        v=v, kind=kind, reply=reply, subject=subject,
        evidence=evidence, supersedes=supersedes, raw=data,
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

    print("PASS  fails open on every malformed case" if not fails else f"FAIL  {fails} case(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
