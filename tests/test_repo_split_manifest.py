"""
Guardrail for config/repo-split-manifest.json (Phase 2 of the generic-
engine/instance-config split, task-1788075644, 2026-08-30).

Two checks:

1. Completeness -- every git-tracked path must be covered by one of the
   manifest's buckets (engine / instance / ambiguous /
   runtime_only_not_manifested). A new top-level file or directory that
   nobody classified should fail loudly here instead of silently sitting
   unclassified until someone tries to do the real repo split and has to
   re-derive all of this under pressure.

2. Leakage -- none of the real Discord snowflake IDs live in
   config/channels.json (an instance file) should appear inside a file
   classified as engine. Engine is supposed to mean "portable to any
   Karakos install unmodified" -- a hardcoded real ID is exactly the kind
   of thing that would make that false.

Nothing here moves any files. See docs/design/repo-split-manifest.md for
the reasoning behind each bucket.

Both config/repo-split-manifest.json and config/channels.json are
themselves classified "instance" (real per-install data) -- as of the
Phase 3 split (task-1788078430, 2026-08-30) they no longer live in this
(engine) repo at all. Locally they're symlinks into the sibling instance
checkout; in a fresh clone (CI, or any other engine-only install) they
simply don't exist. This whole module is a no-op skip in that case --
the guardrail still has value on a machine with the instance repo
checked out alongside, but it can't be a hard CI requirement for a repo
that, by design, doesn't carry the data it's checking.
"""

import json
import re
import subprocess

import pytest

from conftest import PACKAGE_ROOT

MANIFEST_PATH = PACKAGE_ROOT / "config" / "repo-split-manifest.json"
CHANNELS_PATH = PACKAGE_ROOT / "config" / "channels.json"

if not (MANIFEST_PATH.exists() and CHANNELS_PATH.exists()):
    pytest.skip(
        "config/repo-split-manifest.json and/or config/channels.json not "
        "present -- both are instance-classified and live outside this "
        "engine repo post-split (task-1788078430); skipping the guardrail "
        "rather than failing CI on data it will never have.",
        allow_module_level=True,
    )

# Directories whose *contents* we don't expect to read as text (binary,
# vendored, or otherwise noisy) when scanning for leaked IDs.
_LEAKAGE_SCAN_EXCLUDE_DIRS = {"vendor", "__pycache__", "node_modules", ".next"}


def _load_manifest():
    return json.loads(MANIFEST_PATH.read_text())


def _tracked_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=PACKAGE_ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line]


def _bucket_prefixes(manifest):
    """Flatten engine/instance/ambiguous/runtime_only into one list of
    path prefixes -- a manifest entry ending in '/' covers everything
    under it, one not ending in '/' covers just that exact file."""
    prefixes = []
    prefixes.extend(manifest["engine"])
    prefixes.extend(manifest["instance"])
    prefixes.extend(manifest["ambiguous"].keys())
    prefixes.extend(manifest["runtime_only_not_manifested"])
    return prefixes


def _is_covered(path, prefixes):
    return any(
        path == p or (p.endswith("/") and path.startswith(p))
        for p in prefixes
    )


class TestManifestCompleteness:
    def test_manifest_parses(self):
        manifest = _load_manifest()
        for key in ("engine", "instance", "ambiguous", "runtime_only_not_manifested"):
            assert key in manifest

    def test_every_tracked_file_is_classified(self):
        manifest = _load_manifest()
        prefixes = _bucket_prefixes(manifest)
        uncovered = [
            path for path in _tracked_files()
            if not _is_covered(path, prefixes)
        ]
        assert uncovered == [], (
            "These git-tracked paths aren't classified in "
            "config/repo-split-manifest.json (engine/instance/ambiguous/"
            "runtime_only_not_manifested) -- add them to a bucket:\n  "
            + "\n  ".join(uncovered)
        )

    def test_no_path_is_double_classified(self):
        """A path prefix listed in two buckets is a real ambiguity that
        should show up in 'ambiguous', not silently pick whichever
        bucket happens to get checked first."""
        manifest = _load_manifest()
        engine = set(manifest["engine"])
        instance = set(manifest["instance"])
        overlap = engine & instance
        assert overlap == set(), f"listed in both engine and instance: {overlap}"


class TestNoInstanceLeakageIntoEngine:
    """A real Discord ID from config/channels.json showing up inside a
    file classified as 'engine' means that file isn't actually portable
    to another Karakos install -- the whole point of the engine bucket."""

    @pytest.fixture
    def real_ids(self):
        channels = json.loads(CHANNELS_PATH.read_text())
        ids = set()
        ids.update(channels.get("server_ids", []))
        for ch in channels.get("channels", {}).values():
            if ch.get("id"):
                ids.add(str(ch["id"]))
            if ch.get("guild_id"):
                ids.add(str(ch["guild_id"]))
            for bot_id in ch.get("known_bots", []) or []:
                ids.add(str(bot_id))
        return ids

    @pytest.fixture
    def engine_files(self):
        manifest = _load_manifest()
        engine_prefixes = manifest["engine"]
        files = []
        for path in _tracked_files():
            if not any(
                path == p or (p.endswith("/") and path.startswith(p))
                for p in engine_prefixes
            ):
                continue
            if any(f"/{d}/" in f"/{path}/" for d in _LEAKAGE_SCAN_EXCLUDE_DIRS):
                continue
            files.append(path)
        return files

    def test_no_real_discord_ids_in_engine_files(self, real_ids, engine_files):
        assert real_ids, "channels.json produced no IDs to check against -- fixture bug"
        violations = []
        for rel_path in engine_files:
            full_path = PACKAGE_ROOT / rel_path
            try:
                text = full_path.read_text(errors="ignore")
            except (UnicodeDecodeError, IsADirectoryError, OSError):
                continue
            for real_id in real_ids:
                # Word-boundary match so a real ID isn't flagged as a
                # substring of some unrelated longer number.
                if re.search(rf"(?<!\d){re.escape(real_id)}(?!\d)", text):
                    violations.append(f"{rel_path}: contains {real_id}")
        assert violations == [], (
            "Real Discord IDs from config/channels.json (instance data) "
            "found inside files classified as engine (should be portable "
            "to any Karakos install):\n  " + "\n  ".join(violations)
        )
