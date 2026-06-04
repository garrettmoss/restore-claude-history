#!/usr/bin/env python3
"""
restore_claude_desktop.py

Repair Claude Desktop session metadata so the UI stops showing
"Session not found on disk" / "Message not found on disk" for transcripts
that are still on disk under ~/.claude/projects/.

v1 scope: Mode A surgical edit only. For each broken metadata file in
~/Library/Application Support/Claude/claude-code-sessions/<acct>/<org>/local_*.json
that's missing `cliSessionId`, find the matching JSONL by createdAt ↔ first-record
timestamp, write `cliSessionId = <JSONL UUID>`, and remove `transcriptUnavailable`.
Leaves every other field untouched. Desktop re-bloats the file on next load but
preserves the field we added.

Mode A snapshot-restore fallback (for ambiguous matches) and Mode B (JSONL
restore from snapshots) are out of scope for v1 — they're reported but not
acted on. See TODO.md "Claude Desktop session recovery".

macOS only. Requires Claude Desktop to be fully quit (Cmd-Q) before any edits.
See NOTES.md → "Claude Desktop session recovery — failure-mode taxonomy".
"""

from __future__ import annotations

__version__ = "0.1.0"

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# -------- types --------


@dataclass
class MetaFile:
    """One Desktop session metadata file (`local_*.json`)."""

    path: Path
    data: dict
    project_cwd: str          # from data['cwd']; the live working dir this session ran in
    session_id: str           # from data['sessionId']; e.g. "local_ece5671d-..."
    title: str = ""
    is_archived: bool = False


@dataclass
class JsonlCandidate:
    """A JSONL on disk that might be the transcript for a given MetaFile."""

    path: Path
    first_timestamp_ms: int   # ms since epoch, parsed from the first record's `timestamp`
    uuid: str                 # filename minus .jsonl


@dataclass
class Diagnosis:
    """What's wrong (or right) with one MetaFile, and what to do about it."""

    meta: MetaFile
    mode: str                 # "healthy" | "mode-a" | "mode-b" | "mode-a-ambiguous" | "unknown"
    matched_jsonl: JsonlCandidate | None = None
    match_delta_seconds: float | None = None
    ambiguous_candidates: list[JsonlCandidate] = field(default_factory=list)
    note: str = ""            # human-readable detail for the report


# -------- shell helpers --------


def die(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg: str) -> None:
    print(f"warn: {msg}", file=sys.stderr)


# -------- preflight --------


