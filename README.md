# restore-claude-history

Three small macOS tools for keeping and recovering Claude Code chats:

- **[`backup_claude_history.py`](backup_claude_history.py)** — *prevention, and recovery from your own backups.* Copies your transcripts out of `~/.claude/projects/` on every session start, so a cleanup sweep or bad update can't take them — then `restore`s them straight back if one goes missing. No external drive needed. Set this up first.
- **[`restore_claude_code.py`](restore_claude_code.py)** — *recovery from Time Machine.* Your transcripts were deleted and you have no backup of your own. Pulls them back from macOS Time Machine or local APFS snapshots.
- **[`restore_claude_desktop.py`](restore_claude_desktop.py)** — Claude Desktop's UI shows **"Session not found on disk"** for sessions whose transcript is still there. Repairs the broken metadata link, no external drive required.

Each tool is useful on its own. `backup_claude_history.py` is the everyday safety net; the two restore tools are what you reach for when prevention wasn't in place — `restore_claude_code.py` needs a Time Machine drive *or* local snapshots, the Desktop tool needs only local files.

## Background

**Deleted transcripts.** Claude Code stores chat transcripts as JSONL files under `~/.claude/projects/<encoded-cwd>/`. A cleanup job prunes them after `cleanupPeriodDays` (default: **30 days**, undocumented, no warning). If you haven't changed that setting, you've probably already lost months of conversations.

**Desktop's "Session not found on disk."** A separate failure: Desktop's per-session metadata files (`~/Library/Application Support/Claude/claude-code-sessions/.../local_*.json`) lose the `cliSessionId` field that links metadata to transcript, so the UI can't find a transcript that's still on disk.

## Prevention first

Prevention has two layers. Do both — they cover different failure modes.

**1. Raise the cleanup window.** Add this to `~/.claude/settings.json`:

```json
"cleanupPeriodDays": 36500
```

That's ~100 years. There's no documented upper bound; the schema just wants a positive integer. Do this on every machine you use Claude Code on.

