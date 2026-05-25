# TODO

Open work for this repo. Add as needed.

## Publicize the repo

Higher leverage than another feature — one well-placed comment lands in front of people *actively searching* right now. Do this before getting lost in new code.

Suggested order (lowest cost / highest signal first):

1. **Fill in `NOTES.md` → "Related GitHub issues" section.** Search `anthropics/claude-code` issues for: `cleanupPeriodDays`, `history deleted`, `lost chats`, `session not found`, `transcript missing`. Capture the URLs + a one-line summary of each. Aim for 3–8 active threads.

2. **Comment on each of those issues.** Short, helpful, not spammy. Example:

   > Had this happen and built a recovery tool for it (macOS + Time Machine only): https://github.com/garrettmoss/restore-claude-history — also includes the one-line settings change to prevent it going forward.

   Don't post the same message verbatim everywhere; tweak per thread.

3. **File a new issue on `anthropics/claude-code`** if no good thread exists for it. Title: "`cleanupPeriodDays` silently deletes chat history; recovery tool for macOS users". Body: explain the bug surface, link the repo, ask for either (a) UI exposure of the setting, (b) a warning before deletion, or (c) bumping the default. This is also the path to Anthropic actually *seeing* the work.

4. **Reddit.** Candidates: r/ClaudeAI (most direct audience), r/MachineLearning (broader), r/macsysadmin (the Time Machine angle). One post per sub, spread over a few days. Title something like "Recovered months of deleted Claude Code chats from Time Machine — script + writeup".

5. **Hacker News** (news.ycombinator.com). Submit as `Show HN: restore-claude-history – recover deleted Claude Code chats from Time Machine`. HN front page = hundreds of GitHub stars in a day; most submissions vanish. Low cost, asymmetric upside. Best times to submit: weekday mornings US time.

6. **dev.to** — write a short post walking through the bug, the prevention setting, and how the recovery works. Indexed by Google long-term; useful for anyone searching "claude code chat history deleted" months from now.

7. **Friends + personal network.** People who use Claude Code and might lose chats themselves — the prevention setting alone is worth sharing even if they never need the recovery.

8. **Stretch: reach out to Anthropic directly.** If any of the above gets traction, that's leverage to ask Anthropic to link the tool from their docs or surface `cleanupPeriodDays` in the UI. The point isn't credit; it's preventing future users from hitting this in the first place.

Tip: track which channels actually drove traffic (GitHub repo Insights → Traffic) so future-you knows what worked.

## Claude Desktop session recovery

The Claude Desktop app has an embedded Claude Code area that lists past sessions in its UI, but clicking them often shows **"Session not found on disk"** — same disappearing-chat problem as Claude Code CLI, different storage location.

Likely path (needs verification):
`~/Library/Application Support/Claude/claude-code-sessions/`

Other adjacent dirs that may matter:
- `~/Library/Application Support/Claude/claude-code/`
- `~/Library/Application Support/Claude/claude-code-vm/`
- `~/Library/Application Support/Claude/local-agent-mode-sessions/`

Suggested approach for whoever picks this up:
1. **Investigate first, code second.** Look at what's actually in those dirs, what file format the sessions use, and whether the UI is reading from the same place we'd be writing to. Don't assume it works like Claude Code's `~/.claude/projects/`.
2. **Compare against a Time Machine snapshot.** Mount a snapshot, compare the same dirs inside it to what's on disk now. The diff *is* the deleted content.
3. **Decide: extend `restore_claude_history.py` or write a sibling?** Depends on how similar the file layout and recovery logic are. If JSONLs in a parallel dir, probably one script with a `--desktop` flag. If wildly different format (SQLite, IndexedDB, encrypted blobs, etc.), a sibling script is cleaner.
4. **Start with `young-ladys-primer`.** It's the same project we used for the Claude Code recovery, so we know what "before" looks like and have a good chance of finding restorable data in the snapshots. The UI currently shows these chats with the title "Session not found on disk" and the subtitle "Send a message to start fresh in this directory" (along with "Archive" and "Delete" buttons — note: not "Recover"). Hopefully this is the more recoverable failure mode of the two.
5. **Then stress-test on `data-of-being`.** Its chats show "no messages yet" — a more severe failure mode. Possibly older than the available Time Machine snapshots, in which case this one may genuinely be unrecoverable. Useful either way: success expands the script's coverage, failure tells us where the floor is.

NOTES.md has the design rationale and gotchas from the Claude Code recovery work — most of the snapshot-handling, ACL-stripping, and mtime-preservation logic will carry over.

## Stretch: user-hosted Claude chat backups

A continuous, user-run backup of `~/.claude/projects/` so you don't have to rely on Time Machine (or any specific OS-level snapshot tool) to recover from a future deletion event.

- **Explicitly post-v1.** Ship the recovery tool, do the Desktop follow-up, *then* consider this. Easy to lose a week here.
- **Weakens the current pitch.** Today the script is "Time Machine + run this." Adding a backup feature means the story splits: "Time Machine, OR you installed our backup tool *before* the deletion." Most users won't have done the latter — so the recovery story stays cleaner if backups stay separate.
- **Probably a sibling project**, not a feature of this one. Different shape (daemon vs. one-shot), different audience (preventative vs. reactive).
