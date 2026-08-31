#!/usr/bin/env python3
"""
Memory Dedup — Track 1b (weekly) of the curated memory layer.

See docs/design/curated-memory-layer.md (phasing: dedup is Phase 3,
built once there's enough real data in `facts` to need it — filing
this now alongside Track 2's patterns job since Ian asked to keep
going through the whole memory project where possible).

Track 1's nightly consolidation (memory-maintenance.py) can extract the
same or near-same fact more than once across different episodes — the
same term defined in three separate conversations produces three
similar-but-not-identical rows. This job finds near-duplicate `facts`
rows via embedding similarity and merges them, so the facts table (and
a human skimming data/memory-candidates/) doesn't quietly accumulate
five wordings of one definition.

Reuses the same embedding model memory-maintenance.py already loads for
episodes (fastembed, BAAI/bge-small-en-v1.5) rather than adding a
second embedding dependency. Gracefully no-ops (healthy exit, zero
merges) if fastembed isn't installed — same pattern
generate_embeddings() in memory-maintenance.py already uses.

Merge policy, scoped per domain (never merge across domains — "Crab
Cavern" the Discord server and an unrelated "Crab Cavern" restaurant,
if that ever happened, shouldn't collide just because the words match):
cluster facts whose embeddings have cosine similarity >= MERGE_THRESHOLD.
Keep the fact with higher confidence (tie-break: earlier created_at, so
the original survives over any later re-derivation). Delete the rest,
but fold each merged-away fact's citation into the survivor's content
first — a merge must not destroy evidence, only redundancy.
"""

import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE_ROOT", "/workspace"))
MEMORY_DIR = WORKSPACE / "data" / "memory"
MEMORY_DB = MEMORY_DIR / "memory.db"
HEALTH_FILE = WORKSPACE / "data" / "health" / "memory-dedup.json"
CANDIDATES_DIR = WORKSPACE / "data" / "memory-candidates"

MERGE_THRESHOLD = float(os.environ.get("DEDUP_MERGE_THRESHOLD", "0.92"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] memory-dedup: %(message)s",
)
log = logging.getLogger(__name__)


def init_db() -> sqlite3.Connection:
    """Connect to the shared memory.db. See memory-patterns.py's
    init_db() docstring for why this duplicates a small CREATE TABLE
    block instead of importing memory-maintenance.py directly (bare,
    non-package bin/ imports + hyphenated filenames)."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MEMORY_DB))
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            content TEXT NOT NULL,
            confidence REAL DEFAULT 0.8,
            domain TEXT DEFAULT 'general',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP
        );
    """)

    cols = {row["name"] for row in conn.execute("PRAGMA table_info(facts)").fetchall()}
    if "embedding" not in cols:
        conn.execute("ALTER TABLE facts ADD COLUMN embedding BLOB")

    conn.commit()
    return conn


