#!/usr/bin/env python3
"""
backup_claude_history.py

Forward backup of ~/.claude/projects/ so recovery stops depending on Time
Machine. Copies Claude Code JSONL transcripts out of the deletion path on every
session launch, before the next cleanup sweep can touch them.

This is the everyday prevention path. restore_claude_code.py stays the deep
failsafe for chats older than these backups reach (it pulls from Time Machine /
APFS snapshots). Two scripts, two sources, one job each — by design there is no
"check backups, fall back to TM" router here.

Verbs (backup-v0.1.0, the prevention half):
  backup     Run one copy-on-grow pass. This is what the SessionStart hook calls.
  install    Merge a SessionStart hook into ~/.claude/settings.json (non-clobbering).
  uninstall  Remove that hook again.
  status     Is the hook installed, when did backup last run, anything stale?
  list       What's backed up, grouped by project.

On-disk layout (mirror + manifest):
  ~/.claude-code-backups/
    projects/<encoded-project>/<uuid>.jsonl       mirror of ~/.claude/projects/
    manifest.json                                 per-file {bytes, src_mtime, backed_up_at}
The manifest exists to detect *silent hook failure* — a backup that quietly
stopped running is the classic way backups betray you. `status` reads it.

Copy-on-grow: a file is backed up only when the backup is missing or the live
file has *grown*. JSONLs are append-only (/rewind orphans a dead branch and
keeps appending; it does not shrink the file), so one high-water-mark copy per
file is lossless. Keying on grew-only — not mtime, not size-differs — makes the
engine both mtime-immune and shrink-safe. mtime is preserved on copy so a
restored file never looks "just now" to Claude's mtime-keyed cleanup.

Credit: the copy-on-grow SessionStart-hook approach is @ojura's, sketched on
https://github.com/anthropics/claude-code/issues/59248 . This is our own
implementation of that idea.

macOS only (paths and the SessionStart hook target ~/.claude). Standard library
only. See NOTES.md → "Strategy: prevention vs. restoration".
"""

from __future__ import annotations

__version__ = "0.1.0"

# Cheap pre-filter for the substantial-vs-noise *reporting* split (never gates
# the backup itself — every grown file is copied regardless). Growth above this
# many bytes is taken as substantial without inspection: real exchanges are
# large and parsing them would be wasted work. Only growth at or below this is
# worth opening up to check *what* the new records actually are (see
# growth_is_only_bookkeeping). Set generously: being wrong-high just means we
# parse a tail we didn't strictly need to — a few milliseconds, no risk. The
# real judgment is by record type, not this number. See NOTES.md → bookkeeping-bug.
SUBSTANTIAL_GROWTH_BYTES = 2048

# JSONL record `type`s that Claude re-appends as bookkeeping — unchanged — on
# every Desktop open/quit and on CLI --continue/--resume, without checking they
# duplicate the current value. Growth made up *only* of these is churn, not new
# conversation. Anything else (notably `user`/`assistant`) is real content.
BOOKKEEPING_RECORD_TYPES = frozenset(
    {"custom-title", "ai-title", "mode", "permission-mode", "last-prompt"}
)

import argparse
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


# -------- locations --------


def projects_root(home: Path) -> Path:
    return home / ".claude" / "projects"


def backup_root(home: Path) -> Path:
    # "code" not "session": scopes this to Claude Code, not Desktop's store.
    return home / ".claude-code-backups"


def backup_projects_root(home: Path) -> Path:
    return backup_root(home) / "projects"


def manifest_path(home: Path) -> Path:
    return backup_root(home) / "manifest.json"


def settings_path(home: Path) -> Path:
    return home / ".claude" / "settings.json"


# The command the SessionStart hook runs. We point it at this script's own
# absolute path so there's a single artifact — no separately-installed shell
# script to drift out of sync. `install` resolves the real path at install time.
def hook_command(script_path: Path) -> str:
    return f"{sys.executable} {script_path} backup"


# -------- shell helpers --------


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"warn: {msg}", file=sys.stderr)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -------- manifest --------


@dataclass
class ManifestEntry:
    bytes: int
    src_mtime: float
    backed_up_at: str  # ISO-8601 UTC