def desktop_is_running() -> bool:
    """
    True iff the main Claude Desktop app process is alive. The chrome-native-host
    browser-extension helper stays alive separately and does NOT touch session
    files, so we deliberately don't false-positive on it.
    """
    try:
        r = subprocess.run(
            ["pgrep", "-f", "Claude.app/Contents/MacOS"],
            capture_output=True, text=True, check=False,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except FileNotFoundError:
        # `pgrep` should exist on every macOS; if not, assume safe and continue.
        return False


# -------- path helpers --------


def encoded_project_dir(cwd: str) -> str:
    """
    Map an absolute cwd (e.g. /Users/foo/projects/bar) to Claude's encoded
    project-dir name (e.g. -Users-foo-projects-bar). The replacement of '/'
    with '-' is what produces the leading hyphen — don't add one yourself.
    """
    return cwd.replace("/", "-")


def desktop_sessions_root(home: Path) -> Path:
    return home / "Library" / "Application Support" / "Claude" / "claude-code-sessions"


def projects_root(home: Path) -> Path:
    return home / ".claude" / "projects"


# -------- metadata enumeration --------


def load_meta(path: Path) -> MetaFile | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        warn(f"could not parse {path}: {e}")
        return None
    cwd = data.get("cwd", "")
    sid = data.get("sessionId", "")
    if not cwd or not sid:
        warn(f"{path}: missing cwd or sessionId; skipping")
        return None
    return MetaFile(
        path=path,
        data=data,
        project_cwd=cwd,
        session_id=sid,
        title=data.get("title", ""),
        is_archived=bool(data.get("isArchived", False)),
    )


def enumerate_meta_files(home: Path, project_filter: str | None) -> list[MetaFile]:
    """
    Walk all `local_*.json` files under the sessions root. If `project_filter`
    is set, restrict to files whose `cwd` encodes to that name.
    """
    root = desktop_sessions_root(home)
    if not root.is_dir():
        die(f"Claude Desktop sessions dir not found at {root}. "
            f"Is Claude Desktop installed on this machine?")
    metas: list[MetaFile] = []
    # Layout: <root>/<acct-uuid>/<org-uuid>/local_*.json
    for f in sorted(root.glob("*/*/local_*.json")):
        m = load_meta(f)
        if m is None:
            continue
        if project_filter is not None:
            if encoded_project_dir(m.project_cwd) != project_filter:
                continue
        metas.append(m)
    return metas


# -------- JSONL matching --------


def parse_jsonl_first_timestamp_ms(path: Path) -> int | None:
    """
    Read the first non-blank line of `path` and return its `timestamp` as ms
    since epoch. Returns None on any parse failure (bad JSON, no timestamp,
    unreadable file). JSONLs are append-only logs so the first record is the
    earliest — which is what `createdAt` in the metadata corresponds to.
    """
    try:
        with path.open("r", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    return None
                ts = rec.get("timestamp")
                if not ts:
                    return None
                # ISO-8601 with trailing 'Z'. fromisoformat in py3.11+ handles 'Z';
                # for older 3.x we substitute +00:00 defensively.
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    return None
                return int(dt.timestamp() * 1000)
    except OSError:
        return None
    return None


def jsonl_candidates_for(meta: MetaFile, home: Path) -> list[JsonlCandidate]:
    """List all JSONLs in this metadata's encoded project dir, with first-record timestamps."""
    proj = projects_root(home) / encoded_project_dir(meta.project_cwd)
    if not proj.is_dir():
        return []
    out: list[JsonlCandidate] = []
    for p in sorted(proj.glob("*.jsonl")):
        ts = parse_jsonl_first_timestamp_ms(p)
        if ts is None:
            continue
        out.append(JsonlCandidate(
            path=p,
            first_timestamp_ms=ts,
            uuid=p.stem,
        ))
    return out


def pick_match(
    meta: MetaFile,
    candidates: list[JsonlCandidate],
    tolerance_seconds: float,
) -> tuple[JsonlCandidate | None, float | None, list[JsonlCandidate]]:
    """
    Find the best createdAt-aligned JSONL for `meta`.

    Returns (best, delta_seconds, ambiguous). `best` is None if no candidate
    falls within `tolerance_seconds`. `ambiguous` is the full list of candidates
    inside the tolerance window when there are 2+ — caller treats that as
    refuse-to-act-on rather than guessing.
    """
    created_ms = meta.data.get("createdAt")
    if not isinstance(created_ms, int):
        return None, None, []
    tol_ms = tolerance_seconds * 1000.0
    in_window = [c for c in candidates
                 if abs(c.first_timestamp_ms - created_ms) <= tol_ms]
    if not in_window:
        return None, None, []
    if len(in_window) > 1:
        return None, None, in_window
    best = in_window[0]
    delta = abs(best.first_timestamp_ms - created_ms) / 1000.0
    return best, delta, []


# -------- diagnosis --------


def diagnose(meta: MetaFile, home: Path, tolerance_seconds: float) -> Diagnosis:
    has_cli = "cliSessionId" in meta.data and meta.data["cliSessionId"]
    tu = meta.data.get("transcriptUnavailable", False)

    if has_cli and not tu:
        return Diagnosis(meta=meta, mode="healthy")

    # Broken in some way. Distinguish Mode A vs Mode B.
    candidates = jsonl_candidates_for(meta, home)
    if not candidates:
        # Project dir empty or missing — Mode B (full content loss for this session).
        # We don't try to map session_id → JSONL UUID here because (a) Desktop's
        # session_id is `local_<uuid>`, not the JSONL UUID, and (b) absence of
        # any JSONL in the project dir is sufficient evidence either way.
        return Diagnosis(
            meta=meta,
            mode="mode-b",
            note="no JSONLs in project dir; transcript restore needed (out of scope for v1)",
        )

    best, delta, ambiguous = pick_match(meta, candidates, tolerance_seconds)
    if best is not None:
        return Diagnosis(
            meta=meta,
            mode="mode-a",
            matched_jsonl=best,
            match_delta_seconds=delta,
        )
    if ambiguous:
        return Diagnosis(
            meta=meta,
            mode="mode-a-ambiguous",
            ambiguous_candidates=ambiguous,
            note=f"{len(ambiguous)} JSONLs within {tolerance_seconds:.0f}s of createdAt; "
                 f"snapshot-restore fallback needed (out of scope for v1)",
        )
    return Diagnosis(
        meta=meta,
        mode="unknown",
        note="no JSONL in project dir matched createdAt within tolerance",
    )


# -------- mutation --------


def backup_sessions_dir(home: Path) -> Path:
    """Snapshot the entire sessions root to /tmp before we touch anything."""
    src = desktop_sessions_root(home)
    dst = Path(f"/tmp/claude-code-sessions.backup-{int(time.time())}")
    if dst.exists():
        die(f"refusing to overwrite existing backup at {dst}")
    shutil.copytree(src, dst)
    return dst


def apply_mode_a_fix(diag: Diagnosis, verbose: bool) -> bool:
    """
    Surgical edit: add cliSessionId (from matched JSONL UUID), remove
    transcriptUnavailable. Leave every other field alone. Atomic-replace
    via a tmp file in the same dir. Returns True on success.
    """
    assert diag.mode == "mode-a" and diag.matched_jsonl is not None
    meta_path = diag.meta.path
    data = dict(diag.meta.data)
    data["cliSessionId"] = diag.matched_jsonl.uuid
    data.pop("transcriptUnavailable", None)
    tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    try:
        # Preserve compact-ish JSON formatting close to what Desktop writes.
        # Desktop's own format is compact (no indent) but it tolerates indented
        # JSON fine — we keep indent=2 for readability of any post-edit diff.
        tmp.write_text(json.dumps(data, indent=2))
        os.chmod(tmp, 0o600)
        os.replace(tmp, meta_path)
    except OSError as e:
        warn(f"failed to write {meta_path}: {e}")
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    if verbose:
        print(f"  fixed: {meta_path.name} "
              f"(cliSessionId={diag.matched_jsonl.uuid}, "
              f"match delta={diag.match_delta_seconds:.2f}s)")
    return True


# -------- reporting --------


def short_id(session_id: str) -> str:
    """`local_ece5671d-82d5-...` → `ece5671d`."""
    s = session_id
    if s.startswith("local_"):
        s = s[len("local_"):]
    return s.split("-", 1)[0]


def print_report(diagnoses: list[Diagnosis]) -> None:
    """Per-session line + per-mode summary. Goes to stdout."""
    by_project: dict[str, list[Diagnosis]] = {}
    for d in diagnoses:
        by_project.setdefault(d.meta.project_cwd, []).append(d)

    for cwd in sorted(by_project):
        print(f"\n{cwd}")
        for d in by_project[cwd]:
            tag = d.mode
            arch = " [archived]" if d.meta.is_archived else ""
            title = (d.meta.title or "(untitled)")[:60]
            line = f"  {tag:<20} {short_id(d.meta.session_id):<8}  {title}{arch}"
            if d.mode == "mode-a" and d.matched_jsonl is not None:
                line += f"  ← {d.matched_jsonl.uuid} (Δ{d.match_delta_seconds:.2f}s)"
            elif d.note:
                line += f"  ({d.note})"
            print(line)

    counts: dict[str, int] = {}
    for d in diagnoses:
        counts[d.mode] = counts.get(d.mode, 0) + 1
    print()
    print("summary:")
    for mode in ("healthy", "mode-a", "mode-a-ambiguous", "mode-b", "unknown"):
        if mode in counts:
            print(f"  {mode}: {counts[mode]}")


# -------- argv & main --------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="restore_claude_desktop.py",
        description="Repair Claude Desktop session metadata "
                    "(\"Session not found on disk\") — macOS only.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--dry-run", action="store_true",
                   help="Report only; do not modify any files.")
    p.add_argument("--no-backup", action="store_true",
                   help="Skip the pre-apply backup of the sessions dir to /tmp. "
                        "Off by default — we back up unless you opt out.")
    p.add_argument("--project", default=None,
                   help="Limit to one encoded project dir name "
                        "(e.g. -Users-you-projects-foo). Starts with '-' — pass with = "
                        "(--project=-Users-...) to avoid argparse treating it as a flag.")
    p.add_argument("--match-tolerance", type=float, default=60.0,
                   help="Max seconds between metadata createdAt and JSONL first-record "
                        "timestamp for a confident match (default: 60).")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Print one line per applied fix.")
    return p.parse_args()


def main() -> int:
    # Pre-rewrite `--project FOO` → `--project=FOO` so argparse doesn't reject
    # encoded names that start with '-'. Same pattern as restore_claude_history.py.
    argv = sys.argv[1:]
    rewritten: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--project" and i + 1 < len(argv) and argv[i + 1].startswith("-"):
            rewritten.append(f"--project={argv[i + 1]}")
            i += 2
            continue
        rewritten.append(argv[i])
        i += 1
    sys.argv = [sys.argv[0]] + rewritten

    args = parse_args()
    home = Path.home()

    # Preflight: Desktop must be quit. True in both dry-run and apply modes —
    # otherwise users will dry-run with Desktop open, see clean output, then
    # forget to quit before applying. Same check, both modes, no surprises.
    if desktop_is_running():
        die("Claude Desktop is running. Quit it with Cmd-Q first, then re-run "
            "this script. (Desktop rewrites session metadata from in-memory state "
            "and will clobber any edits made while it's open.)")

    metas = enumerate_meta_files(home, args.project)
    if not metas:
        if args.project:
            die(f"No Desktop metadata files found for --project={args.project}.")
        die("No Desktop metadata files found. "
            "Is Claude Desktop installed and has it been launched at least once?")

    diagnoses = [diagnose(m, home, args.match_tolerance) for m in metas]
    print_report(diagnoses)

    fixable = [d for d in diagnoses if d.mode == "mode-a"]
    if not fixable:
        print()
        print("Nothing to fix in v1 scope (Mode A only).")
        return 0

    if args.dry_run:
        print()
        print(f"DRY RUN: would fix {len(fixable)} Mode A session(s). "
              f"Re-run without --dry-run to apply.")
        return 0

    # Apply.
    if not args.no_backup:
        backup_path = backup_sessions_dir(home)
        print()
        print(f"Backed up sessions dir to {backup_path}")

    print()
    fixed = 0
    for d in fixable:
        if apply_mode_a_fix(d, args.verbose):
            fixed += 1
    print()
    print(f"Fixed {fixed} of {len(fixable)} Mode A session(s). "
          f"Launch Claude Desktop to verify — transcripts should now load.")
    if fixed < len(fixable):
        print(f"{len(fixable) - fixed} session(s) failed to write — see warnings above.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
