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

## Friendly Full Disk Access (FDA) error message — done 2026-06-16

When `mount_apfs` fails without FDA, [restore_claude_code.py](restore_claude_code.py)'s `mount_snapshot` now prints clear guidance and exits via `die()` (fatal — every snapshot mount fails the same way). Same treatment in [tests/spotlight_harness.py](tests/spotlight_harness.py); README documents the symptom→fix.

**Gotcha for the record:** `mount_apfs` without FDA emits `Operation not permitted` (EPERM) — the word "permission" never appears. The original spec's substring check (`"permission" in stderr`) would have missed it. Predicate is `"not permitted" in low or "permission" in low`. Unrelated mount failures (bad device, typo'd snapshot) still fall through to the existing warn-and-skip.

## Claude Desktop session recovery — deferred work

`restore_claude_desktop.py` `desktop-v0.1.0` shipped the Mode A surgical-edit path (verified on Claude Desktop 1.11187.4). The full failure-mode taxonomy, the `cliSessionId` mechanism, and the storage layout live in [NOTES.md](NOTES.md) → "Claude Desktop session recovery — failure-mode taxonomy." Two paths remain on the roadmap; both reuse snapshot logic from `restore_claude_code.py`.

- **v0.2.0 — Mode A snapshot fallback.** Triggers when the report shows NEEDS REVIEW (multiple transcript candidates within `--match-tolerance` of the metadata `createdAt`). Recipe: pull `local_*.json` from the newest TM or local snapshot where it has `cliSessionId` present, then copy over the live broken file using the `cp -p` + `chmod -N` + `chmod u+w` recipe from the main script. No NEEDS REVIEW cases on this machine — would be coded against a hypothetical, so wait for a user report or build a test fixture deliberately.
- **v0.3.0 — Mode B JSONL restore.** Triggers on LOST sessions. Diff `sessions-index.json` ↔ live `~/.claude/projects/<encoded>/` to enumerate missing JSONLs; restore them from snapshots using main-script logic; then run the Mode A path on the corresponding metadata. **Repair metadata alongside the transcript, not just the transcript** — per the orphan-cleanup risk @BasedGPT raised on [#62272](https://github.com/anthropics/claude-code/issues/62272#issuecomment-4554894518), restored JSONLs without matching metadata get re-deleted on the next cleanup sweep. Users with snapshot coverage reaching back far enough to catch the deletion are rare (maintainer's case had nothing past 2026-03-11). Defer until a user actually hits it.

**Why these stay deferred — re-confirmed 2026-06-16 (can't honestly test either on this machine):**
- **v0.2 (NEEDS REVIEW)** has zero cases on this machine. Testing it means manufacturing an ambiguous fixture (two near-simultaneous JSONLs + broken metadata + a snapshot with a good copy) — which only proves the code matches the fixture you invented to exercise it, not that it does the right thing on real ambiguity.
- **v0.3 (LOST / Mode B)** can't be round-tripped here at all. `young-ladys-primer` JSONLs are present and were never lost (it was a Mode A `cliSessionId`-stripping case, nothing to restore). The one genuinely LOST project, `data-of-being`, has **no TM/local-snapshot coverage reaching back to the deletion** — so the only real LOST case is precisely the one where the recovery *source* doesn't exist. Shipping snapshot-restore + metadata-synthesis that has never once recovered a real file is a credibility risk for a tool whose pitch is "this actually recovered my data." Wait for a user report with coverage (gives a reason, a real fixture, and someone to verify with).

When v0.2 lands, factor `Snapshot`, `mount_snapshot`, `unmount_if_ours`, `find_data_root`, and the encoded-project-name pre-rewrite into a `snapshots.py` module both scripts import. Don't do this pre-emptively per CLAUDE.md.

v0.3's synth-some-metadata shape is closer to [`BasedGPT/claude-code-session-recovery`](https://github.com/BasedGPT/claude-code-session-recovery/blob/main/tools/sessions/synth_session_metadata.py) than shipped Mode A was (Windows-targeted, but the JSONL-matching idioms transfer). He's already credited in README "See also"; if v0.3 actually lifts code from his repo, credit it inline at that point. Design notes in [personal-notes.md](personal-notes.md) → "Desktop recovery — design notes from BasedGPT's reference impl".

## `backup_claude_history.py` — forward backup + restore (PLANNED, decided 2026-06-16)

Promoted from "stretch / sibling project" to a committed **in-repo** project. A continuous, user-run backup of `~/.claude/projects/` so recovery no longer depends on Time Machine (manual trigger, drive plug-in, Spotlight CPU thrash). The new everyday path; the TM scripts become the deep failsafe.

### Decisions locked this session

- **Lives in this repo, as a new script** — not a sibling project. The repo is named `restore-claude-history`, not `restore-from-time-machine`; backup + restore-from-your-own-backups fits the name *better* than today's TM-only scope. Sits alongside the existing standalone scripts per CLAUDE.md's "single-file scripts, share substrate only when a second consumer needs it."
- **Leave the existing scripts as-is for now.** `restore_claude_code.py` stays the TM/APFS failsafe, unchanged and still-tested. `restore_claude_desktop.py` unchanged. Once the backup layer is up and running, *then consider* merging restore paths or renaming the old scripts for clarity (e.g. signalling "TM failsafe" in the name). Don't pre-merge.
- **Restore stays separate, routed by docs, not a code-path router.** `backup_claude_history.py restore` reads *your own backups* (the default people reach for). `restore_claude_code.py` stays the TM/snapshot failsafe for chats older than your backups reach. The docs route between them — we do NOT rewrite the TM script into a "check backups first, fall back to TM" router (couples the tested script to a backup format, gives it two code paths). Two scripts, two sources, one job each.
- **On-disk layout: mirror + manifest.** Mirror the `~/.claude/projects/` tree into `~/.claude-session-backups/` (browsable, like ojura's), PLUS a `manifest.json` tracking per-file `{bytes, src_mtime, backed_up_at}`. The manifest is what makes `status`/`list` fast and — critically — lets the tool *detect silent hook failure* (a backup that never ran is the classic backup killer). This layout must be locked before the hook goes in, so hook and tool agree from day one.
- **Don't install the hook yet.** Holding off is correct, not procrastination: the hook writes the layout, so the layout/manifest design ships first. Install via the tool's `install` verb once v0.1 lands.

### Why this over "just set `cleanupPeriodDays: 36500`"

Per @ojura on [#59248](https://github.com/anthropics/claude-code/issues/59248): processes started with `--setting-sources local`, and SDK sessions with `settingSources: []` (including autonomously spawned subagents), bypass the setting and fall back to the 30-day default. A `SessionStart`-driven backup sidesteps that whole class of bypass. (Maintainer should still run normal full-machine backups — this tool is deliberately Claude-Code-specific, not a general backup.)

### Scope split (versioned lanes, prefix `backup-`)

- **backup-v0.1.0 — prevention half (build first).** `install`/`uninstall` (merge a `SessionStart` hook into `~/.claude/settings.json`, don't clobber), the copy-on-grow backup engine writing the manifest, `status` (is the hook installed / when did it last run / anything stale), `list` (what's backed up, by project/date). Hook can be installed as soon as this lands and the layout is locked.
- **backup-v0.2.0 — restore half (the hard part).** `restore` a file/session/project back into `~/.claude/projects/`, **with metadata repair** so the next cleanup sweep doesn't re-delete it as an orphan (the @BasedGPT risk on [#62272](https://github.com/anthropics/claude-code/issues/62272#issuecomment-4554894518) — this is why restore is a real feature, not a `cp`). By the time we build this, v0.1 will have produced real backups to test restore against — a far better position than the Desktop v0.3 guesswork. Overlaps the Desktop script's metadata concerns; watch for shared substrate.
- **Maybe later:** `read` (peek at a backed-up transcript without restoring); a macOS LaunchAgent for non-session-start coverage (maintainer's usage — WiFi-off-when-idle, deletions only seen on app restart — means a `SessionStart` hook already covers his case; LaunchAgent is for users with background-sweep exposure).

### Reference implementation

@ojura's `SessionStart` hook on [#59248](https://github.com/anthropics/claude-code/issues/59248) is the seed for the backup engine: copies `*.jsonl` from `~/.claude/projects/` to `~/.claude-session-backups/` only when the live file has **grown** (the JSONL is append-only, so high-water-mark copy is lossless; grew-only keying is mtime-immune AND shrink-safe). **It already runs on macOS unmodified** — its `sz()` helper does `stat -c%s || stat -f%z` (GNU || BSD/macOS), so no port needed. Archived verbatim in [personal-notes.md](personal-notes.md) → "@ojura's comment on #59248". **Write our own implementation, credit @ojura, link the comment — don't copy-paste** (his own stated-safe preference, captured in personal-notes.md).

### Note on Claude Desktop chats

The backup protects **Claude Code transcripts** (`~/.claude/projects/*.jsonl`) — local, authoritative, deletable, and what gets swept. It does NOT cover Claude **Desktop sidebar chats**: verified 2026-06-16 that those are not stored as local JSON under `~/Library/Application Support/Claude/` (only config/cache lives there) — they're server-side on Anthropic's infra, synced down, not the user's to lose locally. Desktop-launched Claude *Code* sessions still land in `~/.claude/projects/`, so the hook covers those too.
