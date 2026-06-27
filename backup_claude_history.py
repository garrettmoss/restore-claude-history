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

Verbs:
  backup     Run one copy-on-grow pass. This is what the SessionStart hook calls.
  install    Merge a SessionStart hook into ~/.claude/settings.json (non-clobbering).
  uninstall  Remove that hook again.
  status     Is the hook installed, when did backup last run, anything stale?
  list       What's backed up, grouped by project (shows each session's title).
  restore    Copy backed-up transcript(s) back into ~/.claude/projects/. Dry-run
             by default; --apply to write. (backup-v0.2.0, the restoration half.)

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

# Title recovery reads only the first few records of a transcript — titles and
# the first prompt live at the top. We cap by *record count*, not bytes: a
# single record can be enormous (an `<ide_opened_file>` user message can inline a
# multi-MB file), and a byte-cap would truncate mid-record and fail to parse the
# rest. LABEL_SCAN_RECORDS bounds the work; LABEL_MAX_LINE_BYTES skips any single
# line too big to be a title before we waste a json.loads on it (the first-prompt
# fallback reads such a line only as a truncated prefix). A miss here is cosmetic
# (we show the UUID), never a data problem.
LABEL_SCAN_RECORDS = 50
LABEL_MAX_LINE_BYTES = 64 * 1024

# Two distinct "no label" states, kept separate because they make different
# claims. EMPTY is a confident claim — we scanned the whole file and it holds no
# conversation, only file-history-snapshot / bookkeeping records (a session shell
# spun up but never chatted in; not a lost chat). NOT_FOUND is honest
# uncertainty — there *is* conversation, we just couldn't extract a title from
# what we scanned (it may sit past the cap). "(untitled)" would over-claim the
# latter. Both are still backed up; labeling is display-only.
EMPTY_SESSION_LABEL = "(empty — no messages)"
NO_TITLE_LABEL = "(title not found)"

import argparse
import json
import os
import re
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


def read_session_label(jsonl: Path) -> str | None:
    """
    Recover the human-readable title Claude shows in the VS Code / Desktop chat
    picker, reading it straight out of a transcript.

    Resolution mirrors the UI's own priority: a manual rename (`custom-title`)
    wins; otherwise the auto-generated `ai-title`; otherwise the first user
    prompt. Each title type can be re-appended (it's bookkeeping churn — see
    BOOKKEEPING_RECORD_TYPES), so we keep the *last* occurrence of each, which is
    the current value. Returns None only if the file is unreadable or has no
    usable label — callers fall back to the bare UUID.

    Reads line by line (not a byte-blob) so an oversized record can't truncate
    the read mid-line; bounded by LABEL_SCAN_RECORDS. See the constants above.
    """
    custom = ai = first_prompt = None
    saw_conversation = False   # any user/assistant record at all?
    reached_end = True         # did we scan the whole file (vs. hit the cap)?
    try:
        with jsonl.open("rb") as f:
            seen = 0
            for raw in f:
                if seen >= LABEL_SCAN_RECORDS:
                    reached_end = False
                    break
                if not raw.strip():
                    continue
                seen += 1
                # A line too big to be a title: don't parse it as JSON. It may
                # still be the first user message, so keep a truncated prefix for
                # the fallback, but skip the (expensive, pointless) full parse.
                if len(raw) > LABEL_MAX_LINE_BYTES:
                    saw_conversation = True  # only user/assistant records get big
                    if first_prompt is None:
                        first_prompt = _first_user_text(raw[:LABEL_MAX_LINE_BYTES])
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = rec.get("type")
                if t in ("user", "assistant"):
                    saw_conversation = True
                if t == "custom-title":
                    custom = rec.get("customTitle") or rec.get("title") or custom
                elif t == "ai-title":
                    ai = rec.get("aiTitle") or ai
                elif t == "user" and first_prompt is None:
                    first_prompt = _first_user_text(raw)
    except OSError:
        return None
    label = custom or ai or first_prompt
    if label:
        return " ".join(label.split())  # collapse newlines/runs of whitespace
    # No label. Distinguish a genuinely empty session (we scanned the whole file
    # and it held no conversation — just snapshots/bookkeeping) from a real chat
    # we simply couldn't title (e.g. content sits past the scan cap). Only the
    # former gets the reassuring "empty" tag; the latter falls back to the UUID.
    if reached_end and not saw_conversation:
        return EMPTY_SESSION_LABEL
    return None


