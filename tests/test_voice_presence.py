"""
Tests for bin/voice_presence.py — Phase 1 of task-1788226029:
detect-and-log voice drift on outgoing replies via embedding
cosine-similarity against anchor texts, no live model call.

Embedding *generation* (fastembed/BAAI-bge-small) is intentionally not
exercised here, same reasoning test_memory_dedup.py already documents:
it's a real model load, slow and non-deterministic to pin in CI. What's
tested is everything downstream of a score existing (cosine similarity,
the flag rule, the log-write/no-write paths) via monkeypatched scores,
plus the module's own no-op behavior when fastembed genuinely isn't
available.
"""

import json

import pytest

from conftest import import_script, PACKAGE_ROOT


@pytest.fixture
def vp(monkeypatch, tmp_workspace):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_workspace))
    return import_script("voice_presence", file_path=PACKAGE_ROOT / "bin" / "voice_presence.py")


class TestCosineSimilarity:
    def test_identical_vectors_score_one(self, vp):
        assert vp._cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self, vp):
        assert vp._cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_score_negative_one(self, vp):
        assert vp._cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector_does_not_divide_by_zero(self, vp):
        assert vp._cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


class TestScoreTextGuards:
    def test_empty_text_returns_none(self, vp):
        assert vp.score_text("") is None
        assert vp.score_text("   ") is None

    def test_missing_model_returns_none(self, vp, monkeypatch):
        monkeypatch.setattr(vp, "_get_model", lambda: None)
        assert vp.score_text("anything") is None


class TestLogScore:
    def test_no_write_when_score_unavailable(self, vp, monkeypatch, tmp_workspace):
        monkeypatch.setattr(vp, "score_text", lambda text: None)
        vp.log_score("Marvin", "general", "some reply")
        assert not vp.LOG_PATH.exists()

    def test_writes_row_and_creates_parent_dir(self, vp, monkeypatch, tmp_workspace):
        fake = {"pos_sim": 0.6, "neg_sim": 0.3, "contrast": 0.3, "flagged": False}
        monkeypatch.setattr(vp, "score_text", lambda text: fake)
        vp.log_score("Marvin", "general", "a perfectly ordinary reply")
        assert vp.LOG_PATH.exists()
        rows = [json.loads(line) for line in vp.LOG_PATH.read_text().splitlines()]
        assert len(rows) == 1
        row = rows[0]
        assert row["agent"] == "Marvin"
        assert row["channel"] == "general"
        assert row["pos_sim"] == 0.6
        assert row["flagged"] is False
        assert row["snippet"] == "a perfectly ordinary reply"

    def test_appends_rather_than_overwrites(self, vp, monkeypatch, tmp_workspace):
        fake = {"pos_sim": 0.5, "neg_sim": 0.5, "contrast": 0.0, "flagged": False}
        monkeypatch.setattr(vp, "score_text", lambda text: fake)
        vp.log_score("Marvin", "general", "first")
        vp.log_score("relay", "signals", "second")
        rows = [json.loads(line) for line in vp.LOG_PATH.read_text().splitlines()]
        assert len(rows) == 2
        assert [r["agent"] for r in rows] == ["Marvin", "relay"]

    def test_flagged_row_logs_a_warning(self, vp, monkeypatch, tmp_workspace, caplog):
        fake = {"pos_sim": 0.2, "neg_sim": 0.7, "contrast": -0.5, "flagged": True}
        monkeypatch.setattr(vp, "score_text", lambda text: fake)
        with caplog.at_level("WARNING"):
            vp.log_score("Marvin", "general", "a generic ops-bot reply")
        assert any("voice-presence" in r.message for r in caplog.records)

    def test_unflagged_row_does_not_log_a_warning(self, vp, monkeypatch, tmp_workspace, caplog):
        fake = {"pos_sim": 0.7, "neg_sim": 0.2, "contrast": 0.5, "flagged": False}
        monkeypatch.setattr(vp, "score_text", lambda text: fake)
        with caplog.at_level("WARNING"):
            vp.log_score("Marvin", "general", "a properly dry reply")
        assert not any("voice-presence" in r.message for r in caplog.records)