def cosine_similarity(a, b) -> float:
    """Plain-Python cosine similarity — no numpy dependency for the
    part that's actually easy to unit test without a real model."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def cluster_by_similarity(items: list, threshold: float = MERGE_THRESHOLD) -> list:
    """items: list of dicts with at least "id" and "embedding" (a
    sequence of floats). Returns a list of clusters, each a list of
    item dicts, using simple greedy single-link clustering: an item
    joins the first cluster where it's similar enough to *any* member.

    Deliberately not a full hierarchical/optimal clustering — greedy
    single-link is enough for "same fact reworded a few times" and easy
    to reason about; a pathological case (a long chain of pairwise-similar
    but end-to-end-dissimilar facts merging transitively) is an accepted
    tradeoff at this data scale, not something worth a real clustering
    library for.
    """
    clusters: list = []
    for item in items:
        placed = False
        for cluster in clusters:
            if any(cosine_similarity(item["embedding"], member["embedding"]) >= threshold for member in cluster):
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])
    return clusters


def _pick_survivor(cluster: list) -> dict:
    """Higher confidence wins; ties broken by earlier created_at (the
    original survives over a later re-derivation of the same fact)."""
    return sorted(cluster, key=lambda r: (-r["confidence"], r["created_at"]))[0]


def merge_cluster(conn: sqlite3.Connection, cluster: list) -> dict | None:
    """Merge a cluster of >=2 duplicate facts into one surviving row.
    Returns a summary dict, or None if the cluster has only one member
    (nothing to merge)."""
    if len(cluster) < 2:
        return None

    survivor = _pick_survivor(cluster)
    losers = [r for r in cluster if r["id"] != survivor["id"]]

    # Fold every loser's own citation trail into the survivor's content
    # instead of discarding it — a merge reduces redundancy, not evidence.
    content = survivor["content"]
    for loser in losers:
        if loser["content"] not in content:
            content = f"{content} | {loser['content']}"

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE facts SET content = ?, updated_at = ? WHERE id = ?",
        (content, now, survivor["id"]),
    )
    loser_ids = [r["id"] for r in losers]
    conn.executemany("DELETE FROM facts WHERE id = ?", [(i,) for i in loser_ids])
    conn.commit()

    return {"survivor_id": survivor["id"], "merged_ids": loser_ids, "subject": survivor["subject"]}


def generate_embeddings_for_facts(conn: sqlite3.Connection) -> int:
    """Same pattern as memory-maintenance.py's generate_embeddings():
    optional dependency, graceful no-op if missing."""
    try:
        from fastembed import TextEmbedding
    except ImportError:
        log.warning("fastembed not installed — skipping fact embedding generation")
        return 0

    rows = conn.execute("SELECT id, content FROM facts WHERE embedding IS NULL LIMIT 200").fetchall()
    if not rows:
        return 0

    model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    texts = [row["content"] for row in rows]
    embeddings = list(model.embed(texts))

    import numpy as np
    for row, emb in zip(rows, embeddings):
        emb_bytes = np.array(emb, dtype=np.float32).tobytes()
        conn.execute("UPDATE facts SET embedding = ? WHERE id = ?", (emb_bytes, row["id"]))
    conn.commit()
    return len(rows)


def _load_embedded_facts_by_domain(conn: sqlite3.Connection) -> dict:
    import numpy as np
    rows = conn.execute(
        "SELECT id, subject, content, confidence, domain, created_at, embedding "
        "FROM facts WHERE embedding IS NOT NULL"
    ).fetchall()

    by_domain: dict = {}
    for row in rows:
        item = dict(row)
        item["embedding"] = np.frombuffer(row["embedding"], dtype=np.float32).tolist()
        by_domain.setdefault(row["domain"], []).append(item)
    return by_domain


def write_merge_log(merges: list) -> Path | None:
    if not merges:
        return None
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CANDIDATES_DIR / f"dedup-{date_str}.md"
    lines = [f"# Fact dedup merges — {date_str}", ""]
    for m in merges:
        lines.append(f"- **{m['subject']}**: kept fact {m['survivor_id']}, merged {m['merged_ids']}")
    lines.append("")
    out_path.write_text("\n".join(lines))
    return out_path


def write_health(success: bool, stats: dict) -> None:
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "healthy" if success else "error",
        "stats": stats,
    }))


def run(conn: sqlite3.Connection) -> dict:
    embedded = generate_embeddings_for_facts(conn)
    by_domain = _load_embedded_facts_by_domain(conn)

    merges = []
    for domain, items in by_domain.items():
        for cluster in cluster_by_similarity(items):
            merged = merge_cluster(conn, cluster)
            if merged:
                merges.append(merged)

    merge_log = write_merge_log(merges)
    return {
        "embedded": embedded,
        "clusters_merged": len(merges),
        "facts_removed": sum(len(m["merged_ids"]) for m in merges),
        "merge_log": str(merge_log) if merge_log else None,
    }


def main():
    log.info("Memory dedup job starting")
    start = time.time()
    try:
        conn = init_db()
        stats = run(conn)
        conn.close()
        stats["duration_s"] = round(time.time() - start, 2)
        log.info(f"Dedup job complete: {json.dumps(stats)}")
        write_health(True, stats)
    except Exception as e:
        log.error(f"Dedup job failed: {e}")
        write_health(False, {"error": str(e)})
        sys.exit(1)


if __name__ == "__main__":
    main()
