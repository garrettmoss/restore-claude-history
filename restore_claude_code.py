#!/usr/bin/env python3
"""
restore_claude_code.py

Recover deleted Claude Code chat transcripts (~/.claude/projects/<project>/*.jsonl)
from macOS Time Machine APFS snapshots.

For each (project, filename) seen across all snapshots, picks the LARGEST
version (JSONLs are append-only, so bigger == more complete) and copies it
back, preserving mtime and stripping the inherited Time Machine ACL.

macOS + APFS Time Machine only. Requires Full Disk Access for the terminal
or IDE running this. See NOTES.md for background.
"""

from __future__ import annotations

__version__ = "1.1.0"

import argparse
import getpass
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


# -------- types --------


@dataclass
class Snapshot:
    """One APFS snapshot — either on an external TM volume or local to the boot Data volume."""

    name: str                # e.g. "com.apple.TimeMachine.2026-04-24-205237.backup"
    device: str = ""         # BSD device the snapshot lives on, e.g. "disk5s2" or "disk3s5"
    kind: str = "tm"         # "tm" (external TM drive) or "local" (internal Data volume)
    mountpoint: Path | None = None
    owned_by_us: bool = False  # True if we mount_apfs'd it (cleanup will unmount)


@dataclass
class JsonlEntry:
    """One JSONL found inside a snapshot."""

    project: str             # encoded project dir name
    filename: str            # <uuid>.jsonl
    size: int
    src: Path                # absolute path inside the (mounted) snapshot


# -------- shell helpers --------


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command, capture stdout/stderr as text."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# -------- TM device detection --------


def find_tm_device() -> str | None:
    """
    Return e.g. 'disk5s2' — the APFS volume that is the user's TM destination,
    or None if no TM drive is currently mounted.

    `tmutil destinationinfo` is authoritative; it lists the actual TM
    destinations and their mount points. We resolve the mount point back to
    a BSD device via `diskutil info`. Falls back to scanning APFS volumes
    whose *volume name* contains "Time Machine" if tmutil has nothing.

    Why we don't just grep `diskutil info <dev>` for "Time Machine": that
    field also lists snapshot names, and every Mac with local TM snapshots
    on the internal disk will match — so we'd accidentally pick the
    internal data volume.
    """
    info = run(["tmutil", "destinationinfo"], check=False).stdout
    for mp in re.findall(r"^Mount Point\s*:\s*(.+)$", info, re.MULTILINE):
        dev_info = run(["diskutil", "info", mp.strip()], check=False).stdout
        m = re.search(r"Device Node:\s*/dev/(disk\d+s\d+)", dev_info)
        if m:
            return m.group(1)

    # Fallback: scan APFS volumes by *volume name* (not the full info blob).
    listing = run(["diskutil", "list"]).stdout
    # Lines: "   1:    APFS Volume Sapphire Time Machine   148.1 GB   disk5s2"
    for line in listing.splitlines():
        m = re.match(r"\s*\d+:\s+APFS Volume\s+(.+?)\s+[\d.]+\s+\w+\s+(disk\d+s\d+)", line)
        if m and re.search(r"time\s*machine", m.group(1), re.IGNORECASE):
            return m.group(2)

    return None


def find_local_data_device() -> str | None:
    """
    Return the BSD device for the internal APFS Data volume (e.g. 'disk3s5'),
    where macOS keeps local TM snapshots (`com.apple.TimeMachine.<ts>.local`).

    The Data volume is always mounted at /System/Volumes/Data on modern macOS
    (Catalina+); resolve via diskutil. Returns None if anything goes sideways
    (non-APFS host, ancient macOS, etc.) so the caller can fall through.
    """
    dev_info = run(["diskutil", "info", "/System/Volumes/Data"], check=False).stdout
    m = re.search(r"Device Node:\s*/dev/(disk\d+s\d+)", dev_info)
    return m.group(1) if m else None


