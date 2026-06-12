# TODO

Open work for this repo. Add as needed.

## Publicize the repo

Higher leverage than another feature — one well-placed comment lands in front of people *actively searching* right now. Do this before getting lost in new code.

Suggested order (lowest cost / highest signal first):

- [x] **1. Fill in `NOTES.md` → "Related GitHub issues" section.** Searched `anthropics/claude-code` for `cleanupPeriodDays`, `history deleted`, `lost chats`, `session not found`, `transcript missing`. Captured 16 threads grouped by priority in NOTES.md. *Done 2026-05-24.*

- [ ] **2. Comment on each of those issues.** 🟡 *In progress: 7 posted (#59248, #41458, #26452, #9258, #60984, #53717, #48334) plus #62272 filed. Remaining queue lives in NOTES.md → "Related GitHub issues" → "To do" (6 open threads); the list was pruned 2026-06-12 down to live macOS-relevant ones — closed/dead/all-Windows threads removed. Check each remaining thread for an actual macOS sufferer before drafting.* Short, helpful, not spammy. Example:

   > Had this happen and built a recovery tool for it (macOS + Time Machine only): https://github.com/garrettmoss/restore-claude-history

   Don't post the same message verbatim everywhere; tweak per thread.

- [x] **3. File a new issue on `anthropics/claude-code`** if no good thread exists for it. Filed [#62272](https://github.com/anthropics/claude-code/issues/62272) on 2026-05-25 — "Chat JSONLs deleted from `~/.claude/projects/` despite `cleanupPeriodDays` set high — appears triggered by updates/restarts." Asks for any of: honor the setting, warn before deletion, surface in UI.

- [ ] **4. Reddit.** Candidates: r/ClaudeAI (most direct audience), r/MachineLearning (broader), r/macsysadmin (the Time Machine angle). One post per sub, spread over a few days. With the Desktop tool shipped, the story sharpens: "two scripts — one fixes 'Session not found on disk' with no external drive; the other restores deleted JSONLs from Time Machine if you've lost the content too." Lead with the no-drive angle — it's the lower-friction hook.

- [ ] **5. Hacker News** (news.ycombinator.com). Submit as `Show HN: restore-claude-history – recover deleted Claude Code chats (and repair Claude Desktop's "Session not found on disk")`. The Desktop angle is the stronger lead — "fix Claude Desktop's broken sidebar with a single-field metadata edit" is more concrete than the TM recovery story alone. HN front page = hundreds of GitHub stars in a day; most submissions vanish. Low cost, asymmetric upside. Best times to submit: weekday mornings US time.

- [ ] **6. dev.to** — write a short post walking through the bug, the prevention setting, and how the recovery works. Indexed by Google long-term; useful for anyone searching "claude code chat history deleted" months from now.

- [ ] **7. Friends + personal network.** People who use Claude Code and might lose chats themselves — the prevention setting alone is worth sharing even if they never need the recovery.

- [ ] **8. Stretch: reach out to Anthropic directly.** If any of the above gets traction, that's leverage to ask Anthropic to link the tool from their docs or surface `cleanupPeriodDays` in the UI. The point isn't credit; it's preventing future users from hitting this in the first place.

Tip: track which channels actually drove traffic (GitHub repo Insights → Traffic) so future-you knows what worked.

## Quiet Spotlight on snapshot mounts (v1.2) — paused

**Paused, not abandoned.** Measured with `tests/spotlight_harness.py` (2026-06-01) and confirmed the TM-drive CPU pain is outside the script's reach via the strategies we'd queued — the swarm scans the macOS-owned auto-mount, which we deliberately don't touch. Full findings in [NOTES.md](NOTES.md) ("Spotlight indexes the macOS-owned auto-mount..." entry) and [tests/logs/index.md](tests/logs/index.md). Harness + logs stay around for a future session — possibly its own repo (`spotlight-tamer`) if it outgrows this project.

**Open question for a future session** (not blocking): identify what re-enables indexing after `mdutil -d /Volumes/<TM drive>`. Install Apple's developer Logging profile to defeat `<private>` redaction in `log show --predicate 'subsystem == "com.apple.metadata"'`, run the disable, grep the log for the re-enable event. If we can name the daemon, we can decide whether the loud-but-honest plist-injection recipe (sudo, persists across mounts, system-wide) is worth documenting in README as an "if you really want to."

## Friendly Full Disk Access (FDA) error message

Today, if the terminal running [restore_claude_code.py](restore_claude_code.py) doesn't have Full Disk Access, `mount_apfs` fails with a cryptic `Operation not permitted` and the script keeps going (it's caught as a warn-and-skip in `mount_snapshot`). User sees the raw macOS error and no guidance.

Fix: in `mount_snapshot`, when `mount_apfs` fails and stderr contains `permission` (case-insensitive), print a clearer message before the existing warn and exit. Don't add a separate preflight check — `com.apple.TCC` probing adds a new failure mode unrelated to what we actually care about, and the real `mount_apfs` call is the only place this matters.

Suggested wording (keep `(Underlying error: ...)` so unrelated mount failures — typo'd snapshot name, missing device — still surface):

```
ERROR: Could not mount your Time Machine backup snapshot.

This usually means the terminal running this script doesn't have
Full Disk Access (FDA). Grant FDA to whatever terminal you are
running this from (Terminal.app, iTerm, VS Code, Cursor, etc.):

  System Settings → Privacy & Security → Full Disk Access → toggle on

Then re-run. (Underlying error: <captured mount_apfs stderr>)
```

Apply the same treatment in [tests/spotlight_harness.py](tests/spotlight_harness.py)'s mount call. Also worth a one-line note in README about needing FDA.

## Claude Desktop session recovery — deferred work

`restore_claude_desktop.py` `desktop-v0.1.0` shipped the Mode A surgical-edit path (verified on Claude Desktop 1.11187.4). The full failure-mode taxonomy, the `cliSessionId` mechanism, and the storage layout live in [NOTES.md](NOTES.md) → "Claude Desktop session recovery — failure-mode taxonomy." Two paths remain on the roadmap; both reuse snapshot logic from `restore_claude_code.py`.

- **v0.2.0 — Mode A snapshot fallback.** Triggers when the report shows NEEDS REVIEW (multiple transcript candidates within `--match-tolerance` of the metadata `createdAt`). Recipe: pull `local_*.json` from the newest TM or local snapshot where it has `cliSessionId` present, then copy over the live broken file using the `cp -p` + `chmod -N` + `chmod u+w` recipe from the main script. No NEEDS REVIEW cases on this machine — would be coded against a hypothetical, so wait for a user report or build a test fixture deliberately.
- **v0.3.0 — Mode B JSONL restore.** Triggers on LOST sessions. Diff `sessions-index.json` ↔ live `~/.claude/projects/<encoded>/` to enumerate missing JSONLs; restore them from snapshots using main-script logic; then run the Mode A path on the corresponding metadata. **Repair metadata alongside the transcript, not just the transcript** — per the orphan-cleanup risk @BasedGPT raised on [#62272](https://github.com/anthropics/claude-code/issues/62272#issuecomment-4554894518), restored JSONLs without matching metadata get re-deleted on the next cleanup sweep. Users with snapshot coverage reaching back far enough to catch the deletion are rare (maintainer's case had nothing past 2026-03-11). Defer until a user actually hits it.

When v0.2 lands, factor `Snapshot`, `mount_snapshot`, `unmount_if_ours`, `find_data_root`, and the encoded-project-name pre-rewrite into a `snapshots.py` module both scripts import. Don't do this pre-emptively per CLAUDE.md.

v0.3's synth-some-metadata shape is closer to [`BasedGPT/claude-code-session-recovery`](https://github.com/BasedGPT/claude-code-session-recovery/blob/main/tools/sessions/synth_session_metadata.py) than shipped Mode A was (Windows-targeted, but the JSONL-matching idioms transfer). He's already credited in README "See also"; if v0.3 actually lifts code from his repo, credit it inline at that point. Design notes in [personal-notes.md](personal-notes.md) → "Desktop recovery — design notes from BasedGPT's reference impl".

## Stretch: user-hosted Claude chat backups

A continuous, user-run backup of `~/.claude/projects/` so you don't have to rely on Time Machine (or any specific OS-level snapshot tool) to recover from a future deletion event.

- **Explicitly post-v1.** Ship the recovery tool, do the Desktop follow-up, *then* consider this. Easy to lose a week here.
- **Weakens the current pitch.** Today the script is "Time Machine + run this." Adding a backup feature means the story splits: "Time Machine, OR you installed our backup tool *before* the deletion." Most users won't have done the latter — so the recovery story stays cleaner if backups stay separate.
- **Probably a sibling project**, not a feature of this one. Different shape (daemon vs. one-shot), different audience (preventative vs. reactive).

**Starting point when we pick this up:** @ojura sketched a `SessionStart` hook on [#59248](https://github.com/anthropics/claude-code/issues/59248) — a small bash script that copies any `*.jsonl` from `~/.claude/projects/` to `~/.claude-session-backups/` on every session launch, only when the live file has grown (mtime-immune, shrink-safe). Wired in via `~/.claude/settings.json` under `hooks.SessionStart`. Worth using as the reference implementation for our `backup_claude_history.py` — credit ojura, then extend with: a real CLI, restore-from-backup verb, retention policy, optional macOS LaunchAgent for continuous (not just session-start) coverage, and cross-platform stat handling (his script already handles GNU vs. BSD `stat`). One concrete reason to prioritize this over "just set `cleanupPeriodDays: 36500`": per ojura, processes started with `--setting-sources local` or SDK sessions with `settingSources: []` (including autonomously spawned subagents) bypass the setting and fall back to the 30-day default. A SessionStart-driven backup sidesteps that whole class of bypass.
