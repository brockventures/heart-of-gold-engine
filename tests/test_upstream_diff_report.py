"""
Tests for bin/upstream-diff-report.py (task-1788075644, Phase 3 narrow
version, 2026-08-30).

Real divergence analysis showed a clean git merge with
mcarmody/karakos-package isn't tractable anymore (agent-server.py alone:
a 4,610-line diff on a ~3-4k line file). This script exists to keep
visibility into what's diverged and what's local-only feature work
without pretending reconciliation is mechanical. These tests exercise
engine_files() and build_report()'s diffing/sorting/bucketing logic
against fixture trees -- no real network access, no real clone of
mcarmody/karakos-package.
"""

import json
import subprocess

import pytest

from conftest import import_script


@pytest.fixture
def script():
    return import_script("upstream-diff-report")


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A tiny fake 'our repo' with a manifest and a couple of engine
    files, git-tracked so `git ls-files` works for real."""
    root = tmp_path / "ours"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)

    (root / "config").mkdir()
    manifest = {
        "engine": ["bin/", "README.md"],
        "instance": ["config/channels.json"],
        "ambiguous": {},
        "runtime_only_not_manifested": [],
    }
    (root / "config" / "repo-split-manifest.json").write_text(json.dumps(manifest))
    (root / "config" / "channels.json").write_text("{}")

    (root / "bin").mkdir()
    (root / "bin" / "shared.py").write_text("line1\nline2\nline3\n")
    (root / "bin" / "local_only.py").write_text("only we have this\n")
    (root / "README.md").write_text("readme\n")

    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=root, check=True)

    monkeypatch.setenv("WORKSPACE_ROOT", str(root))
    return root


@pytest.fixture
def fake_upstream(tmp_path):
    """A fake 'upstream' tree (not a real clone, just files at the cache
    path build_report() reads from) with one identical file and one
    diverged file."""
    return tmp_path


class TestEngineFiles:
    def test_returns_only_engine_classified_tracked_files(self, script, fake_repo, monkeypatch):
        monkeypatch.setattr(script, "WORKSPACE_ROOT", fake_repo)
        monkeypatch.setattr(script, "MANIFEST_PATH", fake_repo / "config" / "repo-split-manifest.json")
        files = script.engine_files()
        assert set(files) == {"bin/shared.py", "bin/local_only.py", "README.md"}
        assert "config/channels.json" not in files


class TestBuildReport:
    def test_identical_diverged_and_local_only_buckets(self, script, fake_repo, monkeypatch):
        monkeypatch.setattr(script, "WORKSPACE_ROOT", fake_repo)
        monkeypatch.setattr(script, "MANIFEST_PATH", fake_repo / "config" / "repo-split-manifest.json")

        cache_dir = fake_repo.parent / "cache"
        cache_dir.mkdir()
        (cache_dir / "bin").mkdir()
        # Identical to ours.
        (cache_dir / "bin" / "shared.py").write_text("line1\nline2\nline3\n")
        # README exists upstream too, but different content -> diverged.
        (cache_dir / "README.md").write_text("a totally different readme\nwith more lines\nhere\n")
        # local_only.py deliberately has no upstream counterpart.
        monkeypatch.setattr(script, "CACHE_DIR", cache_dir)

        compared, local_only = script.build_report()

        assert local_only == ["bin/local_only.py"]

        by_path = dict(compared)
        assert by_path["bin/shared.py"] == 0  # identical
        assert by_path["README.md"] > 0  # diverged

        # Most-diverged sorts first.
        assert compared[0][0] == "README.md"

    def test_diff_line_count_zero_for_identical_files(self, script, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("same\ncontent\n")
        b.write_text("same\ncontent\n")
        assert script.diff_line_count(a, b) == 0

    def test_diff_line_count_positive_for_different_files(self, script, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("one\ntwo\nthree\n")
        b.write_text("one\nTWO\nthree\nfour\n")
        assert script.diff_line_count(a, b) > 0