def list_snapshots(device: str) -> list[str]:
    """List snapshot names on /dev/<device>.

    `diskutil apfs listSnapshots` outputs a tree with leading pipe chars:
        |   Name:   com.apple.TimeMachine.<ts>.backup
    so we match Name: anywhere on the line, not just after whitespace.
    """
    out = run(["diskutil", "apfs", "listSnapshots", f"/dev/{device}"], check=False).stdout
    return [m.group(1).strip() for m in re.finditer(r"Name:\s*(\S+)", out)]


# -------- mount management --------


def existing_mounts() -> dict[str, Path]:
    """Map snapshot-name -> mountpoint for snapshots macOS already has mounted."""
    out = run(["mount"], check=False).stdout
    result: dict[str, Path] = {}
    # Lines look like:
    # com.apple.TimeMachine.<ts>.backup@/dev/diskNsM on /Volumes/.timemachine/<UUID>/<ts>.backup (apfs, ...)
    for line in out.splitlines():
        m = re.match(r"^(com\.apple\.TimeMachine\.[^@]+\.backup)@\S+ on (.+) \(", line)
        if m:
            result[m.group(1)] = Path(m.group(2))
    return result


def mount_snapshot(snap: Snapshot, tmp_root: Path) -> bool:
    """Mount `snap` ourselves under tmp_root. Returns True on success."""
    # Strip both possible suffixes — .backup (TM drive) and .local (boot Data volume).
    label = (snap.name
             .removeprefix("com.apple.TimeMachine.")
             .removesuffix(".backup")
             .removesuffix(".local"))
    mp = tmp_root / f"snap-{snap.kind}-{label}"
    mp.mkdir(parents=True, exist_ok=True)
    try:
        run(["mount_apfs", "-s", snap.name, f"/dev/{snap.device}", str(mp)])
    except subprocess.CalledProcessError as e:
        print(f"  warn: failed to mount {snap.name}: {e.stderr.strip()}", file=sys.stderr)
        try:
            mp.rmdir()
        except OSError:
            pass
        return False
    snap.mountpoint = mp
    snap.owned_by_us = True
    return True


def unmount_if_ours(snap: Snapshot) -> None:
    """Unmount + rmdir a snapshot we mounted. Borrowed mounts are left alone."""
    if not (snap.owned_by_us and snap.mountpoint):
        return
    mp = snap.mountpoint
    # Try graceful unmount, then forced.
    for args in (["diskutil", "unmount", str(mp)],
                 ["diskutil", "unmount", "force", str(mp)]):
        r = run(args, check=False)
        if r.returncode == 0:
            break
    try:
        mp.rmdir()
    except OSError:
        pass


# -------- data layout probing --------


def find_data_root(mp: Path) -> Path | None:
    """
    Locate the dir containing 'Users/<user>/.claude/...' inside a snapshot
    mountpoint. Three known layouts:

      1. TM-drive snapshot via our mount_apfs: <mp>/<ts>.backup/Data/Users/...
      2. TM-drive snapshot via macOS auto-mount: same as (1), or sometimes
         <mp>/Data/Users/...
      3. Local snapshot of the boot Data volume (snapshots of /dev/diskNsM
         where N is the Data volume): the snapshot IS the Data volume, so
         layout is <mp>/Users/... directly — no Data/ wrapper, no .backup
         wrapper.

    Returned path is the dir whose immediate child is 'Users'.
    """
    if (mp / "Users").is_dir():
        return mp                          # local-snapshot layout
    if (mp / "Data" / "Users").is_dir():
        return mp / "Data"                 # TM-drive direct layout
    for child in mp.glob("*.backup"):
        if (child / "Data" / "Users").is_dir():
            return child / "Data"          # TM-drive auto-mount layout
    return None


# -------- indexing --------


def index_snapshot(
    snap: Snapshot,
    user: str,
    only_project: str | None,
    verbose: bool,
) -> list[JsonlEntry]:
    """Walk one mounted snapshot and return every JSONL it contains."""
    if snap.mountpoint is None:
        return []
    data = find_data_root(snap.mountpoint)
    if data is None:
        if verbose:
            print(f"  {snap.name}: no Data/ dir under {snap.mountpoint}")
        return []
    projects = data / "Users" / user / ".claude" / "projects"
    if not projects.is_dir():
        if verbose:
            print(f"  {snap.name}: no projects dir at {projects}")
        return []

    entries: list[JsonlEntry] = []
    for proj_dir in projects.iterdir():
        if not proj_dir.is_dir():
            continue
        if only_project and proj_dir.name != only_project:
            continue
        for jsonl in proj_dir.glob("*.jsonl"):
            try:
                size = jsonl.stat().st_size
            except OSError:
                continue
            entries.append(JsonlEntry(
                project=proj_dir.name,
                filename=jsonl.name,
                size=size,
                src=jsonl,
            ))
    return entries


