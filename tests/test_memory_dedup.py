"""
Tests for bin/memory-dedup.py — Track 1b (weekly) of the curated memory
layer: merges near-duplicate `facts` rows found via embedding
similarity. See docs/design/curated-memory-layer.md.

Embedding *generation* (fastembed/BAAI-bge-small) is intentionally not
exercised here — same reasoning memory-maintenance.py's own
generate_embeddings() already goes untested by: it's a real model load,
slow and non-deterministic to pin in CI. What's tested is everything
downstream of an embedding existing: cosine similarity, clustering,
survivor selection, and the actual DB merge (citation folding + row
deletion) — using small synthetic vectors instead of real ones.
"""

from datetime import datetime, timedelta, timezone

import pytest

from conftest import import_script, PACKAGE_ROOT


@pytest.fixture
def md(monkeypatch, tmp_workspace):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
    return import_script("memory-dedup", file_path=PACKAGE_ROOT / "bin" / "memory-dedup.py")


@pytest.fixture
def conn(md):
    c = md.init_db()
    yield c
    c.close()


def _insert_fact(conn, subject="Crab Cavern", content="def", confidence=0.7, domain="glossary", days_ago=0):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    cursor = conn.execute(
        "INSERT INTO facts (subject, content, confidence, domain, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (subject, content, confidence, domain, ts, ts),
    )
    conn.commit()
    return cursor.lastrowid


class TestInitDbMigration:
    def test_embedding_column_added_to_existing_facts_table(self, md, tmp_workspace):
        import sqlite3
        (tmp_workspace / "data" / "memory").mkdir(parents=True, exist_ok=True)
        db_path = tmp_workspace / "data" / "memory" / "memory.db"
        pre = sqlite3.connect(str(db_path))
        pre.execute("""
            CREATE TABLE facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence REAL DEFAULT 0.8,
                domain TEXT DEFAULT 'general',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP
            )
        """)
        pre.commit()
        pre.close()

        conn = md.init_db()
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(facts)").fetchall()}
        assert "embedding" in cols
        conn.close()

    def test_running_init_db_twice_is_safe(self, md):
        md.init_db().close()
        conn = md.init_db()
        assert conn is not None
        conn.close()


class TestCosineSimilarity:
    def test_identical_vectors_are_similarity_one(self, md):
        assert md.cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_are_similarity_zero(self, md):
        assert md.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_are_similarity_negative_one(self, md):
        assert md.cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_mismatched_length_is_zero_not_a_crash(self, md):
        assert md.cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_zero_vector_is_zero_not_a_divide_by_zero_crash(self, md):
        assert md.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestClusterBySimilarity:
    def test_similar_items_cluster_together(self, md):
        items = [
            {"id": 1, "embedding": [1.0, 0.0]},
            {"id": 2, "embedding": [0.99, 0.01]},
        ]
        clusters = md.cluster_by_similarity(items, threshold=0.9)
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_dissimilar_items_stay_separate(self, md):
        items = [
            {"id": 1, "embedding": [1.0, 0.0]},
            {"id": 2, "embedding": [0.0, 1.0]},
        ]
        clusters = md.cluster_by_similarity(items, threshold=0.9)
        assert len(clusters) == 2

    def test_empty_input_yields_no_clusters(self, md):
        assert md.cluster_by_similarity([]) == []

    def test_single_item_is_its_own_cluster(self, md):
        clusters = md.cluster_by_similarity([{"id": 1, "embedding": [1.0, 0.0]}])
        assert clusters == [[{"id": 1, "embedding": [1.0, 0.0]}]]