def _strip_ide_prefix(text: str) -> str:
    """
    Drop a leading `<ide_opened_file>…</ide_opened_file>` note the VS Code
    extension prepends to a message — so the title is the user's actual prompt,
    not "The user opened the file …". The closing tag makes the cut unambiguous;
    we leave other injected prefixes (e.g. "Caveat: …") alone.
    """
    return re.sub(r"^\s*<ide_opened_file>.*?</ide_opened_file>\s*", "", text, count=1,
                  flags=re.DOTALL)


def _first_user_text(raw: bytes) -> str | None:
    """
    Pull the text out of a `user` record's raw JSON line. Tolerant: a truncated
    line (from the oversized-record path) won't parse, so fall back to a crude
    regex for the first text field. Returns None if nothing usable is found.
    """
    text = None
    try:
        rec = json.loads(raw)
        content = rec.get("message", {}).get("content")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        if isinstance(content, str) and content.strip():
            text = content.strip()
    except json.JSONDecodeError:
        # Widen past a possible <ide_opened_file>…</ide_opened_file> note (~200
        # chars) so its closing tag is captured and _strip_ide_prefix can cut it.
        m = re.search(rb'"text"\s*:\s*"([^"\\]{1,500})', raw)
        if m:
            text = m.group(1).decode("utf-8", errors="replace").strip()
    if not text:
        return None
    text = _strip_ide_prefix(text).strip()
    return text or None


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


def _fmt_age_secs(secs: int) -> str:
    """Render an age in seconds as a compact 's/m/h/d ago' string."""
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def fmt_age(iso: str | None) -> str:
    """Age of an ISO-8601 timestamp (e.g. manifest backed_up_at / last_run)."""
    if not iso:
        return "never"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return _fmt_age_secs(int((datetime.now(timezone.utc) - then).total_seconds()))


def fmt_age_epoch(ts: float | None) -> str:
    """
    Age of a float epoch mtime (e.g. a transcript's src_mtime). `list` uses this
    to show *last chat activity* — the time a user recognizes — rather than when
    our backup happened to copy the file. See cmd_list for why that distinction
    matters.
    """
    if ts is None:
        return "never"
    return _fmt_age_secs(int(time.time() - ts))


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

    # Order by the chat's own last-activity time (src_mtime), NOT backed_up_at.
    # backed_up_at is when *our script copied the file* — in a first full sweep
    # that's identical for every file, so showing it makes every chat read "2d
    # ago" and destroys the chronological ladder a user recognizes. src_mtime is
    # the transcript's real mtime (preserved on copy), which is exactly what the
    # VS Code / Desktop picker sorts by. backed_up_at stays in `status`, where
    # "when did the backup last run" is the point.
    def proj_recency(proj: str) -> float:
        return max(e.src_mtime for _, e in by_project[proj])

    print(f"Backed-up transcripts ({backup_root(home)})")
    for proj in sorted(by_project, key=proj_recency, reverse=True):
        files = sorted(by_project[proj], key=lambda t: t[1].src_mtime, reverse=True)
        total = sum(e.bytes for _, e in files)
        print()
        print(f"  {proj}  ({len(files)} file(s), {_human_bytes(total)})")
        # Header row, aligned to the columns below: a UUID is 36 chars, then the
        # right-justified size (>8) and last-activity age (>7), then the title.
        print(f"    {'SESSION':36}  {'SIZE':>8}  {'LAST USED':>9}  TITLE")
        for name, e in files:
            label = read_session_label(backup_projects_root(home) / proj / name) or NO_TITLE_LABEL
            title = f'  "{_truncate(label, 50)}"'
            uuid = name[: -len(".jsonl")] if name.endswith(".jsonl") else name
            print(f"    {uuid:36}  {_human_bytes(e.bytes):>8}  {fmt_age_epoch(e.src_mtime):>9}{title}")
    return 0


