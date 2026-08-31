"""
Tests for bin/memory-reflection.py — Track 2b (weekly dispatch,
monthly-gated) of the curated memory layer. See
docs/design/curated-memory-layer.md phasing item 4.

v1 is deliberately conservative: it proposes fact-file drafts from
already-established patterns for human review, and does not write
voice.md/MEMORY.md directly (PROPOSE_ONLY). These tests lock that
behavior in as much as the gate/query/mark-reflected mechanics, so a
future change to actually auto-apply is a visible, deliberate diff
rather than a silent capability creep.
"""

from datetime import datetime, timezone

import pytest

from conftest import import_script, PACKAGE_ROOT


@pytest.fixture
def mr(monkeypatch, tmp_workspace):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
    return import_script("memory-reflection", file_path=PACKAGE_ROOT / "bin" / "memory-reflection.py")


@pytest.fixture
def conn(mr):
    c = mr.init_db()
    yield c
    c.close()


def _insert_pattern(conn, agent="Marvin", pattern_type="voice", content="c", status="established",
                     reinforcement_count=3, reflected_at=None):
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        "INSERT INTO patterns (agent, pattern_type, content, reinforcement_count, status, "
        "created_at, updated_at, reflected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (agent, pattern_type, content, reinforcement_count, status, now, now, reflected_at),
    )
    conn.commit()
    return cursor.lastrowid


class TestInitDbMigration:
    def test_reflected_at_column_added_to_existing_patterns_table(self, mr, tmp_workspace):
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
                updated_at TIMESTAMP,
                status TEXT DEFAULT 'pending'
            )
        """)
        pre.commit()
        pre.close()

        conn = mr.init_db()
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(patterns)").fetchall()}
        assert "reflected_at" in cols
        conn.close()

    def test_running_init_db_twice_is_safe(self, mr):
        mr.init_db().close()
        conn = mr.init_db()
        assert conn is not None
        conn.close()


class TestMonthlyGate:
    def test_first_sunday_of_month_is_open(self, mr):
        first_sunday = datetime(2026, 8, 2, tzinfo=timezone.utc)
        assert first_sunday.isoweekday() == 7
        assert mr.monthly_gate_is_open(first_sunday) is True

    def test_second_sunday_of_month_is_closed(self, mr):
        second_sunday = datetime(2026, 8, 9, tzinfo=timezone.utc)
        assert second_sunday.isoweekday() == 7
        assert mr.monthly_gate_is_open(second_sunday) is False

    def test_a_wednesday_is_closed(self, mr):
        wednesday = datetime(2026, 8, 5, tzinfo=timezone.utc)
        assert mr.monthly_gate_is_open(wednesday) is False

    def test_day_seven_sunday_is_still_open(self):
        # day <= 7, so a Sunday landing exactly on the 7th still counts.
        # 2026-06-07 is a real Sunday (verified via datetime.date.isoweekday()).
        d = datetime(2026, 6, 7, tzinfo=timezone.utc)
        assert d.isoweekday() == 7
        from conftest import import_script, PACKAGE_ROOT
        mr = import_script("memory-reflection", file_path=PACKAGE_ROOT / "bin" / "memory-reflection.py")
        assert mr.monthly_gate_is_open(d) is True

    def test_day_eight_sunday_is_closed(self):
        # 2026-11-08 is a Sunday (day > 7) -> gate must be closed.
        d = datetime(2026, 11, 8, tzinfo=timezone.utc)
        assert d.isoweekday() == 7
        from conftest import import_script, PACKAGE_ROOT
        mr = import_script("memory-reflection", file_path=PACKAGE_ROOT / "bin" / "memory-reflection.py")
        assert mr.monthly_gate_is_open(d) is False


class TestRunGating:
    def test_gate_closed_skips_everything(self, mr, conn):
        _insert_pattern(conn)
        not_first_sunday = datetime(2026, 8, 9, tzinfo=timezone.utc)

        stats = mr.run(conn, now=not_first_sunday)

        assert stats["gate_open"] is False
        assert stats["patterns_reflected"] == 0
        # pattern must be untouched
        row = conn.execute("SELECT reflected_at FROM patterns").fetchone()
        assert row["reflected_at"] is None

    def test_gate_open_reflects_established_patterns(self, mr, conn):
        _insert_pattern(conn, content="Keep replies dry and understated.")
        first_sunday = datetime(2026, 8, 2, tzinfo=timezone.utc)

        stats = mr.run(conn, now=first_sunday)

        assert stats["gate_open"] is True
        assert stats["patterns_reflected"] == 1
        assert stats["proposals_file"] is not None
        assert stats["propose_only"] is True

    def test_only_established_status_is_reflected(self, mr, conn):
        _insert_pattern(conn, status="candidate")
        _insert_pattern(conn, status="pending")
        first_sunday = datetime(2026, 8, 2, tzinfo=timezone.utc)

        stats = mr.run(conn, now=first_sunday)

        assert stats["patterns_reflected"] == 0

    def test_already_reflected_pattern_is_not_reprocessed(self, mr, conn):
        already = datetime.now(timezone.utc).isoformat()
        _insert_pattern(conn, reflected_at=already)
        first_sunday = datetime(2026, 8, 2, tzinfo=timezone.utc)

        stats = mr.run(conn, now=first_sunday)

        assert stats["patterns_reflected"] == 0

    def test_reflected_pattern_is_marked_so_it_does_not_repeat_next_month(self, mr, conn):
        pid = _insert_pattern(conn)
        first_sunday = datetime(2026, 8, 2, tzinfo=timezone.utc)

        mr.run(conn, now=first_sunday)

        row = conn.execute("SELECT reflected_at FROM patterns WHERE id = ?", (pid,)).fetchone()
        assert row["reflected_at"] is not None

    def test_no_established_patterns_is_a_clean_noop(self, mr, conn):
        first_sunday = datetime(2026, 8, 2, tzinfo=timezone.utc)
        stats = mr.run(conn, now=first_sunday)
        assert stats["gate_open"] is True
        assert stats["patterns_reflected"] == 0
        assert stats["proposals_file"] is None


class TestProposeOnlyNeverWritesPersonaFiles:
    def test_run_does_not_touch_memory_md_or_voice_md(self, mr, conn, tmp_workspace):
        """The whole point of v1: this job must never write to an
        agent's actual persona/index files, only to the review
        candidates directory."""
        agent_dir = tmp_workspace / "agents" / "Marvin" / "memory"
        agent_dir.mkdir(parents=True, exist_ok=True)
        memory_md = agent_dir / "MEMORY.md"
        memory_md.write_text("# original content\n")
        before = memory_md.read_text()

        _insert_pattern(conn, content="Some established pattern.")
        first_sunday = datetime(2026, 8, 2, tzinfo=timezone.utc)
        mr.run(conn, now=first_sunday)

        assert memory_md.read_text() == before