def load_manifest(home: Path) -> dict[str, ManifestEntry]:
    """
    Read manifest.json into {rel_path: ManifestEntry}. rel_path is relative to
    the backup *projects* root (e.g. "-Users-you-projects-foo/abc.jsonl").
    A missing or corrupt manifest reads as empty — the next backup pass rebuilds
    entries for whatever's on disk, and `status` reports the gap.
    """
    p = manifest_path(home)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        warn(f"could not read manifest {p}: {e}; treating as empty")
        return {}
    out: dict[str, ManifestEntry] = {}
    for rel, d in raw.get("files", {}).items():
        try:
            out[rel] = ManifestEntry(
                bytes=int(d["bytes"]),
                src_mtime=float(d["src_mtime"]),
                backed_up_at=str(d["backed_up_at"]),
            )
        except (KeyError, TypeError, ValueError):
            # Skip an unparseable row rather than abort the whole manifest.
            continue
    return out


def save_manifest(home: Path, entries: dict[str, ManifestEntry], last_run: str) -> None:
    """Atomically rewrite manifest.json. `last_run` is the wall-clock of this pass."""
    p = manifest_path(home)
    payload = {
        "version": 1,
        "last_run": last_run,
        "files": {
            rel: {
                "bytes": e.bytes,
                "src_mtime": e.src_mtime,
                "backed_up_at": e.backed_up_at,
            }
            for rel, e in sorted(entries.items())
        },
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, p)


def manifest_last_run(home: Path) -> str | None:
    p = manifest_path(home)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text()).get("last_run")
    except (OSError, json.JSONDecodeError):
        return None


# -------- backup engine --------


@dataclass
class BackupResult:
    copied: int = 0
    copied_subagents: int = 0   # subset of `copied` that are subagent fragments
    substantial: int = 0        # subset of `copied`: grew by > SUBSTANTIAL_GROWTH_BYTES
    skipped: int = 0
    bytes_copied: int = 0
    failed: int = 0


def is_subagent(rel: str) -> bool:
    """
    True if a rel path (relative to the projects root) is a subagent fragment
    rather than a top-level conversation transcript.

    Parent conversations live at <project>/<uuid>.jsonl (2 path segments).
    Subagent fragments live deeper, at <project>/<uuid>/subagents/agent-*.jsonl.
    The backup keeps both, but counting them separately stops "80 transcripts"
    from reading as "80 conversations you forgot about" — most of the surplus is
    subagent calls, not lost chats.
    """
    return rel.count("/") > 1


def growth_is_only_bookkeeping(src: Path, prev_bytes: int) -> bool:
    """
    Read only the records appended since the last backup (the bytes past
    `prev_bytes`) and return True iff every one of them is a bookkeeping record
    type — i.e. the file "grew" but gained no real conversation.

    This is the judge behind the substantial-vs-noise report. We seek to
    prev_bytes so we parse just the appended tail, not the whole file. Conserva-
    tive on uncertainty: any parse failure, any unknown/real record type, or an
    empty tail all return False (treat as substantial), so we never *quietly*
    under-report something that might be real content. The cost of a False here
    is only a louder status line; never lost data.
    """
    try:
        with src.open("rb") as f:
            f.seek(prev_bytes)
            tail = f.read()
    except OSError:
        return False
    text = tail.decode("utf-8", errors="replace")
    saw_record = False
    # The seek may land mid-line if the previous record didn't end exactly at
    # prev_bytes; a partial leading fragment just parses as junk → False, which
    # is the safe (substantial) direction.
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return False
        if rec.get("type") not in BOOKKEEPING_RECORD_TYPES:
            return False
        saw_record = True
    return saw_record


def growth_is_substantial(src: Path, prev: "ManifestEntry | None") -> bool:
    """
    Did `src` gain real conversation since its last backup (vs. only bookkeeping
    churn)? Gate then judge: a brand-new file or growth above
    SUBSTANTIAL_GROWTH_BYTES is substantial without inspection (cheap, common
    path); only small growth on an existing file is parsed to see whether the
    appended records are all bookkeeping. Shared by run_backup and cmd_status so
    the two never disagree on what counts as substantial.
    """
    if prev is None:
        return True  # brand-new file: new content by definition
    growth = src.stat().st_size - prev.bytes
    if growth > SUBSTANTIAL_GROWTH_BYTES:
        return True
    return not growth_is_only_bookkeeping(src, prev.bytes)


