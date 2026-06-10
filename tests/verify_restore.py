#!/usr/bin/env python3
"""
verify_restore.py — end-to-end test for restore_claude_code.py

Builds a sandbox from your real ~/.claude/projects/<project>, deletes a few
files from the sandbox, runs the main script with --dest, then checks that
the deleted files came back with correct sizes, correct historical mtimes,
and no inherited TM ACL. Cleans up after itself.

Usage:
    python3 tests/verify_restore.py --project=-Users-you-projects-foo
    python3 tests/verify_restore.py --project=-Users-you-projects-foo --keep

Requires: a Time Machine drive plugged in with snapshots containing the
chosen project. Same prereqs as the main script (Full Disk Access etc.).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_SCRIPT = REPO_ROOT / "restore_claude_code.py"
CLAUDE_DIR = Path.home() / ".claude" / "projects"
NUM_FILES_TO_TEST = 5


@dataclass
class FileFingerprint:
    path: Path
    size: int
    mtime: float


def fingerprint(path: Path) -> FileFingerprint:
    st = path.stat()
    return FileFingerprint(path=path, size=st.st_size, mtime=st.st_mtime)


def has_acl(path: Path) -> bool:
    """`ls -le` shows a '+' after the permission bits when ACLs are present."""
    out = subprocess.run(["ls", "-le", str(path)], capture_output=True, text=True).stdout
    # Lines look like:  -rw-------+ 1 user staff ...
    # vs (no ACL):      -rw-------  1 user staff ...
    # vs (xattrs only): -rw-------@ 1 user staff ...
    m = re.match(r"^\S+", out)
    return bool(m and "+" in m.group(0))


def list_recoverable(project: str, source: str) -> dict[str, tuple[int, float]]:
    """Ask the main script which JSONL filenames are present in available
    snapshots, scoped to one project. Returns {filename: (size, mtime)}
    for the largest version seen across all available snapshots.

    Uses --list-only, which emits tab-separated rows to stdout:
        kind \\t snapshot_name \\t project \\t filename \\t size \\t mtime
    Status text goes to stderr there, so stdout is parse-clean.

    Newest-first traversal means the largest (size, mtime) for a given
    filename appears in the first row for it. We still pick the max
    defensively in case ordering ever changes.
    """
    result = subprocess.run(
        [str(MAIN_SCRIPT), "--list-only",
         f"--source={source}", f"--project={project}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        die(f"--list-only exited {result.returncode}")
    largest: dict[str, tuple[int, float]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 6 and parts[2] == project:
            filename = parts[3]
            try:
                size = int(parts[4])
                mtime = float(parts[5])
            except ValueError:
                continue
            cur = largest.get(filename)
            if cur is None or size > cur[0]:
                largest[filename] = (size, mtime)
    return largest


def pick_test_files(project_dir: Path, available: dict[str, tuple[int, float]]) -> list[FileFingerprint]:
    """Pick a spread of JSONLs that both exist on-disk AND appear in at
    least one snapshot the upcoming restore will see.

    Fingerprints carry the SNAPSHOT size and mtime (from --list-only),
    not the live on-disk values. JSONLs are append-only, so a snapshot's
    copy of a file can be smaller than the live one — and that's the
    size we'll get back when we restore. Same logic for mtime: the
    snapshot has the original mtime, which is what mtime preservation
    should reproduce.

    Without the snapshot-intersection step, the test can pick files that
    don't exist in any available snapshot (common with --source=local,
    where the snapshot may be weeks old and miss recently-created files),
    leading to "restored 0" results that look like a code bug but aren't.
    """
    on_disk = list(project_dir.glob("*.jsonl"))
    before = len(on_disk)
    candidates = [p for p in on_disk if p.name in available]
    skipped = before - len(candidates)
    if skipped:
        print(f"[setup]  skipping {skipped} on-disk file(s) not present in available snapshots")
    candidates.sort(key=lambda p: available[p.name][0])
    if len(candidates) < NUM_FILES_TO_TEST:
        die(f"Only {len(candidates)} JSONLs are both on-disk and in a snapshot; "
            f"need at least {NUM_FILES_TO_TEST}.")
    # Pick evenly across the snapshot-size distribution.
    step = max(1, len(candidates) // NUM_FILES_TO_TEST)
    picks = candidates[::step][:NUM_FILES_TO_TEST]
    return [FileFingerprint(path=p, size=available[p.name][0], mtime=available[p.name][1])
            for p in picks]


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True,
                        help="encoded project dir under ~/.claude/projects "
                             "(e.g. -Users-you-projects-foo)")
    parser.add_argument("--source", choices=["local", "tm", "both"], default="both",
                        help="snapshot pool to exercise (matches the main "
                             "script's --source flag). Default 'both'.")
    parser.add_argument("--keep", action="store_true",
                        help="leave the sandbox in /tmp after the run (for inspection)")
    # Same dash-eating workaround as the main script.
    argv = sys.argv[1:]
    rewritten: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--project" and i + 1 < len(argv) and argv[i + 1].startswith("-"):
            rewritten.append(f"--project={argv[i + 1]}")
            i += 2
        else:
            rewritten.append(argv[i])
            i += 1
    args = parser.parse_args(rewritten)

    real_project = CLAUDE_DIR / args.project
    if not real_project.is_dir():
        die(f"No such project on disk: {real_project}")

    # Set up sandbox.
    sandbox_root = Path(tempfile.mkdtemp(prefix="claude-restore-verify-"))
    sandbox_project = sandbox_root / args.project
    print(f"[setup]  sandbox: {sandbox_project}")
    shutil.copytree(real_project, sandbox_project)

    # Ask the main script what's actually present in the requested
    # snapshot source(s) for this project, so we don't pick files that
    # can't possibly be restored (e.g., created after the snapshot).
    available = list_recoverable(args.project, args.source)
    print(f"[setup]  {len(available)} file(s) available in --source={args.source} snapshots")

    # Pick + delete test files. Capture fingerprints from the REAL files
    # (sandbox copies have today's mtime because cp doesn't preserve it).
    real_picks = pick_test_files(real_project, available)
    print(f"[setup]  picked {len(real_picks)} files to delete + restore:")
    for fp in real_picks:
        print(f"           {fp.size:>10} bytes  mtime={fp.mtime}  {fp.path.name}")
        (sandbox_project / fp.path.name).unlink()

    # Run main script against sandbox.
    print(f"[run]    {MAIN_SCRIPT} --dest {sandbox_root} --project={args.project} --source={args.source}")
    result = subprocess.run(
        [str(MAIN_SCRIPT), "--dest", str(sandbox_root),
         f"--project={args.project}", f"--source={args.source}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        die(f"restore script exited {result.returncode}")
    # Surface the last line of output (the summary).
    summary = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    print(f"[run]    {summary}")

    # Verify.
    # - size: must match the SNAPSHOT size (fingerprint.size, from
    #   --list-only). Live size may be larger if the file has grown
    #   since the snapshot, but we can only restore what's in the snapshot.
    # - mtime: must match the SNAPSHOT mtime (fingerprint.mtime, from
    #   --list-only) within 1s. This is THE load-bearing check — the
    #   whole script exists to avoid mtime being rewritten to "now"
    #   (see CLAUDE.md). Stamp it on the wrong value and Claude Code's
    #   cleanup deletes it again, which is the very bug we're working
    #   around.
    # - ACL: must not be present.
    failures: list[str] = []
    for original in real_picks:
        restored = sandbox_project / original.path.name
        if not restored.exists():
            failures.append(f"missing: {restored}")
            continue
        rst = restored.stat()
        if rst.st_size != original.size:
            failures.append(
                f"size mismatch on {restored.name}: "
                f"got {rst.st_size}, want {original.size} (snapshot size)"
            )
        if abs(rst.st_mtime - original.mtime) > 1.0:
            failures.append(
                f"mtime drift on {restored.name}: "
                f"got {rst.st_mtime}, want {original.mtime} (snapshot mtime) "
                f"(diff {rst.st_mtime - original.mtime:.1f}s)"
            )
        if has_acl(restored):
            failures.append(f"ACL still present on {restored.name}")

    # Clean up unless --keep.
    if args.keep:
        print(f"[keep]   sandbox left at {sandbox_root}")
    else:
        shutil.rmtree(sandbox_root, ignore_errors=True)
        print(f"[clean]  removed {sandbox_root}")

    if failures:
        print()
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print()
    print(f"PASS: {len(real_picks)} files restored with correct size, mtime, no ACL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