# -------- restore --------


def strip_acl_and_make_writable(path: Path) -> None:
    """Remove the inherited TM ACL and ensure the user can overwrite."""
    run(["chmod", "-N", str(path)], check=False)
    try:
        st = path.stat()
        path.chmod(st.st_mode | stat.S_IWUSR)
    except OSError:
        pass


def restore_file(entry: JsonlEntry, claude_dir: Path, dry_run: bool, verbose: bool) -> tuple[bool, int]:
    """
    Restore one JSONL if the on-disk version is missing or smaller.
    Returns (restored, bytes).
    """
    dest_dir = claude_dir / entry.project
    dest = dest_dir / entry.filename

    if dest.exists():
        if dest.stat().st_size >= entry.size:
            if verbose:
                print(f"skip   {entry.project}/{entry.filename} "
                      f"(on-disk {dest.stat().st_size} >= snapshot {entry.size})")
            return (False, 0)

    if dry_run:
        print(f"would  {entry.project}/{entry.filename} ({entry.size} bytes)")
        return (True, entry.size)

    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        strip_acl_and_make_writable(dest)

    # shutil.copy2 preserves mtime via copystat; we still touch -r below in
    # case any future ACL/permission shenanigans break copystat.
    shutil.copy2(entry.src, dest)
    try:
        src_stat = entry.src.stat()
        os.utime(dest, (src_stat.st_atime, src_stat.st_mtime))
    except OSError:
        pass
    strip_acl_and_make_writable(dest)

    if verbose:
        print(f"restore {entry.project}/{entry.filename} ({entry.size} bytes)")
    return (True, entry.size)


def restore_subdirs_from_snapshot(
    snap: Snapshot,
    user: str,
    claude_dir: Path,
    only_project: str | None,
    include_memory: bool,
    dry_run: bool,
    verbose: bool,
) -> int:
    """
    Restore per-session subdirs (subagents/, etc.) and optionally memory/
    from one mounted snapshot. Skips dirs that already exist on disk —
    so when called newest-first across snapshots, first writer wins.
    """
    if snap.mountpoint is None:
        return 0
    data = find_data_root(snap.mountpoint)
    if data is None:
        return 0
    projects = data / "Users" / user / ".claude" / "projects"
    if not projects.is_dir():
        return 0
    copied = 0
    for proj_dir in projects.iterdir():
        if not proj_dir.is_dir():
            continue
        if only_project and proj_dir.name != only_project:
            continue
        dest_proj = claude_dir / proj_dir.name
        for sub in proj_dir.iterdir():
            if not sub.is_dir():
                continue
            if sub.name == "memory" and not include_memory:
                continue
            dest = dest_proj / sub.name
            if dest.exists():
                continue
            if dry_run:
                print(f"would  subdir {dest} (from {sub})")
                copied += 1
                continue
            dest_proj.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copytree(sub, dest)
            except OSError as e:
                print(f"  fail: copytree {sub} -> {dest}: {e}", file=sys.stderr)
                continue
            for root, _dirs, files in os.walk(dest):
                for name in files:
                    strip_acl_and_make_writable(Path(root) / name)
            if verbose:
                print(f"restore subdir {dest}")
            copied += 1
    return copied