def copy_preserving_mtime(src: Path, dst: Path) -> None:
    """
    Copy src → dst preserving mtime. mtime preservation is load-bearing: Claude
    Code's cleanup keys deletion on filesystem mtime, so a backup restored with a
    fresh `now` mtime would look current to *us* but a restore of it later must
    carry the real mtime forward. We keep the source mtime on the backup copy so
    the chain is correct end to end. (shutil.copy2 copies data + stat, including
    mtime; we don't touch atime/mtime afterward.)
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def run_backup(home: Path, verbose: bool) -> BackupResult:
    """
    One copy-on-grow pass over ~/.claude/projects/**/*.jsonl.

    A file is copied when its backup is missing or the live file is larger than
    what the manifest records. Append-only JSONLs mean "larger" == "more
    complete", so this is lossless. We trust the manifest's recorded size as the
    high-water mark rather than re-stat'ing the backup copy each run — cheaper,
    and it means a backup file that itself got mangled still gets refreshed on
    the next growth.
    """
    src_root = projects_root(home)
    result = BackupResult()
    if not src_root.is_dir():
        warn(f"no Claude projects dir at {src_root}; nothing to back up")
        # Still write a manifest so `status` can distinguish "never ran" from
        # "ran, found nothing".
        save_manifest(home, load_manifest(home), now_iso())
        return result

    manifest = load_manifest(home)
    dst_root = backup_projects_root(home)

    for src in sorted(src_root.rglob("*.jsonl")):
        rel = src.relative_to(src_root).as_posix()
        try:
            st = src.stat()
        except OSError:
            # File vanished mid-walk (cleanup racing us); skip it.
            continue
        prev = manifest.get(rel)
        # Copy if no record, no backup file on disk, or the live file grew.
        dst = dst_root / rel
        if prev is not None and prev.bytes >= st.st_size and dst.is_file():
            result.skipped += 1
            continue
        try:
            copy_preserving_mtime(src, dst)
        except OSError as e:
            warn(f"failed to back up {rel}: {e}")
            result.failed += 1
            continue
        manifest[rel] = ManifestEntry(
            bytes=st.st_size,
            src_mtime=st.st_mtime,
            backed_up_at=now_iso(),
        )
        result.copied += 1
        if is_subagent(rel):
            result.copied_subagents += 1
        if growth_is_substantial(src, prev):
            result.substantial += 1
        result.bytes_copied += st.st_size
        if verbose:
            print(f"  backed up {rel} ({st.st_size} bytes)")

    save_manifest(home, manifest, now_iso())
    return result


# -------- settings.json hook merge --------


def load_settings(home: Path) -> dict:
    p = settings_path(home)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        die(f"could not parse {p}: {e}. Fix or remove it, then re-run install.")
    if not isinstance(data, dict):
        die(f"{p} is not a JSON object; refusing to modify it.")
    return data


def save_settings(home: Path, data: dict) -> None:
    p = settings_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, p)


def _our_command_marker() -> str:
    """
    Substring that identifies *our* hook regardless of which python/path it was
    installed with. Matching on the script basename + verb lets uninstall and
    status find the hook even if sys.executable changed between runs.
    """
    return "backup_claude_history.py backup"


def find_our_hook(data: dict) -> bool:
    """True iff a SessionStart hook running our backup command is present."""
    for group in data.get("hooks", {}).get("SessionStart", []):
        for h in group.get("hooks", []):
            if h.get("type") == "command" and _our_command_marker() in h.get("command", ""):
                return True
    return False


def install_hook(home: Path, script_path: Path) -> str:
    """
    Merge a SessionStart hook into settings.json without clobbering existing
    hooks. Returns a human-readable status string. Idempotent: a second install
    is a no-op.

    settings.json shape:
      { "hooks": { "SessionStart": [ { "hooks": [ {type, command}, ... ] } ] } }
    We append our command into the first SessionStart group (creating the
    structure if absent), rather than adding a parallel group — keeps the file
    tidy and matches how Claude Code itself nests them.
    """
    data = load_settings(home)
    if find_our_hook(data):
        return "already installed"

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        die("settings.json `hooks` is not an object; refusing to modify it.")
    session_start = hooks.setdefault("SessionStart", [])
    if not isinstance(session_start, list):
        die("settings.json `hooks.SessionStart` is not a list; refusing to modify it.")

    entry = {"type": "command", "command": hook_command(script_path)}
    if session_start and isinstance(session_start[0], dict) and isinstance(
        session_start[0].get("hooks"), list
    ):
        session_start[0]["hooks"].append(entry)
    else:
        session_start.append({"hooks": [entry]})

    save_settings(home, data)
    return "installed"


def uninstall_hook(home: Path) -> str:
    """Remove our SessionStart hook. Leaves everyone else's hooks untouched."""
    data = load_settings(home)
    if not find_our_hook(data):
        return "not installed"

    changed = False
    groups = data.get("hooks", {}).get("SessionStart", [])
    for group in groups:
        hlist = group.get("hooks", [])
        kept = [
            h for h in hlist
            if not (h.get("type") == "command"
                    and _our_command_marker() in h.get("command", ""))
        ]
        if len(kept) != len(hlist):
            group["hooks"] = kept
            changed = True

    # Prune now-empty groups, then an empty SessionStart list, then empty hooks —
    # so uninstall leaves the file as clean as it found it.
    groups = [g for g in groups if g.get("hooks")]
    if groups:
        data["hooks"]["SessionStart"] = groups
    else:
        data["hooks"].pop("SessionStart", None)
    if not data.get("hooks"):
        data.pop("hooks", None)

    if changed:
        save_settings(home, data)
        return "uninstalled"
    return "not installed"


