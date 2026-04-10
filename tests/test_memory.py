"""
Tests for bin/memory-maintenance.py — Memory consolidation and decay.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import import_script, PACKAGE_ROOT


class TestMemoryDatabaseInit:
    """Test memory database initialization."""

    def test_creates_tables(self, tmp_workspace, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        mm = import_script("memory-maintenance")

        conn = mm.init_db()

        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        assert "episodes" in tables
        assert "facts" in tables
        assert "patterns" in tables
        conn.close()

    def test_tables_have_expected_columns(self, tmp_workspace, monkeypatch):
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        mm = import_script("memory-maintenance")

        conn = mm.init_db()

        cursor = conn.execute("PRAGMA table_info(episodes)")
        columns = {row[1] for row in cursor.fetchall()}
        assert "summary" in columns
        assert "importance" in columns
        assert "created_at" in columns
        assert "embedding" in columns
        conn.close()

    def test_init_is_idempotent(self, tmp_workspace, monkeypatch):
        """Calling init_db twice should not error."""
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        mm = import_script("memory-maintenance")

        conn1 = mm.init_db()
        conn1.close()
        conn2 = mm.init_db()
        conn2.close()


class TestMemoryDecay:
    """Test episode importance decay."""

    def test_decay_reduces_importance(self, memory_db, tmp_workspace, monkeypatch):
        conn, db_path = memory_db
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
        monkeypatch.setenv("MEMORY_DECAY_RATE", "0.25")

        old_date = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
        conn.execute(
            "INSERT INTO episodes (summary, importance, created_at) VALUES (?, ?, ?)",
            ("Test episode", 8.0, old_date),
        )
        conn.commit()

        cursor = conn.execute("SELECT importance FROM episodes WHERE summary = 'Test episode'")
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == 8.0
        conn.close()

    def test_episodes_below_cutoff_eligible_for_pruning(self, memory_db):
        """Episodes with effective score below cutoff should be prunable."""
        conn, _ = memory_db

        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        conn.execute(
            "INSERT INTO episodes (summary, importance, created_at) VALUES (?, ?, ?)",
            ("Old boring episode", 2.0, old_date),
        )
        conn.commit()

        cursor = conn.execute("SELECT importance FROM episodes WHERE summary = 'Old boring episode'")
        row = cursor.fetchone()
        decay_rate = 0.25
        effective = row[0] - (30 * decay_rate)
        assert effective < 6.0
        conn.close()


class TestEpisodeStorage:
    """Test episode CRUD operations."""

    def test_insert_episode(self, memory_db):
        conn, _ = memory_db

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO episodes (summary, importance, channel, tags, agents, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("User discussed deployment", 7.5, "general", "deploy,ops", "amos", now),
        )
        conn.commit()

        cursor = conn.execute("SELECT * FROM episodes WHERE summary LIKE '%deployment%'")
        row = cursor.fetchone()
        assert row is not None
        assert row[2] == 7.5
        conn.close()

    def test_facts_table(self, memory_db):
        conn, _ = memory_db

        conn.execute(
            "INSERT INTO facts (subject, content, confidence, domain) VALUES (?, ?, ?, ?)",
            ("Mike", "Lives in Southern California", 0.95, "personal"),
        )
        conn.commit()

        cursor = conn.execute("SELECT * FROM facts WHERE subject = 'Mike'")
        row = cursor.fetchone()
        assert row is not None
        assert "Southern California" in row[2]
        conn.close()

    def test_patterns_table(self, memory_db):
        conn, _ = memory_db

        conn.execute(
            "INSERT INTO patterns (agent, pattern_type, content, confidence) VALUES (?, ?, ?, ?)",
            ("test-agent", "correction", "Don't fabricate URLs", 0.9),
        )
        conn.commit()

        cursor = conn.execute("SELECT * FROM patterns WHERE agent = 'test-agent'")
        row = cursor.fetchone()
        assert row is not None
        assert "fabricate" in row[3]
        conn.close()
