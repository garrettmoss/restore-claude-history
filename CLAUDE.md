# CLAUDE.md

Agent guidance for working in this repo. If you're an AI assistant: read this first, then NOTES.md for design rationale and TODO.md for open work.

## Project shape

Single-file Python script (`restore_claude_history.py`) that recovers deleted Claude Code JSONL transcripts from macOS Time Machine snapshots. Standard library only — no dependencies. macOS + APFS only.

Docs are deliberately split:
- **README.md** — user-facing: what it does, how to run it.
- **NOTES.md** — design rationale, hand-verified recovery steps, gotchas, related GitHub issues.
- **TODO.md** — open work, publicity plan, follow-up projects.

When you change behavior, update the relevant doc — usually NOTES.md (for new gotchas) or README.md (for user-visible changes). Don't create new top-level docs without checking; the three above are intended to be the full set.

## Versioning

The script's in-file `__version__` constant and the latest git tag MUST stay in sync.

- Current: `__version__ = "1.0.0"` in `restore_claude_history.py`; matching git tag `v1.0.0` on origin.
- `push.followTags = true` is set, so annotated tags travel with `git push` automatically. Don't push tags separately.
- When bumping version: edit `__version__`, commit, tag the commit (`git tag -a vX.Y.Z -m "..."`), push. Both updates go in the same commit as the change they describe.
- Users run `python3 restore_claude_history.py --version` to report bugs. If they say "1.0.0" and the latest tag is `v1.2.0`, that's a stale clone.

## Things that are easy to get wrong

- **mtime preservation is load-bearing.** The script preserves the snapshot's original mtime on restored files. Do NOT rewrite mtimes to in-file message timestamps — that triggers the very cleanup bug we're working around. See the @ojura/#59248 gotcha in NOTES.md.
- **Encoded project names start with `-`.** Argparse rejects them as flags. The script pre-rewrites `--project FOO` → `--project=FOO`. Don't undo this.
- **Don't touch macOS auto-mounted snapshots.** The script distinguishes between snapshots it mounted (cleanup unmounts them) and pre-existing macOS auto-mounts (left alone). The `owned_by_us` flag on the `Snapshot` dataclass exists for exactly this reason.
- **Time Machine drive must be plugged in for any non-trivial test.** Without it, the script exits early at `find_tm_volume()`. Spotlight indexes mounted TM volumes aggressively; keep test runs short and unplug when done.