# -------- restore engine --------
#
# Restore reads *our own* backups (under ~/.claude-code-backups/) and copies
# transcripts back into ~/.claude/projects/. It is deliberately scoped to Claude
# Code CLI transcripts: the JSONL plus its real mtime is the complete state a CLI
# session needs. Claude Desktop's separate metadata (sessions-index.json,
# local_*.json) is a different beast and stays restore_claude_desktop.py's job —
# see repair_session_index() for the one deferred seam.
#
# Two safety rules carry the whole design:
#   1. Dry-run by default. Restore is destructive-adjacent; you opt in with
#      --apply. The preview names each session by title so you see what you're
#      about to touch.
#   2. Never shrink a live file. This is copy-on-grow run backwards: if the live
#      transcript is *larger* than the backup, you kept chatting after the last
#      backup, and overwriting would silently drop that tail. We refuse such a
#      target unless --force. (A live file the same size or smaller-but-present
#      is treated as already-restored / nothing-to-do.)


@dataclass
class RestorePlan:
    rel: str
    src: Path          # backup copy
    dst: Path          # live destination
    backup_bytes: int
    live_bytes: int | None   # None if no live file exists
    action: str        # "restore" | "skip-present" | "refuse-live-grew"
    label: str | None


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def resolve_targets(
    manifest: dict[str, "ManifestEntry"],
    *,
    session: str | None,
    project: str | None,
    want_all: bool,
) -> list[str]:
    """
    Turn a selector into the list of manifest rel-paths to restore.

    --session accepts either the canonical "<project>/<uuid>" rel-path (a direct
    manifest key) or a bare "<uuid>" we resolve across projects — refusing if the
    same UUID exists in more than one project (effectively impossible, but we
    don't guess). --project restores every transcript under one encoded project.
    --all restores everything backed up. Exactly one selector is required;
    argparse enforces that. die()s with a helpful message on no match.
    """
    if want_all:
        return sorted(manifest)

    if project is not None:
        prefix = project.rstrip("/") + "/"
        hits = sorted(r for r in manifest if r.startswith(prefix))
        if not hits:
            die(f"no backed-up transcripts under project {project!r}. "
                f"Run `list` to see projects.")
        return hits

    # --session
    sess = session.strip()
    if sess.endswith(".jsonl"):
        sess = sess[: -len(".jsonl")]
    if "/" in sess:  # canonical <project>/<uuid>
        rel = sess if sess.endswith(".jsonl") else sess + ".jsonl"
        if rel not in manifest:
            die(f"no backed-up transcript {rel!r}. Run `list` to see what's available.")
        return [rel]
    # bare UUID: find which project(s) hold it
    suffix = "/" + sess + ".jsonl"
    hits = sorted(r for r in manifest if r.endswith(suffix))
    if not hits:
        die(f"no backed-up transcript with session id {sess!r}. "
            f"Run `list` to see backed-up sessions.")
    if len(hits) > 1:
        joined = "\n  ".join(hits)
        die(f"session id {sess!r} exists in multiple projects — disambiguate with "
            f"--session <project>/<uuid>:\n  {joined}")
    return hits


def plan_restore(home: Path, rels: list[str], manifest: dict[str, "ManifestEntry"]) -> list[RestorePlan]:
    """Build a RestorePlan per target without touching anything. Pure inspection."""
    src_root = backup_projects_root(home)
    dst_root = projects_root(home)
    plans: list[RestorePlan] = []
    for rel in rels:
        src = src_root / rel
        dst = dst_root / rel
        backup_bytes = manifest[rel].bytes if rel in manifest else (
            src.stat().st_size if src.is_file() else 0
        )
        live_bytes = dst.stat().st_size if dst.is_file() else None
        if live_bytes is None:
            action = "restore"
        elif live_bytes > backup_bytes:
            action = "refuse-live-grew"
        else:
            # Live file present and no larger than the backup: already restored
            # (or backup is the high-water mark). Nothing to do unless --force.
            action = "skip-present"
        plans.append(RestorePlan(
            rel=rel, src=src, dst=dst,
            backup_bytes=backup_bytes, live_bytes=live_bytes,
            action=action, label=read_session_label(src),
        ))
    return plans


