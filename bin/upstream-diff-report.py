#!/usr/bin/env python3
"""
upstream-diff-report.py — Compare our engine-classified files against
mcarmody/karakos-package's current main, without attempting to merge.

Built 2026-08-30 as the narrower version of Phase 3 (task-1788075644):
a real divergence check (cloning upstream into /tmp and diffing
bin/agent-server.py etc. by hand) showed a clean `git merge` isn't
tractable anymore — agent-server.py alone is a 4,610-line diff on a file
that's only ~3-4k lines either side. Ian's call: accept the drift, but
still keep visibility into what's diverged and what each side has that
the other doesn't, rather than losing that entirely.

Only compares paths listed under "engine" in
config/repo-split-manifest.json — instance files (agents/Marvin/,
config/channels.json, etc.) were never expected to match upstream and
aren't meaningful to diff against someone else's install.

This is comparison only, read-only against a local cache clone under
data/upstream-karakos-package/ (gitignored, not committed). Nothing here
merges, pulls changes into our tree, or touches the live repo's own
history/remotes.

Usage:
  bin/upstream-diff-report.py                  # summary table
  bin/upstream-diff-report.py --refresh         # git pull the cache first
  bin/upstream-diff-report.py --full-diff PATH  # unified diff for one file
  bin/upstream-diff-report.py --top 30          # show more rows
"""

import argparse
import json
import os
import subprocess
from pathlib import Path

WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", Path(__file__).resolve().parent.parent))
MANIFEST_PATH = WORKSPACE_ROOT / "config" / "repo-split-manifest.json"
UPSTREAM_URL = "https://github.com/mcarmody/karakos-package.git"
CACHE_DIR = WORKSPACE_ROOT / "data" / "upstream-karakos-package"

# Vendored/generated noise -- real files, correctly classified engine by
# the manifest (directory-level granularity doesn't distinguish "the
# skill's own code" from "the third-party library pip-vendored inside
# it"), but comparing them against upstream produces hundreds of
# meaningless "local-only" hits (skills/calendar/vendor/ alone is 1,345
# files of the click/icalendar/tzdata packages, not feature work).
_NOISE_DIR_MARKERS = {"vendor", "__pycache__", "node_modules", ".next"}


def engine_files() -> list[str]:
    """Every git-tracked path in our repo classified 'engine' in the
    Phase 2 manifest, resolved down to actual file paths, minus vendored/
    generated noise (see _NOISE_DIR_MARKERS)."""
    manifest = json.loads(MANIFEST_PATH.read_text())
    prefixes = manifest["engine"]
    out = subprocess.run(
        ["git", "ls-files"], cwd=WORKSPACE_ROOT, capture_output=True, text=True, check=True
    )
    tracked = [line for line in out.stdout.splitlines() if line]
    return [
        path for path in tracked
        if any(path == p or (p.endswith("/") and path.startswith(p)) for p in prefixes)
        and not any(f"/{marker}/" in f"/{path}/" for marker in _NOISE_DIR_MARKERS)
    ]


def ensure_upstream_clone(refresh: bool) -> None:
    if not CACHE_DIR.exists():
        CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", UPSTREAM_URL, str(CACHE_DIR)], check=True)
    elif refresh:
        subprocess.run(["git", "-C", str(CACHE_DIR), "fetch", "--quiet", "origin"], check=True)
        subprocess.run(["git", "-C", str(CACHE_DIR), "reset", "--quiet", "--hard", "origin/main"], check=True)


def upstream_commit_desc() -> str:
    result = subprocess.run(
        ["git", "-C", str(CACHE_DIR), "log", "-1", "--format=%h %ad %s", "--date=short"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def diff_line_count(ours: Path, theirs: Path) -> int:
    """Count of +/- lines in a unified diff between two files. 0 means
    byte-identical (diff's own exit code, not a line-count heuristic)."""
    result = subprocess.run(["diff", "-u", str(theirs), str(ours)], capture_output=True, text=True)
    if result.returncode == 0:
        return 0
    return sum(
        1 for line in result.stdout.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )


def build_report():
    """Returns (compared_rows, local_only_paths). compared_rows is a list
    of (relpath, diff_line_count) sorted most-diverged first. Files that
    only exist on our side (no upstream equivalent) are listed separately
    -- they're not "diverged", they're local-only feature work."""
    compared = []
    local_only = []
    for rel in engine_files():
        ours = WORKSPACE_ROOT / rel
        theirs = CACHE_DIR / rel
        if not ours.is_file():
            continue
        if not theirs.exists():
            local_only.append(rel)
            continue
        compared.append((rel, diff_line_count(ours, theirs)))
    compared.sort(key=lambda row: -row[1])
    return compared, sorted(local_only)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="git pull the cached upstream clone first")
    parser.add_argument("--full-diff", metavar="PATH", help="print the unified diff for one engine-relative path")
    parser.add_argument("--top", type=int, default=15, help="how many most-diverged files to print (default 15)")
    args = parser.parse_args()

    ensure_upstream_clone(args.refresh)

    if args.full_diff:
        theirs = CACHE_DIR / args.full_diff
        ours = WORKSPACE_ROOT / args.full_diff
        subprocess.run(["diff", "-u", str(theirs), str(ours)])
        return

    compared, local_only = build_report()
    identical = [row for row in compared if row[1] == 0]
    diverged = [row for row in compared if row[1] > 0]

    print(f"Upstream (mcarmody/karakos-package): {upstream_commit_desc()}")
    print(f"Compared {len(compared)} engine files that exist on both sides "
          f"({len(identical)} identical, {len(diverged)} diverged).\n")

    print(f"Most-diverged (top {args.top}):")
    for rel, diff_lines in diverged[: args.top]:
        print(f"  {rel:<50} {diff_lines} changed lines")

    if local_only:
        print(f"\n{len(local_only)} engine files with no upstream equivalent (local-only feature work):")
        for rel in local_only:
            print(f"  {rel}")


if __name__ == "__main__":
    main()
