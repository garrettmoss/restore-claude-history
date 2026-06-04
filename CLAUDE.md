# CLAUDE.md

Agent guidance for working in this repo. If you're an AI assistant: read this first, then NOTES.md for design rationale and TODO.md for open work.

## Project shape

A small toolkit of standalone Python scripts for recovering Claude chat data on macOS. Standard library only — no dependencies. macOS + APFS only. Each script is single-file and runs without the others; share substrate only when a second script actually needs it (don't pre-emptively refactor a common module).

- **`restore_claude_history.py`** — recover deleted Claude Code JSONL transcripts (`~/.claude/projects/<project>/*.jsonl`) from Time Machine and/or local APFS snapshots.
- **`restore_claude_desktop.py`** — repair Claude Desktop session metadata when the UI shows "Session not found on disk" for transcripts that are still on disk. v1 handles Mode A (surgical edit of `local_*.json`); Mode A snapshot-restore fallback and Mode B (JSONL restore) are deferred. See NOTES.md for the failure-mode taxonomy.

Docs are deliberately split:
- **README.md** — user-facing: what it does, how to run it.
- **NOTES.md** — design rationale, hand-verified recovery steps, gotchas, related GitHub issues.
- **TODO.md** — open work, publicity plan, follow-up projects.

When you change behavior, update the relevant doc — usually NOTES.md (for new gotchas) or README.md (for user-visible changes). Don't create new top-level docs without checking; the three above are intended to be the full set.

## Versioning

Each script is on its own version clock. Each script's in-file `__version__` constant MUST stay in sync with the latest matching prefixed git tag.

- **`restore_claude_history.py`** — tags `history-vX.Y.Z`. Current: `__version__ = "1.1.0"`. Historical tags `v1.0.0` / `v1.1.0` (no prefix) refer to this script and stay as-is; the prefix pattern starts from the next bump.
- **`restore_claude_desktop.py`** — tags `desktop-vX.Y.Z`. Current: `__version__ = "0.1.0"`.
- `push.followTags = true` is set, so annotated tags travel with `git push` automatically. Don't push tags separately.
- When bumping a script's version: edit its `__version__`, commit, tag the commit with the script's prefix (`git tag -a desktop-vX.Y.Z -m "..."` or `history-vX.Y.Z`), push. Both updates go in the same commit as the change they describe.
- Users run `python3 <script>.py --version` to report bugs. Tell them to mention which script and which version — "history 1.1.0" vs "desktop 0.1.0" are different release lanes now.

## Things that are easy to get wrong

- **mtime preservation is load-bearing.** The script preserves the snapshot's original mtime on restored files. Do NOT rewrite mtimes to in-file message timestamps — that triggers the very cleanup bug we're working around. See the @ojura/#59248 gotcha in NOTES.md.
- **Encoded project names start with `-`.** Argparse rejects them as flags. The script pre-rewrites `--project FOO` → `--project=FOO`. Don't undo this.
- **Don't touch macOS auto-mounted snapshots.** The script distinguishes between snapshots it mounted (cleanup unmounts them) and pre-existing macOS auto-mounts (left alone). The `owned_by_us` flag on the `Snapshot` dataclass exists for exactly this reason.
- **Time Machine drive must be plugged in for any non-trivial test.** Without it, the script exits early at `find_tm_volume()`. Spotlight indexes mounted TM volumes aggressively; keep test runs short and unplug when done.