def repair_session_index(home: Path, rel: str) -> None:
    """
    Deferred seam (backup-v0.2.0 ships without metadata repair).

    Claude Desktop maintains a per-project sessions-index.json that lists which
    JSONLs should exist; a restored transcript missing from it is the @BasedGPT
    orphan risk (#62272) — re-deletable on the next cleanup sweep. But that file
    is a *Desktop* artifact: it's absent for CLI-only projects, where the JSONL
    with its preserved mtime is already the complete state. So there's nothing to
    repair in the case this script is scoped to, and fabricating an index the CLI
    never wrote would be inventing state.

    Left as a named no-op so the call site exists if a real Desktop-restore case
    ever lands here rather than in restore_claude_desktop.py. Do NOT wire it in
    pre-emptively (CLAUDE.md: don't pre-emptively refactor).
    """
    return None


def cmd_restore(
    home: Path,
    *,
    session: str | None,
    project: str | None,
    want_all: bool,
    apply: bool,
    force: bool,
    verbose: bool = False,
) -> int:
    """Restore backed-up transcript(s) into ~/.claude/projects/. Dry-run unless --apply."""
    manifest = load_manifest(home)
    if not manifest:
        die("no backups to restore from. Run `backup` first, or `list` to check.")

    rels = resolve_targets(manifest, session=session, project=project, want_all=want_all)
    plans = plan_restore(home, rels, manifest)

    print(f"{'Restoring' if apply else 'Would restore'} into {projects_root(home)}"
          + ("" if apply else "  (dry run — pass --apply to write)"))

    # Render: one row per top-level chat, grouped by project (long encoded path
    # printed once, same shape as `list`). Subagent fragments don't get their own
    # rows by default — a user restores a *chat*, not its sub-pieces; we just tag
    # the parent with a count. --verbose expands them (and the orphan bucket,
    # where the parent transcript itself wasn't backed up — e.g. data-of-being's
    # Mode-B losses, where the subagents are the only surviving files).
    # Verbosity changes *display only*; every selected file is still restored.
    to_write = 0
    refused = 0
    counts = {"restore": 0, "FORCE": 0, "REFUSE": 0, "skip": 0}

    def classify(p: RestorePlan) -> str:
        if p.action == "restore":
            return "restore"
        if p.action == "refuse-live-grew":
            return "FORCE" if force else "REFUSE"
        return "skip"

    def row(p: RestorePlan, sess: str, indent: str = "    ", suffix: str = "") -> None:
        title = f'"{_truncate(p.label or NO_TITLE_LABEL, 50)}"'
        live_desc = _human_bytes(p.live_bytes) if p.live_bytes is not None else "—"
        sizes = f"{_human_bytes(p.backup_bytes)}→{live_desc}"
        flag = "  ⚠ live larger" if p.action == "refuse-live-grew" else ""
        # `suffix` (e.g. the subagent count) goes after the title so it never
        # widens the fixed SESSION column and breaks alignment.
        print(f"{indent}{classify(p):7}  {sess:36}  {sizes:>17}  {title}{flag}{suffix}")

    by_project: dict[str, list[RestorePlan]] = {}
    for p in plans:
        by_project.setdefault(p.rel.split("/", 1)[0], []).append(p)

    for p in plans:  # tally actions once, across everything (incl. subagents)
        c = classify(p)
        counts[c] += 1
        if c in ("restore", "FORCE"):
            to_write += 1
        elif c == "REFUSE":
            refused += 1

    for proj in sorted(by_project):
        # Split this project's plans into top-level chats and subagents, and map
        # each subagent to its parent UUID (the segment before "/subagents/").
        tops: dict[str, RestorePlan] = {}   # uuid -> plan
        subs: dict[str, list[RestorePlan]] = {}  # parent-uuid -> [plans]
        for p in by_project[proj]:
            tail = p.rel[len(proj) + 1:]
            if "/subagents/" in tail:
                parent = tail.split("/subagents/", 1)[0]
                subs.setdefault(parent, []).append(p)
            else:
                tops[tail[: -len(".jsonl")] if tail.endswith(".jsonl") else tail] = p

        print()
        print(f"  {proj}")
        print(f"    {'ACTION':7}  {'SESSION':36}  {'BACKUP→LIVE':>17}  TITLE")
        for uuid in sorted(tops):
            child = subs.pop(uuid, [])
            suffix = f"  (+{len(child)} subagent(s))" if child and not verbose else ""
            row(tops[uuid], uuid, suffix=suffix)
            if verbose:
                for sp in sorted(child, key=lambda q: q.rel):
                    name = sp.rel.split("/subagents/", 1)[1]
                    name = name[: -len(".jsonl")] if name.endswith(".jsonl") else name
                    row(sp, "  └ " + name, indent="      ")
        # Subagents whose parent transcript isn't in the backup at all.
        orphans = [sp for plist in subs.values() for sp in plist]
        if orphans:
            print(f"    (orphaned subagent(s) — parent transcript not backed up: "
                  f"{len(orphans)})")
            if verbose:
                for sp in sorted(orphans, key=lambda q: q.rel):
                    name = sp.rel[len(proj) + 1:]
                    name = name[: -len(".jsonl")] if name.endswith(".jsonl") else name
                    row(sp, "  └ " + name, indent="      ")

    print()
    if refused and not force:
        print(f"  ⚠ {refused} refused: the live file has grown past the backup "
              f"(you kept chatting since the last backup).")
        print(f"    Overwriting would drop that newer content. Pass --force only if "
              f"you're sure you want the older backed-up version.")

    if not apply:
        if to_write:
            print(f"  {to_write} session(s) would be restored. Re-run with --apply to write.")
        else:
            print("  Nothing to restore.")
        return 0

    # --apply: do the writes.
    written = failed = 0
    for p in plans:
        if p.action == "skip-present":
            continue
        if p.action == "refuse-live-grew" and not force:
            continue
        try:
            copy_preserving_mtime(p.src, p.dst)
        except OSError as e:
            warn(f"failed to restore {p.rel}: {e}")
            failed += 1
            continue
        repair_session_index(home, p.rel)  # deferred no-op; see its docstring
        written += 1
        print(f"  restored {p.rel}")
    print()
    print(f"  restored {written} session(s)"
          + (f", {failed} failed" if failed else "")
          + ". mtime preserved; Claude Code's cleanup won't see them as 'just now'.")
    return 1 if failed else 0


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

    restore = sub.add_parser(
        "restore", parents=[common],
        help="Restore backed-up transcript(s) into ~/.claude/projects/ (dry-run by default).",
        description="Restore Claude Code CLI transcripts from your own backups. "
                    "Dry-run by default — pass --apply to write. Refuses to overwrite "
                    "a live file that has grown past the backup unless --force.",
    )
    sel = restore.add_mutually_exclusive_group(required=True)
    sel.add_argument("--session", metavar="<project>/<uuid> | <uuid>",
                     help="One session: canonical <project>/<uuid>, or a bare UUID "
                          "(refused if it exists in more than one project).")
    sel.add_argument("--project", metavar="<encoded-project>",
                     help="Every backed-up transcript under one encoded project dir.")
    sel.add_argument("--all", action="store_true",
                     help="Every transcript in the backup.")
    restore.add_argument("--apply", action="store_true",
                         help="Actually write. Without this, restore only previews.")
    restore.add_argument("--force", action="store_true",
                         help="Override the never-shrink guard and overwrite a live "
                              "file that is larger than the backup. Use with care.")

    # Encoded project names (and so <project>/<uuid> session keys) start with
    # '-', which argparse mistakes for a flag. Rewrite "--project FOO" ->
    # "--project=FOO" (same for --session) so users don't need the '=' syntax.
    # Same gotcha and fix as restore_claude_code.py.
    argv = sys.argv[1:]
    rewritten: list[str] = []
    i = 0
    while i < len(argv):
        if (argv[i] in ("--project", "--session")
                and i + 1 < len(argv) and argv[i + 1].startswith("-")):
            rewritten.append(f"{argv[i]}={argv[i + 1]}")
            i += 2
        else:
            rewritten.append(argv[i])
            i += 1
    return p.parse_args(rewritten)


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

    if args.verb == "restore":
        return cmd_restore(
            home,
            session=args.session,
            project=args.project,
            want_all=args.all,
            apply=args.apply,
            force=args.force,
            verbose=args.verbose,
        )

    die(f"unknown verb: {args.verb}")  # unreachable; argparse enforces choices


if __name__ == "__main__":
    sys.exit(main())