# -------- main --------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Restore deleted Claude Code chat transcripts from Time Machine snapshots.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--dry-run", action="store_true",
                   help="show what would be restored; copy nothing")
    p.add_argument("--project", metavar="NAME",
                   help="limit to one encoded project dir "
                        "(e.g. -Users-you-projects-foo)")
    p.add_argument("--include-memory", action="store_true",
                   help="also restore <project>/memory/ subdirs")
    p.add_argument("--verbose", action="store_true",
                   help="log every file decision, not just the summary")
    p.add_argument("--dest", metavar="DIR", type=Path,
                   help="restore into DIR instead of ~/.claude/projects "
                        "(useful for testing against a copy of your real projects)")
    p.add_argument("--source", choices=["local", "tm", "both"], default="both",
                   help="which snapshot pool to search: 'local' (internal "
                        "Data-volume APFS snapshots, no drive needed), 'tm' "
                        "(external Time Machine drive), or 'both' (default — "
                        "uses whichever is available, prefers newest first "
                        "across pools)")
    p.add_argument("--list-only", action="store_true",
                   help="don't restore anything; just list one tab-separated "
                        "row per (project, filename) pair found in snapshots "
                        "(kind, snapshot_name, project, filename, size). "
                        "Honors --source and --project. Useful for previewing "
                        "what's recoverable, and for test tooling.")

    # Encoded project names start with '-', which argparse would otherwise
    # mistake for another flag. Rewrite "--project FOO" -> "--project=FOO"
    # so users don't have to remember the '=' syntax.
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
    return p.parse_args(rewritten)


