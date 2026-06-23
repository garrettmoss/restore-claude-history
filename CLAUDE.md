# CLAUDE.md

Agent guidance for working in this repo. If you're an AI assistant: read this first, then NOTES.md for design rationale and TODO.md for open work.

## Project shape

A small toolkit of standalone Python scripts for recovering Claude chat data on macOS. Standard library only — no dependencies. macOS + APFS only. Each script is single-file and runs without the others; share substrate only when a second script actually needs it (don't pre-emptively refactor a common module).

- **`restore_claude_code.py`** — recover deleted Claude Code JSONL transcripts (`~/.claude/projects/<project>/*.jsonl`) from Time Machine and/or local APFS snapshots. (Renamed from `restore_claude_history.py` to make the Code-vs-Desktop axis explicit; the repo name stays `restore-claude-history`.)
- **`restore_claude_desktop.py`** — repair Claude Desktop session metadata when the UI shows "Session not found on disk" for transcripts that are still on disk. v1 handles Mode A (surgical edit of `local_*.json`); Mode A snapshot-restore fallback and Mode B (JSONL restore) are deferred. See NOTES.md for the failure-mode taxonomy.
- **`backup_claude_history.py`** — forward backup of `~/.claude/projects/` so recovery stops depending on Time Machine. **backup-v0.1.0 (prevention half) shipped:** `install`/`uninstall` a non-clobbering `SessionStart` hook, a copy-on-grow `backup` engine writing `~/.claude-code-backups/` + `manifest.json`, plus `status` and `list`. The restore half (a `restore` verb reading *our own* backups, with metadata repair) is backup-v0.2.0, not yet built. See TODO.md (roadmap) and NOTES.md (rationale + the "As built" section).

Docs are deliberately split:
- **README.md** — user-facing: what it does, how to run it.
- **NOTES.md** — design rationale, hand-verified recovery steps, gotchas, Claude Desktop failure-mode taxonomy.
- **TODO.md** — open work, publicity plan, follow-up projects.

(Two more docs are gitignored local scratch: `handoff.md` for live session state, `personal-notes.md` for GitHub drafts / prior-art / the publicity tracker.)

When you change behavior, update the relevant doc — usually NOTES.md (for new gotchas) or README.md (for user-visible changes). Don't create new top-level docs without checking; the set above is intended to be complete.

## Versioning

Each script is on its own version clock. Each script's in-file `__version__` constant MUST stay in sync with the latest matching prefixed git tag.

- **`restore_claude_code.py`** — tags `code-vX.Y.Z`. Current: `__version__ = "1.1.0"`. Historical tags `v1.0.0` / `v1.0.1` / `v1.1.0` (no prefix) refer to this script from before the rename and stay as-is. The prefixed lane starts at the next bump (so the next release is `code-v1.2.0`). The prefix is `code-`, not `history-`: it has to name the *script* to disambiguate from `desktop-`, and the repo is already named `restore-claude-history` so `history-` would just echo the repo. No `history-v*` tag was ever created.
- **`restore_claude_desktop.py`** — tags `desktop-vX.Y.Z`. Current: `__version__ = "0.1.0"`.
- **`backup_claude_history.py`** — tags `backup-vX.Y.Z`. Current: `__version__ = "0.1.0"`. New lane; first release is `backup-v0.1.0`.
- `push.followTags = true` is set, so annotated tags travel with `git push` automatically. Don't push tags separately.
- When bumping a script's version: edit its `__version__`, commit, tag the commit with the script's prefix (`git tag -a desktop-vX.Y.Z -m "..."` or `code-vX.Y.Z`), push. Both updates go in the same commit as the change they describe.
- Users run `python3 <script>.py --version` to report bugs. Tell them to mention which script and which version — "code 1.1.0" vs "desktop 0.1.0" vs "backup 0.1.0" are different release lanes now.

## Things that are easy to get wrong

### `restore_claude_code.py`

- **mtime preservation is load-bearing.** The script preserves the snapshot's original mtime on restored files. Do NOT rewrite mtimes to in-file message timestamps — that triggers the very cleanup bug we're working around. See the @ojura/#59248 gotcha in NOTES.md.
- **Encoded project names start with `-`.** Argparse rejects them as flags. The script pre-rewrites `--project FOO` → `--project=FOO`. Don't undo this. (Same gotcha applies to `restore_claude_desktop.py`.)
- **Don't touch macOS auto-mounted snapshots.** The script distinguishes between snapshots it mounted (cleanup unmounts them) and pre-existing macOS auto-mounts (left alone). The `owned_by_us` flag on the `Snapshot` dataclass exists for exactly this reason.
- **The TM drive is optional — `--source local` tests without it.** A missing TM drive only hard-exits under explicit `--source=tm`; the default `both` (and `local`) fall through to internal-disk APFS snapshots. Use `--source=local` (and `verify_restore.py --source=local`) to test drive-free. When you *do* test against the TM drive: Spotlight indexes mounted TM volumes aggressively, so keep runs short and unplug when done.

### `restore_claude_desktop.py`

- **`cliSessionId` is the single load-bearing field.** Mode A repair is "add `cliSessionId` matching the JSONL UUID, remove `transcriptUnavailable`, leave every other field alone." Don't expand the edit surface — Desktop re-writes the file on next load and the only thing it actually needs from us is that one field. See NOTES.md "Claude Desktop: session-recovery failure-mode taxonomy."
- **Desktop must be fully quit before editing.** The script refuses to run while `Claude.app/Contents/MacOS` is alive — Desktop rewrites session files from in-memory state within seconds and will clobber any edits. Don't false-positive on the `chrome-native-host` helper; it's a separate process and doesn't touch session files.
- **`VERIFIED_CLAUDE_DESKTOP_VERSION` is a compatibility footprint, not a ceiling.** Bump it (and re-verify end-to-end) when running against a newer Desktop release. If a future Desktop version breaks the recipe, the constant is the last known-working bisection target.
- **JSONL matching must refuse on ambiguity.** When two JSONLs fall inside the createdAt tolerance window, mark the session NEEDS REVIEW and don't guess. The snapshot-restore fallback (v0.2.0+) is the right answer for those cases.

### `backup_claude_history.py`

- **Copy-on-grow is the whole safety model — never gate the backup on anything but "did it grow."** A file is backed up iff it's missing from the backup or the live file is larger than the manifest's recorded size. Don't add cleverness to the *backup decision* (e.g. "skip if growth looks like noise") — that's how you lose a real short message. All classification cleverness lives in the *reporting* layer only.
- **mtime preservation is load-bearing here too.** `copy_preserving_mtime` uses `shutil.copy2` to carry the source mtime onto the backup. Don't restamp to `now` or to in-file message timestamps — same cleanup-bug trap as `restore_claude_code.py`. The backup must hold the real mtime so a later restore can carry it forward.
- **`install`/`uninstall` must stay non-clobbering and idempotent.** Merge our `SessionStart` hook into `~/.claude/settings.json` without disturbing the user's other settings or hooks; uninstall removes only our entry (matched by the `backup_claude_history.py backup` command substring) and prunes empty structure. Parse→modify→serialize the JSON; never splice text. Python 3.7+ ordered dicts mean key order is preserved — don't worry about "scrambling" the file.
- **Substantial-vs-noise is reporting only, and fails loud.** `growth_is_substantial` gates on `SUBSTANTIAL_GROWTH_BYTES` (cheap pre-filter) then judges by record type (`BOOKKEEPING_RECORD_TYPES`). Any parse failure or unknown type → treat as substantial. The byte threshold tunes efficiency, not correctness — see NOTES.md "As built (backup-v0.1.0)". Don't let this logic leak into the backup decision (see first bullet).
- **Target Python 3.9** — Apple's system `python3` (`/usr/bin/python3`, currently 3.9.6) is the floor for all scripts in this repo. Don't reach for 3.10+ syntax (`match`, etc.); `from __future__ import annotations` is what lets the modern type-hint syntax run on 3.9.
- **The installed hook pins an interpreter path (`sys.executable`).** It can silently die if that python is removed (see TODO.md follow-up). `status`'s last-run staleness check is the safety net; don't remove it.