**2. Back up your transcripts continuously** with [`backup_claude_history.py`](backup_claude_history.py) (see [Backup](#backup) below). The setting defangs the *documented* cleanup, but multiple user reports (e.g. [#41458](https://github.com/anthropics/claude-code/issues/41458)) describe chats vanishing *despite* the flag being set, most often around app updates — and some session types ignore the setting entirely. The flag is a stopgap; a real backup is the safety net. Set the flag *and* keep backups, not one or the other.

## Backup

This script: [`backup_claude_history.py`](backup_claude_history.py)

Copies your Claude Code transcripts out of the deletion path into `~/.claude-code-backups/` every time a Claude Code session starts — so even if a cleanup sweep or an update deletes the originals, your latest copy survives outside `~/.claude/projects/`. It's the everyday prevention path; [`restore_claude_code.py`](restore_claude_code.py) stays the deeper Time Machine failsafe for chats older than your backups reach.

How it works: a `SessionStart` hook runs one **copy-on-grow** pass on each session launch — a transcript is copied only when its backup is missing or the live file has *grown* (JSONLs are append-only, so the largest copy is the most complete). Original mtimes are preserved, so a backed-up file never looks "freshly touched" to the cleanup job. A `manifest.json` records what was captured and when, so `status` can warn you if backups silently stop running.

### Requirements

- macOS with Claude Code installed.
- Python 3.9+ (the system `python3` from Apple's Command Line Tools is fine). No external dependencies.

### Quickstart

```bash
git clone https://github.com/garrettmoss/restore-claude-history
cd restore-claude-history

# Install the SessionStart hook (merged into ~/.claude/settings.json,
# leaving your other hooks and settings untouched):
python3 backup_claude_history.py install

# Capture what's already on disk right now (the hook only fires on
# future session starts):
python3 backup_claude_history.py backup

# Check it's working — hook installed, when it last ran, anything stale:
python3 backup_claude_history.py status
```

After install, every new Claude Code session backs up automatically. Run from a stable clone path — the installed hook points at this script's location, so don't delete or move the repo afterward.

### Verbs

| Verb | What it does |
|---|---|
| `backup` | Run one copy-on-grow pass. This is what the hook calls; run it manually anytime to capture the current state. |
| `install` | Merge the `SessionStart` hook into `~/.claude/settings.json` (non-clobbering, idempotent). |
| `uninstall` | Remove that hook again. Existing backups are left in place. |
| `status` | Is the hook installed, when did backup last run, and are any live transcripts not yet (or only partially) backed up? |
| `list` | List backed-up transcripts grouped by project, newest first — with each session's title, size, and when it was last used. |
| `restore` | Copy backed-up transcript(s) back into `~/.claude/projects/`. Dry-run by default; pass `--apply` to write. See [Restoring from your backups](#restoring-from-your-backups). |

Add `--verbose`/`-v` to `backup` to print a line per file copied (and to `restore` to expand each chat's subagent fragments).

### Restoring from your backups

When a transcript gets deleted, `restore` copies it back out of `~/.claude-code-backups/` into `~/.claude/projects/`, preserving the original mtime so the cleanup job doesn't immediately re-delete it. It reads only *your own* backups — [`restore_claude_code.py`](#recovery-from-time-machine) stays the separate Time Machine failsafe for chats older than your backups reach.

```bash
# Preview — what would come back, what's already intact, what's protected.
# Dry-run is the default; nothing is written.
python3 backup_claude_history.py restore --all

# Restore one chat (bare UUID, or the full <project>/<uuid> if it's ambiguous):
python3 backup_claude_history.py restore --session 141950a9-... --apply

# Restore an entire project, or everything:
python3 backup_claude_history.py restore --project=-Users-you-projects-foo --apply
python3 backup_claude_history.py restore --all --apply
```

Pick what to restore with exactly one of `--session <uuid>` (or `<project>/<uuid>`), `--project <encoded>`, or `--all`. Run `list` first to find a chat by its title.

Two safety rules make `restore` safe to point at `--all`:

- **Dry-run by default.** You see the full plan — `restore` / `skip` / `REFUSE` per chat — and nothing is written until you add `--apply`.
- **It never shrinks a live file.** If your on-disk transcript has *grown* past the backup (you kept chatting since the last backup ran), `restore` **refuses** it rather than overwrite newer content with an older copy. `--force` overrides this, deliberately, for the rare case you want the older version back.

The preview groups by project and shows one line per chat (`ACTION  SESSION  BACKUP→LIVE  TITLE`); subagent fragments are folded into a `(+N subagent(s))` count unless you pass `--verbose`. Restoring `--all` or `--project` always brings the subagent fragments along regardless — the count is just a display choice.

### A note on the counts

`status` and `list` count **conversations** and **subagent fragments** separately (e.g. "54 conversations + 26 subagent fragments"). Subagent transcripts live under `<session-uuid>/subagents/` and are backed up too, but they're slices of a parent conversation, not standalone chats — so the split keeps a number like "80 files" from reading as "80 chats you forgot about."

You may also see backups reported as *minor (likely Claude Desktop bookkeeping)*. Claude re-appends small bookkeeping records to a transcript every time you open or quit it in Desktop, which makes idle chats "grow" without gaining real messages. They're still backed up; the tool just reports them quietly so real new conversations stand out. See [NOTES.md](NOTES.md) for the details.

## Recovery from Time Machine

This script: [`restore_claude_code.py`](restore_claude_code.py)

For when transcripts were deleted and you have **no [backup](#backup) of your own** — this is the deep failsafe that pulls them back from macOS Time Machine or local APFS snapshots. (If you *do* have backups from [`backup_claude_history.py`](backup_claude_history.py), restore from those with its [`restore` verb](#restoring-from-your-backups) instead — no Time Machine drive needed. This tool deliberately reads only Time Machine snapshots, never our own backups: it's the proven recovery path, kept separate and single-purpose.)

### Requirements

- macOS with **at least one** of:
  - An APFS Time Machine drive that has snapshots, *or*
  - Local APFS snapshots on your internal disk (run `tmutil listlocalsnapshots /System/Volumes/Data` to check). These are typically present whenever Time Machine has run recently, even if the drive is currently unplugged.
- **Full Disk Access** for whatever app runs the script (Terminal, iTerm, VS Code)
  - System Settings → Privacy & Security → Full Disk Access → +
  - Without it, mounting the backup snapshot fails with `Operation not permitted`; the script will tell you to grant FDA and re-run.
- Python 3.7+ (the system `python3` from Apple's Command Line Tools is fine)

### Quickstart

```bash
git clone https://github.com/garrettmoss/restore-claude-history
cd restore-claude-history

# See what would be restored, no changes made. Uses whichever sources
# are available — TM drive (if plugged in) and local snapshots:
python3 restore_claude_code.py --dry-run --verbose

# Actually restore:
python3 restore_claude_code.py
```

> If your TM drive is unplugged, the script will fall through to local snapshots automatically. Local snapshots only cover ~24h to whenever you last ran a Time Machine backup, so they're a recent-deletion safety net, not a deep archive — plug the drive in if you need older chats.

### Flags

| Flag | What it does |
|---|---|
| `--dry-run` | Show what would be restored, copy nothing. Always run this first. |
| `--source local\|tm\|both` | Which snapshot pool to search. Default `both` (uses whichever is available). `local` skips the TM drive entirely; `tm` requires it. |
| `--project NAME` | Limit to one encoded project dir (e.g. `--project=-Users-you-projects-foo`). Note the `=` — encoded names start with `-`. |
| `--include-memory` | Also restore `<project>/memory/` subdirs. |
| `--verbose` | Log every file decision, not just the summary. |
| `--dest DIR` | Restore into `DIR` instead of `~/.claude/projects` (for testing). |
| `--list-only` | Don't restore anything; just print one tab-separated row per `(project, filename)` pair found in available snapshots (`kind`, `snapshot`, `project`, `filename`, `size`, `mtime`). Useful for previewing what's recoverable. Status text routes to stderr in this mode so stdout stays parse-clean. |

### What it does

1. Finds available snapshot sources per `--source`: your TM drive (if plugged in), the internal Data volume's local APFS snapshots, or both.
2. Walks snapshots **newest-first**, mounting one at a time (read-only), indexing, restoring, and unmounting before moving to the next. Reuses any mounts macOS already auto-mounted instead of remounting.
3. For each `(project, filename)`, takes the **first** version seen — JSONLs are append-only, so the newest snapshot containing a file holds the largest copy. Once restored from one snapshot, older snapshots skip that file.
4. Copies it back, **preserving the original mtime** and stripping the inherited Time Machine ACL so the restored files remain writable.
5. Skips files where your on-disk version is already the same size or larger — so active or in-progress chats are never overwritten with an older snapshot.
6. Cleans up the snapshots it mounted (leaves any pre-existing system mounts alone).

### Verifying it works

There's an end-to-end test that builds a sandbox from your real chats, picks files known to be present in your snapshots, deletes them, restores them, and checks size/mtime/ACL match the snapshot:

```bash
python3 tests/verify_restore.py --project=-Users-you-projects-foo
# Or pin a specific source (handy when validating without the TM drive plugged in):
python3 tests/verify_restore.py --project=-Users-you-projects-foo --source=local
```

## Claude Desktop repair

This script: [`restore_claude_desktop.py`](restore_claude_desktop.py)

Repairs Claude Desktop session metadata so the UI stops showing **"Session not found on disk"** for transcripts that are still on disk under `~/.claude/projects/`. No Time Machine drive needed — the primary repair path is a single-field edit to a local metadata file.

> [!WARNING]
> **Do not send a message in a broken session before repairing it.**
> When you open a session that shows "Session not found on disk" (or "No messages yet") and send a message, Desktop binds a *new* `cliSessionId` to that session card and starts a fresh, empty transcript. The card then looks "fixed" — but the link to your original conversation is gone, and the new pointer might prevent this script (or a later Time Machine restore via [`restore_claude_code.py`](restore_claude_code.py)) from reconnecting the old transcript. Leave broken sessions untouched until you've run the repair.
> Thanks to [@1nwooozip on issue #53717](https://github.com/anthropics/claude-code/issues/53717#issuecomment-4505032582) for documenting this footgun.

### Requirements

- macOS with Claude Desktop installed.
- **Claude Desktop fully quit** (Cmd-Q, not just closed) before running. The script refuses to act otherwise — Desktop rewrites its session files from in-memory state and will clobber any edits made while it's running.
- Python 3.7+.

### Quickstart

```bash
# See what's broken and what would be fixed. No changes:
python3 restore_claude_desktop.py --dry-run

# Actually fix. Backs up the Desktop sessions dir to /tmp first:
python3 restore_claude_desktop.py
```

Sample output:

```
  STATUS        SESSION
  ------------- --------------------------------------------------

  /Users/you/projects/foo
  FIXABLE       Initial setup of build pipeline
  OK            Refactor auth flow
  LOST          Old conversation about caching

Summary
  FIXABLE: 1 session can be repaired by this script (run without --dry-run to apply)
  OK:      1 session loads correctly in Claude Desktop
  LOST:    1 session has broken metadata AND no transcript on disk
```

Then launch Claude Desktop to verify — repaired sessions should load normally.

### What the labels mean

- **OK** — the session loads correctly in Claude Desktop. No action needed.
- **FIXABLE** — metadata is broken but the transcript is still on disk. This is what the script repairs: it writes the missing `cliSessionId` field pointing to the matching transcript, then removes the `transcriptUnavailable` flag. Every other field is left untouched.
- **LOST** — metadata is broken AND no transcript is on disk for it. The script can't help here directly; you'd need a Time Machine (or other snapshot) backup that reaches back to before the transcript was deleted. [`restore_claude_code.py`](restore_claude_code.py) handles that side.
- **NEEDS REVIEW** — metadata is broken and multiple transcripts on disk start within seconds of when the session was created, so the script can't tell which one belongs. Skipped rather than guessed. A future version will fall back to restoring metadata from a Time Machine snapshot for these.

### Flags

| Flag | What it does |
|---|---|
| `--dry-run` | Report only; don't modify any files. |
| `--no-backup` | Skip the pre-apply backup. By default the Desktop sessions dir is copied to `/tmp/claude-code-sessions.backup-<timestamp>/` before any edits. |
| `--project NAME` | Limit to one encoded project dir (e.g. `--project=-Users-you-projects-foo`). Note the `=`. |
| `--match-tolerance SECONDS` | Max time delta between metadata `createdAt` and transcript first-record timestamp for a confident match (default: 60). |
| `--verbose` | Show per-row diagnostic detail and a line per applied fix. |

### Verified compatibility

`v0.1.0` is verified working end-to-end on Claude Desktop **1.11187.4** (both pre- and post-auto-update). Run `python3 restore_claude_desktop.py --version` to see which Desktop version your installed copy was verified against. If a newer Desktop release breaks the recipe, the verified version is the last known-working bisection target — please open an issue with details.

## Background reading

See [NOTES.md](NOTES.md) for the full story: how the bug works, what Time Machine snapshots actually look like, what we tried that didn't work, and the verified working commands from the original recovery session. The "Claude Desktop: session-recovery failure-mode taxonomy" section there documents the Mode A / Mode B split the Desktop tool acts on.

### See also

This tool covers exactly one slice of the disappearing-Claude-chats problem: macOS, Time Machine, JSONLs deleted from disk. If that's not your situation, one of these may help. Grouped by platform.

**macOS:**
- **[DeveloperAlly/claude-code-survival-toolkit](https://github.com/DeveloperAlly/claude-code-survival-toolkit)** — broader in-app survival kit for the VS Code extension: 9 fix scripts (sidebar dropped sessions, scrambled titles, scrambled sort order, vscode `state.vscdb` snapshot/restore) plus 7 governance hooks. macOS bash; use this if your data is on disk but the extension's sidebar is broken or scrambled.

**Linux:**
- **[vsits/restore-claude-history-linux](https://github.com/vsits/restore-claude-history-linux)** — Linux port of this tool. ZFS, Btrfs, and Timeshift all shipped (v1.1.0); real-kernel e2e validation on each. Recovery logic stays in lockstep with this repo via an upstream-sync workflow.

**Windows:**
- **[BasedGPT/claude-code-session-recovery](https://github.com/BasedGPT/claude-code-session-recovery)** — Windows-specific Claude Desktop metadata repair (orphan JSONLs, junction slug mismatches, missing groupings).

**Cross-platform:**
- **[ibrews/claude-session-recovery](https://github.com/ibrews/claude-session-recovery)** — your JSONLs are still on disk, but Claude Desktop's UI doesn't show them (index corruption after a crash/BSOD). Rebuilds the Desktop session index.
- **[markwoitaszek/claude-session-recovery](https://github.com/markwoitaszek/claude-session-recovery)** — Claude Desktop crashes with "There was a problem with the session" on a specific large/complex chat. Extracts the JSONL to clean Markdown so you don't lose the conversation.

## License

[MIT](LICENSE)