# -------- status & list --------


def fmt_age(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - then
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.0f}{unit}" if unit == "B" else f"{f:.1f}{unit}"
        f /= 1024
    return f"{n}B"


def cmd_status(home: Path, script_path: Path) -> int:
    """Report: hook installed? last run? coverage vs. live? anything stale?"""
    data = load_settings(home)
    installed = find_our_hook(data)
    last_run = manifest_last_run(home)
    manifest = load_manifest(home)

    print("backup_claude_history status")
    print(f"  hook installed:  {'yes' if installed else 'NO — run: install'}")
    print(f"  last backup run: {fmt_age(last_run)}" + (f"  ({last_run})" if last_run else ""))
    print(f"  backup root:     {backup_root(home)}")
    tracked_subagents = sum(1 for rel in manifest if is_subagent(rel))
    tracked_convos = len(manifest) - tracked_subagents
    print(f"  files tracked:   {len(manifest)} "
          f"({tracked_convos} conversation(s) + {tracked_subagents} subagent fragment(s))")
    total = sum(e.bytes for e in manifest.values())
    print(f"  backed-up size:  {_human_bytes(total)}")

    # Coverage check: live JSONLs the manifest doesn't cover, or that have grown
    # past what we last captured. This is the silent-failure detector.
    src_root = projects_root(home)
    missing = 0          # live files with no backup at all — genuine gap
    stale_real = 0       # grew past the noise threshold — likely real new content
    stale_noise = 0      # grew, but only a little — likely Desktop bookkeeping
    live_convos = 0
    live_subagents = 0
    if src_root.is_dir():
        for src in src_root.rglob("*.jsonl"):
            rel = src.relative_to(src_root).as_posix()
            if is_subagent(rel):
                live_subagents += 1
            else:
                live_convos += 1
            try:
                size = src.stat().st_size
            except OSError:
                continue
            prev = manifest.get(rel)
            if prev is None:
                missing += 1
            elif size > prev.bytes:
                if growth_is_substantial(src, prev):
                    stale_real += 1
                else:
                    stale_noise += 1
    print(f"  live transcripts: {live_convos + live_subagents} "
          f"({live_convos} conversation(s) + {live_subagents} subagent fragment(s))")
    # `missing` and substantial growth are real coverage gaps worth a ⚠ and a
    # nudge to run backup. Minor growth alone is reported calmly, without alarm —
    # it's almost always Desktop bookkeeping churn, and it's already backed up.
    if missing or stale_real:
        print()
        if missing:
            print(f"  ⚠ {missing} live transcript(s) not yet backed up")
        if stale_real:
            print(f"  ⚠ {stale_real} live transcript(s) have new content since last backup")
        print("    Run `backup` (or just start a Claude Code session if the hook is installed).")
        if stale_noise:
            print(f"  ({stale_noise} more grew by a trivial amount — likely Claude "
                  f"Desktop bookkeeping, not new messages.)")
    elif stale_noise:
        print()
        print(f"  ✓ all conversations backed up "
              f"({stale_noise} had trivial bookkeeping-only growth — nothing to worry about)")
    elif installed and last_run:
        print()
        print("  ✓ all live transcripts are backed up")

    return 0


