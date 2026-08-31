#!/usr/bin/env python3
"""
Memory Reflection — Track 2b (monthly-gated) of the curated memory layer.

See docs/design/curated-memory-layer.md, phasing item 4. Dispatched
weekly by the scheduler (same pattern Amos uses on his side per the
2026-08-31 #agent-chat exchange with Zero: weekly cron dispatch,
monthly-gated execution via a DOW==7 && DOM<=07 guard — any other
Sunday it logs "skipped" and exits 0 without touching anything). Ported
here as monthly_gate_is_open().

**Deliberately conservative for v1.** This is the only job in the
curated-memory pipeline that would ever touch `MEMORY.md`/`voice.md` —
the exact files with a standing, repeatedly-documented history of
drifting wrong under automated/high-load conditions (see
facts/voice-flattened-immediately-after-recalibration-2026-08-28.md and
its siblings). Amos's ratified design for this job is "revert button,
not approval gate" — async edit-then-revert beats a synchronous human
gate for pipeline velocity, and that's the right call in general. But
"designed" and "load-bearing enough to auto-mutate my own personality
file with no live-tested track record" are different bars, so v1 stops
one step short: it finds patterns that already cleared Track 2's
evidence gate (status='established' — 3+ reinforcements, or an
explicit strong human signal reinforced further), and turns each into
a *proposed* fact-file draft for human review, rather than writing
voice.md/MEMORY.md directly. Auto-apply is a real follow-up, not a
missing feature snuck in as a TODO — see PROPOSE_ONLY below.

Only ever consumes patterns.status == 'established' rows — it does not
re-derive judgments about persona from a month of raw episodes itself.
Track 2 already spent the evidence-gating budget; reflection's job is
narrower: decide whether an established pattern is worth turning into
a durable, reviewable artifact, not whether the pattern is real.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
MEMORY_DIR = WORKSPACE / "data" / "memory"
MEMORY_DB = MEMORY_DIR / "memory.db"
HEALTH_FILE = WORKSPACE / "data" / "health" / "memory-reflection.json"
CANDIDATES_DIR = WORKSPACE / "data" / "memory-candidates"

# When this flips to true (a later, explicit change — not an env var
# someone can silently set), reflection would additionally write a
# proposed patch straight into the target file's own review workflow.
# Not built yet. See module docstring.
PROPOSE_ONLY = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] memory-reflection: %(message)s",
)
log = logging.getLogger(__name__)

import sqlite3


def init_db() -> sqlite3.Connection:
    """Connect to the shared memory.db. See memory-patterns.py's
    init_db() docstring for why this duplicates a small schema block
    instead of importing memory-maintenance.py directly."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MEMORY_DB))
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            pattern_type TEXT NOT NULL,
            content TEXT NOT NULL,
            confidence REAL DEFAULT 0.7,
            reinforcement_count INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        );
    """)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(patterns)").fetchall()}
    if "status" not in cols:
        conn.execute("ALTER TABLE patterns ADD COLUMN status TEXT DEFAULT 'pending'")
    if "reflected_at" not in cols:
        conn.execute("ALTER TABLE patterns ADD COLUMN reflected_at TIMESTAMP DEFAULT NULL")

    conn.commit()
    return conn


def monthly_gate_is_open(now: datetime | None = None) -> bool:
    """Weekly dispatch, monthly-gated execution: only the first Sunday
    of the month opens the gate. Direct port of Amos's
    `DOW==7 && DOM<=07` guard (bin/invoke-mnemosyne.sh:71-83) —
    isoweekday() 7 == Sunday, day <= 7 == first occurrence of that
    weekday in the month."""
    now = now or datetime.now(timezone.utc)
    return now.isoweekday() == 7 and now.day <= 7


def load_established_unreflected_patterns(conn: sqlite3.Connection, agent: str = "Marvin") -> list:
    return conn.execute(
        "SELECT * FROM patterns WHERE agent = ? AND status = 'established' AND reflected_at IS NULL",
        (agent,),
    ).fetchall()


def propose_fact_draft(pattern_row) -> dict:
    """Turn an established pattern into a reviewable fact-file draft —
    the same additive, low-risk shape a human already writes by hand
    into agents/Marvin/memory/facts/. No LLM call needed here: Track 2
    already did the judgment work, this just reformats it."""
    return {
        "pattern_id": pattern_row["id"],
        "pattern_type": pattern_row["pattern_type"],
        "reinforcement_count": pattern_row["reinforcement_count"],
        "draft_content": pattern_row["content"],
    }


def mark_reflected(conn: sqlite3.Connection, pattern_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE patterns SET reflected_at = ? WHERE id = ?", (now, pattern_id))
    conn.commit()


def write_reflection_file(proposals: list) -> Path | None:
    if not proposals:
        return None
    month_str = datetime.now(timezone.utc).strftime("%Y-%m")
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CANDIDATES_DIR / f"reflection-{month_str}.md"

    lines = [
        f"# Reflection proposals — {month_str}",
        "",
        "Track 2b monthly reflection (see docs/design/curated-memory-layer.md). "
        "**Proposals only — nothing here has been applied.** Each entry is an "
        "established pattern (3+ reinforcements) turned into a fact-file draft "
        "for human review before it becomes an actual MEMORY.md/facts/ entry.",
        "",
    ]
    for p in proposals:
        lines.append(
            f"## pattern {p['pattern_id']} ({p['pattern_type']}, "
            f"reinforced {p['reinforcement_count']}x)\n\n{p['draft_content']}\n"
        )
    out_path.write_text("\n".join(lines))
    return out_path


def write_health(success: bool, stats: dict) -> None:
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "healthy" if success else "error",
        "stats": stats,
    }))


def run(conn: sqlite3.Connection, agent: str = "Marvin", now: datetime | None = None) -> dict:
    if not monthly_gate_is_open(now):
        log.info("Reflection skipped — not the first Sunday of the month")
        return {"gate_open": False, "patterns_reflected": 0, "proposals_file": None}

    established = load_established_unreflected_patterns(conn, agent=agent)
    proposals = [propose_fact_draft(row) for row in established]

    for row in established:
        mark_reflected(conn, row["id"])

    proposals_file = write_reflection_file(proposals)
    return {
        "gate_open": True,
        "patterns_reflected": len(proposals),
        "proposals_file": str(proposals_file) if proposals_file else None,
        "propose_only": PROPOSE_ONLY,
    }


def main():
    log.info("Memory reflection job starting")
    start = time.time()
    try:
        conn = init_db()
        stats = run(conn)
        conn.close()
        stats["duration_s"] = round(time.time() - start, 2)
        log.info(f"Reflection job complete: {json.dumps(stats)}")
        write_health(True, stats)
    except Exception as e:
        log.error(f"Reflection job failed: {e}")
        write_health(False, {"error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
