"""
Tests for bin/memory-patterns.py — Track 2 (weekly) of the curated
memory layer, see docs/design/curated-memory-layer.md and
task-1788154168's follow-on. Covers evidence gating (pending ->
candidate -> established), the 30-day deprecation sweep, and the
citation-integrity discipline shared with Track 1 (episode ids are
assigned by the script from a real row, never taken from model output).
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import import_script, PACKAGE_ROOT


@pytest.fixture
def mp(monkeypatch, tmp_workspace):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
    return import_script("memory-patterns", file_path=PACKAGE_ROOT / "bin" / "memory-patterns.py")


@pytest.fixture
def conn(mp):
    c = mp.init_db()
    yield c
    c.close()


def _insert_episode(conn, summary="Ian: always cite the real episode id, never guess one.", days_ago=0):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    cursor = conn.execute(
        "INSERT INTO episodes (summary, importance, channel, created_at) VALUES (?, ?, ?, ?)",
        (summary, 6.0, "general", ts),
    )
    conn.commit()
    return cursor.lastrowid


def _fake_run(stdout):
    return lambda *a, **k: type("R", (), {"stdout": stdout, "returncode": 0})()


class TestInitDbMigration:
    def test_status_column_added_to_existing_patterns_table(self, mp, tmp_workspace):
        """The patterns table already existed in production without a
        status column (never populated, but the CREATE TABLE predates
        this job). init_db() must add it idempotently rather than
        assuming a fresh table."""
        import sqlite3
        (tmp_workspace / "data" / "memory").mkdir(parents=True, exist_ok=True)
        db_path = tmp_workspace / "data" / "memory" / "memory.db"
        pre = sqlite3.connect(str(db_path))
        pre.execute("""
            CREATE TABLE patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                pattern_type TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence REAL DEFAULT 0.7,
                reinforcement_count INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP
            )
        """)
        pre.commit()
        pre.close()

        conn = mp.init_db()
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(patterns)").fetchall()}
        assert "status" in cols
        conn.close()

    def test_running_init_db_twice_is_safe(self, mp):
        mp.init_db().close()
        conn = mp.init_db()  # must not raise "duplicate column"
        assert conn is not None
        conn.close()


class TestExtractCandidatePattern:
    def test_none_response_yields_no_candidate(self, mp, monkeypatch):
        monkeypatch.setattr(mp.subprocess, "run", _fake_run("NONE"))
        assert mp.extract_candidate_pattern(1, "just chatting") is None

    def test_valid_response_is_stamped_with_real_episode_id(self, mp, monkeypatch):
        payload = json.dumps({"pattern_type": "citation", "content": "Always cite real ids.", "strength": "strong"})
        monkeypatch.setattr(mp.subprocess, "run", _fake_run(payload))
        candidate = mp.extract_candidate_pattern(77, "Ian: always cite real ids, never guess.")
        assert candidate["episode_id"] == 77
        assert candidate["strength"] == "strong"

    def test_invalid_strength_value_falls_back_to_normal(self, mp, monkeypatch):
        payload = json.dumps({"pattern_type": "x", "content": "y", "strength": "extremely-strong"})
        monkeypatch.setattr(mp.subprocess, "run", _fake_run(payload))
        candidate = mp.extract_candidate_pattern(1, "whatever")
        assert candidate["strength"] == "normal"

    def test_malformed_json_is_not_a_crash(self, mp, monkeypatch):
        monkeypatch.setattr(mp.subprocess, "run", _fake_run("{{{not json"))
        assert mp.extract_candidate_pattern(1, "whatever") is None


class TestEvidenceGating:
    def test_first_normal_signal_is_pending_not_candidate(self, mp, conn):
        candidate = {"agent": "Marvin", "pattern_type": "voice", "content": "keep replies dry", "strength": "normal", "episode_id": 1}
        result = mp.upsert_pattern(conn, candidate)
        assert result["action"] == "inserted"
        assert result["status"] == "pending"

    def test_one_strong_signal_reaches_candidate_immediately(self, mp, conn):
        candidate = {"agent": "Marvin", "pattern_type": "voice", "content": "keep replies dry", "strength": "strong", "episode_id": 1}
        result = mp.upsert_pattern(conn, candidate)
        assert result["status"] == "candidate"

    def test_second_normal_signal_promotes_pending_to_candidate(self, mp, conn):
        c1 = {"agent": "Marvin", "pattern_type": "voice", "content": "keep replies dry and understated", "strength": "normal", "episode_id": 1}
        c2 = {"agent": "Marvin", "pattern_type": "voice", "content": "keep replies dry and understated please", "strength": "normal", "episode_id": 2}
        mp.upsert_pattern(conn, c1)
        result = mp.upsert_pattern(conn, c2)
        assert result["action"] == "reinforced"
        assert result["status"] == "candidate"
        assert result["reinforcement_count"] == 2

    def test_third_reinforcement_reaches_established(self, mp, conn):
        base = {"agent": "Marvin", "pattern_type": "voice", "content": "keep replies dry and understated", "strength": "normal"}
        for ep_id in (1, 2, 3):
            result = mp.upsert_pattern(conn, {**base, "episode_id": ep_id})
        assert result["status"] == "established"
        assert result["reinforcement_count"] == 3

    def test_dissimilar_content_does_not_reinforce_same_type(self, mp, conn):
        c1 = {"agent": "Marvin", "pattern_type": "voice", "content": "keep replies dry and understated", "strength": "normal", "episode_id": 1}
        c2 = {"agent": "Marvin", "pattern_type": "voice", "content": "always double check github tokens before use", "strength": "normal", "episode_id": 2}
        mp.upsert_pattern(conn, c1)
        result = mp.upsert_pattern(conn, c2)
        assert result["action"] == "inserted", "unrelated content under the same pattern_type must not merge"

    def test_different_agents_do_not_share_reinforcement(self, mp, conn):
        c1 = {"agent": "Marvin", "pattern_type": "voice", "content": "keep replies dry and understated", "strength": "normal", "episode_id": 1}
        c2 = {"agent": "Amos", "pattern_type": "voice", "content": "keep replies dry and understated", "strength": "normal", "episode_id": 2}
        mp.upsert_pattern(conn, c1)
        result = mp.upsert_pattern(conn, c2)
        assert result["action"] == "inserted"


class TestDeprecation:
    def test_stale_pattern_is_deprecated(self, mp, conn):
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        conn.execute(
            "INSERT INTO patterns (agent, pattern_type, content, reinforcement_count, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Marvin", "voice", "some old pattern", 2, "candidate", old, old),
        )
        conn.commit()

        deprecated = mp.deprecate_stale_patterns(conn)

        assert deprecated == 1
        row = conn.execute("SELECT status FROM patterns").fetchone()
        assert row["status"] == "deprecated"

    def test_recently_reinforced_pattern_is_not_deprecated(self, mp, conn):
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO patterns (agent, pattern_type, content, reinforcement_count, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Marvin", "voice", "fresh pattern", 3, "established", now, now),
        )
        conn.commit()

        deprecated = mp.deprecate_stale_patterns(conn)

        assert deprecated == 0

    def test_already_deprecated_pattern_is_not_recounted(self, mp, conn):
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        conn.execute(
            "INSERT INTO patterns (agent, pattern_type, content, reinforcement_count, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Marvin", "voice", "already gone", 1, "deprecated", old, old),
        )
        conn.commit()

        assert mp.deprecate_stale_patterns(conn) == 0


class TestRunIntegration:
    def test_run_scans_episodes_and_reports_stats(self, mp, conn, monkeypatch):
        _insert_episode(conn, "Ian: always cite the real episode id, never guess.")
        payload = json.dumps({"pattern_type": "citation", "content": "Cite real ids only.", "strength": "strong"})
        monkeypatch.setattr(mp.subprocess, "run", _fake_run(payload))

        stats = mp.run(conn)

        assert stats["episodes_scanned"] == 1
        assert stats["inserted"] == 1
        assert stats["candidates_file"] is not None

    def test_run_with_no_episodes_is_a_clean_noop(self, mp, conn):
        stats = mp.run(conn)
        assert stats["episodes_scanned"] == 0
        assert stats["patterns_touched"] == 0
        assert stats["candidates_file"] is None