def cmd_list(home: Path) -> int:
    """List backed-up transcripts grouped by project, newest project first."""
    manifest = load_manifest(home)
    if not manifest:
        print("No backups yet. Run `backup`, or `install` the SessionStart hook.")
        return 0

    # Group rel paths (<project>/<uuid>.jsonl) by project.
    by_project: dict[str, list[tuple[str, ManifestEntry]]] = {}
    for rel, e in manifest.items():
        proj, _, name = rel.partition("/")
        if not name:  # malformed entry; skip
            continue
        by_project.setdefault(proj, []).append((name, e))

    # Order projects by most-recently-backed-up file within them.
    def proj_recency(proj: str) -> str:
        return max(e.backed_up_at for _, e in by_project[proj])

    print(f"Backed-up transcripts ({backup_root(home)})")
    for proj in sorted(by_project, key=proj_recency, reverse=True):
        files = sorted(by_project[proj], key=lambda t: t[1].backed_up_at, reverse=True)
        total = sum(e.bytes for _, e in files)
        print()
        print(f"  {proj}  ({len(files)} file(s), {_human_bytes(total)})")
        for name, e in files:
            print(f"    {name}  {_human_bytes(e.bytes):>8}  {fmt_age(e.backed_up_at)}")
    return 0


# -------- argv & main --------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="backup_claude_history.py",
        description="Forward backup of ~/.claude/projects/ Claude Code transcripts "
                    "(macOS). The everyday prevention path; restore_claude_code.py "
                    "stays the Time Machine failsafe.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    # --verbose is shared via a parent parser so it's accepted both before the
    # verb (`-v backup`) and after it (`backup -v`) — the latter is what users
    # reach for first, and a top-level-only flag would reject it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--verbose", "-v", action="store_true",
                        help="Print a line per file backed up.")

    sub = p.add_subparsers(dest="verb", required=True)
    sub.add_parser("backup", parents=[common],
                   help="Run one copy-on-grow backup pass (what the hook calls).")
    sub.add_parser("install", parents=[common],
                   help="Install the SessionStart backup hook (non-clobbering).")
    sub.add_parser("uninstall", parents=[common],
                   help="Remove the SessionStart backup hook.")
    sub.add_parser("status", parents=[common],
                   help="Is the hook installed, when did backup last run, anything stale?")
    sub.add_parser("list", parents=[common],
                   help="List backed-up transcripts by project.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    home = Path.home()
    script_path = Path(__file__).resolve()

    if args.verb == "backup":
        r = run_backup(home, args.verbose)
        # Quiet by default (the hook runs this on every session start). Lead with
        # *substantial* backups — files with real new content. Minor growth (the
        # rest) is almost always Claude Desktop re-appending unchanged bookkeeping
        # records on every open/quit, which would otherwise flood this line with
        # re-backups of idle chats. Both are still backed up; we just don't shout
        # about the noise. See SUBSTANTIAL_GROWTH_BYTES.
        noise = r.copied - r.substantial
        if r.substantial or r.failed:
            print(f"backup: {r.substantial} updated with new content "
                  f"({_human_bytes(r.bytes_copied)})"
                  + (f", {noise} minor (likely Claude Desktop bookkeeping)" if noise else "")
                  + f", {r.skipped} unchanged"
                  + (f", {r.failed} failed" if r.failed else ""))
        elif noise:
            # Nothing substantial — only churn. Stay calm and quiet about it.
            print(f"backup: {noise} chat(s) updated with minor changes only "
                  f"(likely Claude Desktop bookkeeping, not new messages)")
        return 1 if r.failed else 0

    if args.verb == "install":
        status = install_hook(home, script_path)
        print(f"SessionStart hook: {status}")
        if status == "installed":
            print(f"  command: {hook_command(script_path)}")
            print(f"  settings: {settings_path(home)}")
            print("  New Claude Code sessions will now back up transcripts on launch.")
            print("  Run `backup` once now to capture what's already on disk.")
        return 0

    if args.verb == "uninstall":
        status = uninstall_hook(home)
        print(f"SessionStart hook: {status}")
        if status == "uninstalled":
            print(f"  Existing backups under {backup_root(home)} are left in place.")
        return 0

    if args.verb == "status":
        return cmd_status(home, script_path)

    if args.verb == "list":
        return cmd_list(home)

    die(f"unknown verb: {args.verb}")  # unreachable; argparse enforces choices


if __name__ == "__main__":
    sys.exit(main())
