# restore-claude-history

Recover deleted Claude Code chat transcripts from macOS Time Machine snapshots.

## Background

Claude Code stores chat transcripts as JSONL files under `~/.claude/projects/<encoded-cwd>/`. A cleanup job prunes them after `cleanupPeriodDays` (default: **30 days**, undocumented, no warning). If you haven't changed that setting, you've probably already lost months of conversations.

If you have a macOS Time Machine drive, this script ([`restore_claude_history.py`](restore_claude_history.py)) can get them back.

## Prevention first

Before anything else, add this to `~/.claude/settings.json`:

```json
"cleanupPeriodDays": 36500
```

That's ~100 years. There's no documented upper bound; the schema just wants a positive integer. Do this on every machine you use Claude Code on.

**Set this *and* keep backups — not one or the other.** The setting defangs the documented cleanup, but multiple user reports (e.g. [#41458](https://github.com/anthropics/claude-code/issues/41458)) describe chats vanishing *despite* the flag being set, most often around app updates. That's why this script exists alongside the prevention step, not instead of it.

## Recovery

This script: [`restore_claude_history.py`](restore_claude_history.py)

### Requirements

- macOS with an APFS Time Machine drive that has snapshots
- **Full Disk Access** for whatever app runs the script (Terminal, iTerm, VS Code)
  - System Settings → Privacy & Security → Full Disk Access → +
- Python 3.7+ (the system `python3` from Apple's Command Line Tools is fine)

### Quickstart

```bash
# Plug in your Time Machine drive, then:
git clone https://github.com/garrettmoss/restore-claude-history
cd restore-claude-history

# See what would be restored, no changes made:
python3 restore_claude_history.py --dry-run --verbose

# Actually restore:
python3 restore_claude_history.py
```

### Flags

| Flag | What it does |
|---|---|
| `--dry-run` | Show what would be restored, copy nothing. Always run this first. |
| `--project NAME` | Limit to one encoded project dir (e.g. `--project=-Users-you-projects-foo`). Note the `=` — encoded names start with `-`. |
| `--include-memory` | Also restore `<project>/memory/` subdirs. |
| `--verbose` | Log every file decision, not just the summary. |
| `--dest DIR` | Restore into `DIR` instead of `~/.claude/projects` (for testing). |

### What it does

1. Finds your Time Machine APFS volume.
2. Mounts every snapshot read-only (or reuses ones macOS already auto-mounted).
3. Indexes every `.jsonl` it finds across all snapshots.
4. For each `(project, filename)`, picks the **largest** version — JSONLs are append-only, so bigger = more complete.
5. Copies it back, **preserving the original mtime** and stripping the inherited Time Machine ACL so the restored files remain writable.
6. Skips files where your on-disk version is already the same size or larger — so active or in-progress chats are never overwritten with an older snapshot.
7. Cleans up the snapshots it mounted (leaves any pre-existing system mounts alone).

### Verifying it works

There's an end-to-end test that builds a sandbox from your real chats, deletes a few files, restores them, and checks size/mtime/ACL match:

```bash
python3 tests/verify_restore.py --project=-Users-you-projects-foo
```

## Background reading

See [NOTES.md](NOTES.md) for the full story: how the bug works, what Time Machine snapshots actually look like, what we tried that didn't work, and the verified working commands from the original recovery session.

### See also

This tool covers exactly one slice of the disappearing-Claude-chats problem: macOS, Time Machine, JSONLs deleted from disk. If that's not your situation, one of these may help. Grouped by platform.

**macOS:**
- **[DeveloperAlly/claude-code-survival-toolkit](https://github.com/DeveloperAlly/claude-code-survival-toolkit)** — broader in-app survival kit for the VS Code extension: 9 fix scripts (sidebar dropped sessions, scrambled titles, scrambled sort order, vscode `state.vscdb` snapshot/restore) plus 7 governance hooks. macOS bash; use this if your data is on disk but the extension's sidebar is broken or scrambled.

**Linux:**
- **[cnighswonger/restore-claude-history-linux](https://github.com/cnighswonger/restore-claude-history-linux)** — Linux port of this tool. ZFS support shipped; Btrfs and Timeshift planned.

**Windows:**
- **[BasedGPT/claude-code-session-recovery](https://github.com/BasedGPT/claude-code-session-recovery)** — Windows-specific Claude Desktop metadata repair (orphan JSONLs, junction slug mismatches, missing groupings).

**Cross-platform:**
- **[ibrews/claude-session-recovery](https://github.com/ibrews/claude-session-recovery)** — your JSONLs are still on disk, but Claude Desktop's UI doesn't show them (index corruption after a crash/BSOD). Rebuilds the Desktop session index.
- **[markwoitaszek/claude-session-recovery](https://github.com/markwoitaszek/claude-session-recovery)** — Claude Desktop crashes with "There was a problem with the session" on a specific large/complex chat. Extracts the JSONL to clean Markdown so you don't lose the conversation.

## License

[MIT](LICENSE)
