# TODO

Open work for this repo, ordered roughly by priority (top = next up). Add as needed.

## `backup_claude_history.py` — forward backup + restore (PLANNED, decided 2026-06-16)

A continuous, user-run backup of `~/.claude/projects/` so recovery no longer depends on Time Machine (manual trigger, drive plug-in, Spotlight CPU thrash). The new everyday path; the TM scripts become the deep failsafe.

**Locked decisions and rationale live in [NOTES.md](NOTES.md)** → the "Decided 2026-06-16: this becomes `backup_claude_history.py`" block (in-repo new script; existing scripts stay as-is; backup-restore and TM-restore separate, routed by docs not a code router; mirror + `manifest.json` layout; why a `SessionStart` hook fits; credit-don't-copy ojura). This section is the build checklist only — don't restate the why here.

### Scope split (versioned lanes, prefix `backup-`)

- **backup-v0.1.0 — prevention half (build first).** `install`/`uninstall` (merge a `SessionStart` hook into `~/.claude/settings.json`, don't clobber), the copy-on-grow backup engine writing the manifest, `status` (is the hook installed / when did it last run / anything stale), `list` (what's backed up, by project/date). Lock the mirror+manifest layout before installing the hook, so hook and tool agree from day one; install via the `install` verb once this lands. Engine seed = ojura's copy-on-grow hook (grows-only, mtime-immune, append-safe; runs on macOS unmodified) — archived in [personal-notes.md](personal-notes.md), write our own + credit him.
- **backup-v0.2.0 — restore half (the hard part).** `restore` a file/session/project back into `~/.claude/projects/`, **with metadata repair** so the next cleanup sweep doesn't re-delete it as an orphan (the @BasedGPT risk on [#62272](https://github.com/anthropics/claude-code/issues/62272#issuecomment-4554894518) — this is why restore is a real feature, not a `cp`). By the time we build this, v0.1 will have produced real backups to test restore against — a far better position than the Desktop v0.3 guesswork. Overlaps the Desktop script's metadata concerns; watch for shared substrate.
- **Maybe later:** `read` (peek at a backed-up transcript without restoring); a macOS LaunchAgent for non-session-start coverage (maintainer's `SessionStart` case is already covered; LaunchAgent is for users with background-sweep exposure).

## Publicize the repo

Higher leverage than another feature — one well-placed comment lands in front of people *actively searching* right now. Stronger story once `backup_claude_history.py` ships (a no-external-drive prevention tool, not just TM recovery), so this rises after that build.

Suggested order (lowest cost / highest signal first):

- [x] **1. Build the thread list.** Searched `anthropics/claude-code` for `cleanupPeriodDays`, `history deleted`, `lost chats`, `session not found`, `transcript missing`. Captured 16 threads grouped by priority; tracker now lives in [personal-notes.md](personal-notes.md) → "Thread tracker." *Done 2026-05-24.*

- [ ] **2. Comment on each of those issues.** 🟡 *In progress.* Live tracker — posted list and remaining queue — is in [personal-notes.md](personal-notes.md) → "Thread tracker" (gitignored; it's personal campaign state, not repo content). Check each remaining thread for an actual macOS sufferer before drafting. Short, helpful, not spammy. Example:

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

**Paused, not abandoned** (2026-06-01). The TM-drive CPU pain is outside the script's reach — Spotlight scans the macOS-owned auto-mount we deliberately don't touch. Likely its own repo (`spotlight-tamer`) if ever resumed — no real dependency on this project.

**One open question if ever resumed:** name the daemon that re-enables indexing right after `mdutil -d /Volumes/<TM drive>` — install Apple's developer Logging profile to defeat `<private>` redaction, run the disable, grep `com.apple.metadata` logs for the re-enable. If named, decide whether the loud plist-injection recipe is worth documenting in README.

## Claude Desktop session recovery — deferred work

`restore_claude_desktop.py` `desktop-v0.1.0` shipped the Mode A surgical-edit path (verified on Claude Desktop 1.11187.4). The full failure-mode taxonomy, the `cliSessionId` mechanism, and the storage layout live in [NOTES.md](NOTES.md) → "Claude Desktop: session-recovery failure-mode taxonomy." Two paths remain on the roadmap; both reuse snapshot logic from `restore_claude_code.py`.

- **v0.2.0 — Mode A snapshot fallback.** Triggers when the report shows NEEDS REVIEW (multiple transcript candidates within `--match-tolerance` of the metadata `createdAt`). Recipe: pull `local_*.json` from the newest TM or local snapshot where it has `cliSessionId` present, then copy over the live broken file using the `cp -p` + `chmod -N` + `chmod u+w` recipe from the main script. No NEEDS REVIEW cases on this machine — would be coded against a hypothetical, so wait for a user report or build a test fixture deliberately.
- **v0.3.0 — Mode B JSONL restore.** Triggers on LOST sessions. Diff `sessions-index.json` ↔ live `~/.claude/projects/<encoded>/` to enumerate missing JSONLs; restore them from snapshots using main-script logic; then run the Mode A path on the corresponding metadata. **Repair metadata alongside the transcript, not just the transcript** — per the orphan-cleanup risk @BasedGPT raised on [#62272](https://github.com/anthropics/claude-code/issues/62272#issuecomment-4554894518), restored JSONLs without matching metadata get re-deleted on the next cleanup sweep. Users with snapshot coverage reaching back far enough to catch the deletion are rare (maintainer's case had nothing past 2026-03-11). Defer until a user actually hits it.

**Why deferred (re-confirmed 2026-06-16):** neither can be honestly tested on this machine. v0.2 has zero NEEDS REVIEW cases — testing means inventing a fixture that only proves the code matches the fixture. v0.3's one genuinely LOST project (`data-of-being`) has no snapshot coverage reaching the deletion, so the only real case is the one where the recovery *source* doesn't exist. Shipping snapshot-restore that's never recovered a real file is a credibility risk for a tool whose pitch is "this actually recovered my data." Wait for a user report with coverage.

When v0.2 lands, factor `Snapshot`, `mount_snapshot`, `unmount_if_ours`, `find_data_root`, and the encoded-project-name pre-rewrite into a `snapshots.py` module both scripts import. Don't do this pre-emptively per CLAUDE.md.

v0.3's synth-some-metadata shape is closer to [`BasedGPT/claude-code-session-recovery`](https://github.com/BasedGPT/claude-code-session-recovery/blob/main/tools/sessions/synth_session_metadata.py) than shipped Mode A was (Windows-targeted, but the JSONL-matching idioms transfer). He's already credited in README "See also"; if v0.3 actually lifts code from his repo, credit it inline at that point. Design notes in [personal-notes.md](personal-notes.md) → "Desktop recovery — design notes from BasedGPT's reference impl".
