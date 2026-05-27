# TODO

Open work for this repo. Add as needed.

## Publicize the repo

Higher leverage than another feature — one well-placed comment lands in front of people *actively searching* right now. Do this before getting lost in new code.

Suggested order (lowest cost / highest signal first):

- [x] **1. Fill in `NOTES.md` → "Related GitHub issues" section.** Searched `anthropics/claude-code` for `cleanupPeriodDays`, `history deleted`, `lost chats`, `session not found`, `transcript missing`. Captured 16 threads grouped by priority in NOTES.md. *Done 2026-05-24.*

- [ ] **2. Comment on each of those issues.** 🟡 *In progress: 1 of ~16 posted (#59248 on 2026-05-24). Next up: #41458, then expand if traction. See NOTES.md checklist for the full ordered list.* Short, helpful, not spammy. Example:

   > Had this happen and built a recovery tool for it (macOS + Time Machine only): https://github.com/garrettmoss/restore-claude-history

   Don't post the same message verbatim everywhere; tweak per thread.

- [x] **3. File a new issue on `anthropics/claude-code`** if no good thread exists for it. Filed [#62272](https://github.com/anthropics/claude-code/issues/62272) on 2026-05-25 — "Chat JSONLs deleted from `~/.claude/projects/` despite `cleanupPeriodDays` set high — appears triggered by updates/restarts." Asks for any of: honor the setting, warn before deletion, surface in UI.

- [ ] **4. Reddit.** Candidates: r/ClaudeAI (most direct audience), r/MachineLearning (broader), r/macsysadmin (the Time Machine angle). One post per sub, spread over a few days. Title something like "Recovered months of deleted Claude Code chats from Time Machine — script + writeup".

- [ ] **5. Hacker News** (news.ycombinator.com). Submit as `Show HN: restore-claude-history – recover deleted Claude Code chats from Time Machine`. HN front page = hundreds of GitHub stars in a day; most submissions vanish. Low cost, asymmetric upside. Best times to submit: weekday mornings US time.

- [ ] **6. dev.to** — write a short post walking through the bug, the prevention setting, and how the recovery works. Indexed by Google long-term; useful for anyone searching "claude code chat history deleted" months from now.

- [ ] **7. Friends + personal network.** People who use Claude Code and might lose chats themselves — the prevention setting alone is worth sharing even if they never need the recovery.

- [ ] **8. Stretch: reach out to Anthropic directly.** If any of the above gets traction, that's leverage to ask Anthropic to link the tool from their docs or surface `cleanupPeriodDays` in the UI. The point isn't credit; it's preventing future users from hitting this in the first place.

Tip: track which channels actually drove traffic (GitHub repo Insights → Traffic) so future-you knows what worked.

## Claude Desktop session recovery

The Claude Desktop app has an embedded Claude Code area that lists past sessions in its UI, but clicking them often shows **"Session not found on disk"** — same disappearing-chat problem as Claude Code CLI, different storage location.

Confirmed path: `~/Library/Application Support/Claude/claude-code-sessions/<group>/<project>/local_*.json` (verified 2026-05-27 — one group dir present on this machine with 11 `local_*.json` metadata entries).

Other adjacent dirs that may matter:
- `~/Library/Application Support/Claude/claude-code/`
- `~/Library/Application Support/Claude/claude-code-vm/`
- `~/Library/Application Support/Claude/local-agent-mode-sessions/`

### Subtask: metadata synthesis after a Time Machine restore

Raised by @BasedGPT on [#62272](https://github.com/anthropics/claude-code/issues/62272#issuecomment-4554894518) (and corroborated by @ShreeshaJay on [#48334](https://github.com/anthropics/claude-code/issues/48334)): JSONLs restored into `~/.claude/projects/` *without* a matching `local_*.json` entry in `claude-code-sessions/` get treated as orphans on the next cleanup pass and re-deleted. Originally observed on Windows; the path exists on macOS too, so the same risk almost certainly applies here.

This means the current `restore_claude_history.py` flow has a gap: a successful restore can be silently undone on the next sweep if metadata isn't synthesised alongside the JSONLs. Fixing this is the natural bridge into the broader Desktop recovery work below — same files, same machine, same investigation.

Reference implementation: [`BasedGPT/claude-code-session-recovery` → `tools/sessions/synth_session_metadata.py`](https://github.com/BasedGPT/claude-code-session-recovery/blob/main/tools/sessions/synth_session_metadata.py). Windows-targeted; logic ports. He gave permission to use it as the reference (and we gave him reciprocal permission on our largest-file + mtime-preservation code). Credit + link when this lands. I publicly committed to this being "next" in the [#62272 reply](https://github.com/anthropics/claude-code/issues/62272), so don't let it drift.

### Broader Desktop recovery — suggested approach for whoever picks this up
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

**Starting point when we pick this up:** @ojura sketched a `SessionStart` hook on [#59248](https://github.com/anthropics/claude-code/issues/59248) — a small bash script that copies any `*.jsonl` from `~/.claude/projects/` to `~/.claude-session-backups/` on every session launch, only when the live file has grown (mtime-immune, shrink-safe). Wired in via `~/.claude/settings.json` under `hooks.SessionStart`. Worth using as the reference implementation for our `backup_claude_history.py` — credit ojura, then extend with: a real CLI, restore-from-backup verb, retention policy, optional macOS LaunchAgent for continuous (not just session-start) coverage, and cross-platform stat handling (his script already handles GNU vs. BSD `stat`). One concrete reason to prioritize this over "just set `cleanupPeriodDays: 36500`": per ojura, processes started with `--setting-sources local` or SDK sessions with `settingSources: []` (including autonomously spawned subagents) bypass the setting and fall back to the 30-day default. A SessionStart-driven backup sidesteps that whole class of bypass.