class TestMergeCluster:
    def test_single_member_cluster_is_not_merged(self, md, conn):
        fid = _insert_fact(conn)
        row = dict(conn.execute("SELECT * FROM facts WHERE id = ?", (fid,)).fetchone())
        assert md.merge_cluster(conn, [row]) is None

    def test_higher_confidence_survives(self, md, conn):
        low = _insert_fact(conn, subject="X", content="low-confidence wording", confidence=0.5)
        high = _insert_fact(conn, subject="X", content="high-confidence wording", confidence=0.9)
        rows = [dict(r) for r in conn.execute("SELECT * FROM facts WHERE id IN (?, ?)", (low, high))]

        result = md.merge_cluster(conn, rows)

        assert result["survivor_id"] == high
        assert result["merged_ids"] == [low]
        remaining = conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
        assert remaining == 1

    def test_tie_confidence_keeps_earlier_created_at(self, md, conn):
        older = _insert_fact(conn, subject="X", content="original wording", confidence=0.7, days_ago=5)
        newer = _insert_fact(conn, subject="X", content="re-derived wording", confidence=0.7, days_ago=0)
        rows = [dict(r) for r in conn.execute("SELECT * FROM facts WHERE id IN (?, ?)", (older, newer))]

        result = md.merge_cluster(conn, rows)

        assert result["survivor_id"] == older

    def test_merged_content_folds_loser_text_in_not_just_drops_it(self, md, conn):
        keep = _insert_fact(conn, subject="X", content="Crab Cavern is the second Discord server.", confidence=0.9)
        lose = _insert_fact(conn, subject="X", content="Crab Cavern hosts #agent-chat.", confidence=0.5)
        rows = [dict(r) for r in conn.execute("SELECT * FROM facts WHERE id IN (?, ?)", (keep, lose))]

        md.merge_cluster(conn, rows)

        survivor_content = conn.execute("SELECT content FROM facts WHERE id = ?", (keep,)).fetchone()["content"]
        assert "second Discord server" in survivor_content
        assert "#agent-chat" in survivor_content, "merging must not discard the loser's distinct content"


class TestRunIntegration:
    def test_run_merges_near_duplicates_within_a_domain(self, md, conn, monkeypatch):
        import numpy as np

        # Skip real embedding generation; stamp two near-identical facts
        # with hand-crafted near-identical embeddings directly.
        monkeypatch.setattr(md, "generate_embeddings_for_facts", lambda c: 0)

        a = _insert_fact(conn, subject="Crab Cavern", content="def A", confidence=0.9, domain="glossary")
        b = _insert_fact(conn, subject="Crab Cavern", content="def B", confidence=0.5, domain="glossary")
        vec_a = np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes()
        vec_b = np.array([0.99, 0.01, 0.0], dtype=np.float32).tobytes()
        conn.execute("UPDATE facts SET embedding = ? WHERE id = ?", (vec_a, a))
        conn.execute("UPDATE facts SET embedding = ? WHERE id = ?", (vec_b, b))
        conn.commit()

        stats = md.run(conn)

        assert stats["clusters_merged"] == 1
        assert stats["facts_removed"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"] == 1

    def test_run_does_not_merge_across_domains(self, md, conn, monkeypatch):
        import numpy as np
        monkeypatch.setattr(md, "generate_embeddings_for_facts", lambda c: 0)

        a = _insert_fact(conn, subject="X", content="def A", confidence=0.9, domain="glossary")
        b = _insert_fact(conn, subject="X", content="def B", confidence=0.5, domain="other")
        vec = np.array([1.0, 0.0, 0.0], dtype=np.float32).tobytes()
        conn.execute("UPDATE facts SET embedding = ? WHERE id = ?", (vec, a))
        conn.execute("UPDATE facts SET embedding = ? WHERE id = ?", (vec, b))
        conn.commit()

        stats = md.run(conn)

        assert stats["clusters_merged"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"] == 2

    def test_run_with_no_embedded_facts_is_a_clean_noop(self, md, conn, monkeypatch):
        monkeypatch.setattr(md, "generate_embeddings_for_facts", lambda c: 0)
        stats = md.run(conn)
        assert stats["clusters_merged"] == 0
        assert stats["facts_removed"] == 0
        assert stats["merge_log"] is None