def main() -> int:
    if sys.platform != "darwin":
        die("macOS only.")

    args = parse_args()
    # getpass.getuser() reads LOGNAME/USER env vars; more reliable than
    # os.getlogin() in non-TTY contexts (where it can return "root").
    user = getpass.getuser()
    claude_dir = args.dest if args.dest else Path.home() / ".claude" / "projects"
    # Route status/progress to stderr in --list-only mode so stdout stays
    # purely machine-readable rows. Otherwise to stdout, matching prior
    # behavior.
    status = sys.stderr if args.list_only else sys.stdout
    if args.dest:
        print(f"Destination override: {claude_dir}", file=status)

    # Collect snapshots from one or both sources per --source.
    # `tm` = external TM drive (deep history, but Spotlight-noisy and
    # requires drive plugged in); `local` = internal Data volume snapshots
    # (shallow — tied to TM activity, retained ~24h or until disk pressure
    # prunes them — but always available, no drive needed). `both` (default)
    # uses whichever sources actually have data.
    snapshots: list[Snapshot] = []

    if args.source in ("tm", "both"):
        tm_device = find_tm_device()
        if tm_device is None:
            if args.source == "tm":
                die("No Time Machine APFS volume detected. Plug in your TM drive and try again.")
            if args.verbose:
                print("no Time Machine drive detected; continuing with local snapshots only",
                      file=status)
        else:
            tm_names = list_snapshots(tm_device)
            print(f"Time Machine volume: /dev/{tm_device} ({len(tm_names)} snapshot(s))",
                  file=status)
            snapshots.extend(
                Snapshot(name=n, device=tm_device, kind="tm") for n in tm_names
            )

    if args.source in ("local", "both"):
        local_device = find_local_data_device()
        if local_device is None:
            if args.source == "local":
                die("Could not locate the internal APFS Data volume (/System/Volumes/Data).")
            if args.verbose:
                print("no internal Data volume located; skipping local snapshots",
                      file=status)
        else:
            local_names = list_snapshots(local_device)
            print(f"Local Data volume:    /dev/{local_device} ({len(local_names)} snapshot(s))",
                  file=status)
            snapshots.extend(
                Snapshot(name=n, device=local_device, kind="local") for n in local_names
            )

    if not snapshots:
        die("No snapshots found in any requested source. "
            "Plug in your TM drive, or check that local APFS snapshots exist "
            "(`tmutil listlocalsnapshots /System/Volumes/Data`).")

    # Walk snapshots newest-first across the merged pool. Snapshot names
    # embed timestamps (com.apple.TimeMachine.YYYY-MM-DD-HHMMSS.{backup,local}),
    # so lexical sort puts newest first regardless of source. Two reasons for
    # the ordering:
    #   1. JSONLs are append-only; the newest snapshot containing a given
    #      (project, filename) holds the largest version. First sighting
    #      wins (via the `seen` set below), so we never copy a file we'll
    #      later overwrite — and we can short-circuit older snapshots
    #      entirely for pairs we've already restored.
    #   2. restore_subdirs_from_snapshot uses first-writer-wins for session
    #      subdirs (subagents/, memory/), matching the prior all-at-end
    #      behavior.
    snapshots.sort(key=lambda s: s.name, reverse=True)

    # Surface the date range of the available snapshots — gives users a
    # "how stale is my safety net?" read at a glance. Snapshot names embed
    # YYYY-MM-DD-HHMMSS, so we can pull dates from the sorted list directly.
    def _snap_date(name: str) -> str:
        m = re.search(r"(\d{4}-\d{2}-\d{2})-\d{6}", name)
        return m.group(1) if m else "?"
    newest_date = _snap_date(snapshots[0].name)
    oldest_date = _snap_date(snapshots[-1].name)
    if newest_date == oldest_date:
        span = newest_date
    else:
        span = f"{oldest_date} → {newest_date}"
    print(f"Snapshots span: {span} ({len(snapshots)} total across requested sources)",
          file=status)

    pre_mounted = existing_mounts()

    # Sequential mount → index → restore → unmount, one snapshot at a time.
    # Earlier versions mounted every snapshot up front; that scaled poorly
    # because macOS Spotlight spins up an mds_stores worker per mount as
    # soon as it appears, and a TM drive can hold 75–150 snapshots. Holding
    # one owned mount at a time bounds that to a single Spotlight worker
    # for the duration of one snapshot's walk.
    tmp_root: Path | None = None
    seen: set[tuple[str, str]] = set()
    restored = 0
    total_bytes = 0
    skipped = 0
    subdirs = 0
    indexed_pairs = 0
    try:
        for snap in snapshots:
            owned = snap.name not in pre_mounted
            if owned:
                if tmp_root is None:
                    tmp_root = Path(tempfile.mkdtemp(prefix="tm-claude-restore-"))
                if args.verbose:
                    print(f"mounting {snap.name} under {tmp_root}", file=status)
                if not mount_snapshot(snap, tmp_root):
                    continue
            else:
                snap.mountpoint = pre_mounted[snap.name]
                if args.verbose:
                    print(f"using existing mount: {snap.name} -> {snap.mountpoint}",
                          file=status)

            try:
                entries = index_snapshot(snap, user, args.project, args.verbose)
                for entry in entries:
                    key = (entry.project, entry.filename)
                    if key in seen:
                        continue
                    seen.add(key)
                    indexed_pairs += 1
                    if args.list_only:
                        # Tab-separated: kind, snapshot, project, filename,
                        # size, mtime (float epoch seconds). Stable
                        # machine-readable surface for callers (test
                        # harness, user previews). Status lines go to
                        # stderr in list-only mode, so stdout stays clean.
                        try:
                            mtime = entry.src.stat().st_mtime
                        except OSError:
                            mtime = 0.0
                        print(f"{snap.kind}\t{snap.name}\t{entry.project}\t"
                              f"{entry.filename}\t{entry.size}\t{mtime}")
                        continue
                    ok, n = restore_file(entry, claude_dir, args.dry_run, args.verbose)
                    if ok:
                        restored += 1
                        total_bytes += n
                    else:
                        skipped += 1
                if not args.list_only:
                    subdirs += restore_subdirs_from_snapshot(
                        snap, user, claude_dir,
                        args.project, args.include_memory,
                        args.dry_run, args.verbose,
                    )
            finally:
                unmount_if_ours(snap)

        if indexed_pairs == 0:
            die(f"No Claude JSONL files found in any snapshot for user '{user}'.")

        if args.list_only:
            # No summary line — keep stdout pure machine-readable.
            return 0

        print(f"Indexed {indexed_pairs} unique (project, jsonl) pairs across snapshots.")
        print()
        prefix = "DRY RUN: would restore" if args.dry_run else "Restored"
        print(f"{prefix} {restored} file(s), {total_bytes} byte(s). "
              f"Skipped {skipped} already-current. Subdirs: {subdirs}.")
        return 0

    finally:
        if tmp_root is not None:
            try:
                tmp_root.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
