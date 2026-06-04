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

## ~~Sequential mount/index/unmount refactor~~ (shipped v1.0.1, 2026-05-28)

Shipped sequential mount → index → restore → unmount in v1.0.1. Walks snapshots newest-first, dedupes (project, jsonl) pairs via a `seen` set in `main()` (relies on the JSONL append-only invariant: newest snapshot containing a file = largest version). Same for session subdirs — first writer wins.

**Partial improvement, not a cure.** Observed 2026-05-28 (4 snapshots, M-series Mac): no longer saw the 4-up CGPDFService line at 20–50% CPU each that the old parallel design produced — likely because we now only have one owned mount alive at any given moment. So the *concurrent* mount-time worker pile-up is genuinely reduced. But post-script, ~12 mdworker_shared + ~5 CGPDFService still spawned within 1s of exit with mds_stores at 60% CPU; best read is that the macOS-owned auto-mount (which is still mounted because we don't touch it) is what they're scanning. Sequential mounting bounded what *we* contributed; it can't quiet what the OS auto-mount keeps stirring up.

Why ship anyway: real reduction in concurrent worker count, simpler control flow, and the in-loop dedupe is a structural win regardless of Spotlight. Patch-version-only because the user-visible "CPU goes nuts when TM is plugged in" pain is still mostly there.

## ~~Local APFS snapshots as a primary recovery source~~ (shipped v1.1.0, 2026-05-28)

Shipped `--source=local|tm|both` (default `both`) in v1.1.0. With no TM drive plugged in, `find_tm_device()` now falls through silently and the script proceeds against local snapshots on `/System/Volumes/Data`. `find_data_root` handles the local-snapshot mountpoint layout (`<mp>/Users/...`, no `Data/` wrapper). Sequential mount loop merges both pools and sorts newest-first across the union; the `seen` dedupe still applies.

Bonus discovery: **mounting a local-volume snapshot does NOT trigger Spotlight reindex** — verified zero new mdworker_shared/CGPDFService workers post-mount (vs ~12+5 from a TM-drive mount). Apparently the live Data volume's existing index already covers the snapshot's blocks via APFS COW. So the local-snapshot path is both drive-free *and* Spotlight-quiet, captured in NOTES.md.

Reality check on coverage: Apple docs claim "hourly local snapshots, retained 24h," but that retention only kicks in if Time Machine runs automatically. Maintainer backs up manually and rarely — exactly one local snapshot exists on his machine, dated 2026-04-24, the date of his last manual backup. For users like him, local snapshots are "one extra recent safety net" rather than "rolling 24h coverage." Still useful — that snapshot caught everything he'd lost between then and the recovery work — just not the deep history.

Also shipped alongside: `--list-only` flag for machine-readable preview, and the test harness (`verify_restore.py`) now uses it to pick test files from the intersection of (on-disk) ∩ (in-snapshot) and assert restored size + mtime match the snapshot's, not the live file's.

## Quiet Spotlight on snapshot mounts (v1.2) — investigated 2026-06-01, **paused**

**Status: paused, not abandoned.** Spent a session measuring with `tests/spotlight_harness.py` and confirmed the CPU pain on the TM-drive path is **outside our script's reach via the strategies we'd queued.** Not blocking the main Claude-restoration script's roadmap, but the harness + logs + parked question all stay around for a future session that wants to pick this back up — possibly as its own repo if the spotlight-tamer work outgrows this project's scope. Detailed findings in [NOTES.md](NOTES.md) ("Spotlight indexes the macOS-owned auto-mount..." entry) and the harness logs at [tests/logs/index.md](tests/logs/index.md). Summary:

- Drive plugged + unlocked, *no script*: 11 CGPDFService at 195% CPU. The swarm starts before we run anything.
- Our temp snapshot mount is invisible to Spotlight (`mdutil -s` says "unknown indexing state"). Strategies targeting our mountpoint don't address what's actually being scanned.
- `mdutil -d /Volumes/<TM drive>` reaches the real off-state but **macOS auto-re-enables within the same syscall.** Re-enabler unidentified — `backupd` or a volume-mount hook are the leading suspects.

The cheap strategies originally queued here (`.metadata_never_index`, `tmutil addexclusion`, mount flags) all target our temp mountpoint, so they share the "wrong path" problem. Not worth investigating further without a different attack vector.

**Open question worth a future investigation session** (not blocking anything): identify what re-enables indexing after `mdutil -d`. Install Apple's developer Logging profile to disable `<private>` redaction in `log show --predicate 'subsystem == "com.apple.metadata"'`, then `sudo mdutil -d` the TM volume and grep the log for the re-enable. If we can name the daemon, we can decide whether disabling *it* is worth the user-visible cost.

The only known way to fully prevent the swarm today is Spotlight Privacy plist injection at the *macOS auto-mount path* (not our temp mount). That's `/Library/Preferences/com.apple.spotlight.plist` modification, requires sudo, persists across mounts, modifies system-wide Spotlight config. Too invasive for the script to do silently. Could be documented in README as an "if you really want to" recipe for the deep-history users.

## Friendly Full Disk Access (FDA) error message

Today, if the terminal running [restore_claude_history.py](restore_claude_history.py) doesn't have Full Disk Access, `mount_apfs` fails with a cryptic `Operation not permitted` and the script keeps going (it's caught as a warn-and-skip in `mount_snapshot`). User sees the raw macOS error and no guidance.

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

## Claude Desktop session recovery

The Claude Desktop app has an embedded Claude Code area that lists past sessions in its UI, but clicking them often shows **"Session not found on disk"** — same disappearing-chat problem as Claude Code CLI, different storage location.

Confirmed path: `~/Library/Application Support/Claude/claude-code-sessions/<group>/<project>/local_*.json` (verified 2026-05-27 — one group dir present on this machine with 11 `local_*.json` metadata entries).

Storage layout (verified 2026-06-04 on macOS):
- `~/Library/Application Support/Claude/claude-code-sessions/<acct-uuid>/<org-uuid>/local_*.json` — UI-layer metadata (one file per session). NOT transcript content.
- `~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl` — transcript content. Same location Claude Code CLI / VS Code use; shared store.
- `~/.claude/projects/<encoded-cwd>/sessions-index.json` — Desktop's authoritative manifest of what JSONLs *should* exist in this project, with firstPrompt + messageCount + dates. Written by Desktop. Discovered 2026-06-04; not present on every project (only confirmed on `data-of-being` so far — investigate when y-l-p is opened in Desktop next).
- `~/.claude/projects/<encoded-cwd>/<sessionId>/subagents/agent-*.jsonl` — subagent transcript fragments. Survive parent-JSONL deletion in at least some cases.

Ruled out as out-of-scope for transcript recovery (verified 2026-06-04):
- `~/Library/Application Support/Claude/claude-code/` — bundled CLI binary (`<version>/claude.app/`).
- `~/Library/Application Support/Claude/claude-code-vm/` — bundled VM runtime.
- `~/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin/` — extension config.

`local-agent-mode-sessions/<acct>/<org>/` does hold Cowork (agent-mode) session content but that's a separate surface — out of scope for the first cut.

### Investigation 2026-06-04 — failure modes + Mode A recipe proven

See NOTES.md → "Claude Desktop session recovery — failure-mode taxonomy" for the full writeup. Summary:

- **Mode A ("Session not found on disk" / "Message not found on disk") is a metadata-only failure, and the fix is one field.** Proven by hand 2026-06-04 on two `young-ladys-primer` sessions: adding `cliSessionId` (= JSONL filename UUID) to the broken `local_*.json` and removing `transcriptUnavailable` is sufficient. Desktop re-bloats the file with current-schema fields on next load but preserves `cliSessionId` and does not re-add `transcriptUnavailable`. So `transcriptUnavailable: true` is symptom, not cause — Desktop writes it when `cliSessionId` is missing at load time.
- **Mode B ("No messages yet") is a full content loss.** For `data-of-being`, all 5 referenced JSONLs were missing from every available snapshot (oldest 2026-03-11). Content gone before the snapshot window opens — unrecoverable on this machine. For other users with longer snapshot history, this might still recover via the existing `restore_claude_history.py` flow against `~/.claude/projects/`.
- **`sessions-index.json` is the linchpin for detection.** Authoritative manifest of what JSONLs *should* be in a project dir — so Mode B detection is "filenames in index, missing from disk," and Mode A is "JSONL on disk, metadata broken."

### Design (post-investigation)

**Strategy tier reversal vs. what we originally planned:** surgical edit is the *primary* path for Mode A; snapshot restore is the *fallback*. Most users will get a fix with no external drive required — meaningful for the publicity story.

- **Mode A primary — surgical edit.** For each broken `local_*.json`: find the matching JSONL by `createdAt` ↔ first-record `timestamp` (sub-second match on this machine; refuse on ambiguity rather than guessing); write `cliSessionId = <JSONL UUID>`; remove `transcriptUnavailable`; leave every other field alone. No TM drive needed.
- **Mode A fallback — snapshot restore.** Triggers when JSONL matching is ambiguous (multiple candidates within the createdAt window). Pull `local_*.json` from the newest TM/local snapshot where it has `cliSessionId` present. Sidesteps the match-ambiguity problem because the snapshot already names the right JSONL.
- **Mode B — JSONL restore + Mode A repair.** Detect via `sessions-index.json` ↔ disk diff. Restore the missing JSONLs from snapshots (same logic as `restore_claude_history.py`). Then run the Mode A path on the metadata. If JSONLs aren't in any snapshot, surface "not recoverable, here's what `sessions-index.json` says was there" — don't fail silently.
- **Preflight (both modes):** Refuse to mutate metadata while Claude Desktop is running. Verified 2026-06-04 — Desktop re-writes session files from in-memory state within seconds, clobbering edits and re-adding `transcriptUnavailable: true`. Detect a live `Claude.app/Contents/MacOS` process and tell the user to `Cmd-Q` Desktop first. Don't kill it ourselves.
- **First code milestone: read-only diagnostic.** Print, per project, which mode each session is in and what the recommended fix would be. No mutation. Validates detection + matching against real state before we touch any files.

This obsoletes BasedGPT's "synthesize metadata from scratch" approach for Mode A on the macOS side: we don't need to synthesize anything; Desktop's live metadata is fine except for one missing pointer. Reuse from his work shifts to JSONL-matching idioms and CLI structure, not the synth logic itself. Still credit the reference impl when the script lands.

### Subtask: metadata synthesis after a Time Machine restore

Raised by @BasedGPT on [#62272](https://github.com/anthropics/claude-code/issues/62272#issuecomment-4554894518) (and corroborated by @ShreeshaJay on [#48334](https://github.com/anthropics/claude-code/issues/48334)): JSONLs restored into `~/.claude/projects/` *without* a matching `local_*.json` entry in `claude-code-sessions/` get treated as orphans on the next cleanup pass and re-deleted. Originally observed on Windows; the path exists on macOS too, so the same risk almost certainly applies here.

This means the current `restore_claude_history.py` flow has a gap: a successful restore can be silently undone on the next sweep if metadata isn't synthesised alongside the JSONLs. Fixing this is the natural bridge into the broader Desktop recovery work — same files, same machine, same investigation.

Reference implementation: [`BasedGPT/claude-code-session-recovery` → `tools/sessions/synth_session_metadata.py`](https://github.com/BasedGPT/claude-code-session-recovery/blob/main/tools/sessions/synth_session_metadata.py). Windows-targeted; full metadata synthesis. Post-2026-06-04 investigation, our macOS Mode A path doesn't need to synthesize — the live broken file is fine except for one missing field — but his code is still the right reference for JSONL-matching idioms, CLI structure, and the orphan-cleanup risk this subtask is about. He gave permission to use it as the reference (and we gave him reciprocal permission on our largest-file + mtime-preservation code). Credit + link when this lands. I publicly committed to this being "next" in the [#62272 reply](https://github.com/anthropics/claude-code/issues/62272), so don't let it drift. Design notes pulled from his code are in [personal-notes.md](personal-notes.md) → "Desktop recovery — design notes from BasedGPT's reference impl".

## Stretch: user-hosted Claude chat backups

A continuous, user-run backup of `~/.claude/projects/` so you don't have to rely on Time Machine (or any specific OS-level snapshot tool) to recover from a future deletion event.

- **Explicitly post-v1.** Ship the recovery tool, do the Desktop follow-up, *then* consider this. Easy to lose a week here.
- **Weakens the current pitch.** Today the script is "Time Machine + run this." Adding a backup feature means the story splits: "Time Machine, OR you installed our backup tool *before* the deletion." Most users won't have done the latter — so the recovery story stays cleaner if backups stay separate.
- **Probably a sibling project**, not a feature of this one. Different shape (daemon vs. one-shot), different audience (preventative vs. reactive).

**Starting point when we pick this up:** @ojura sketched a `SessionStart` hook on [#59248](https://github.com/anthropics/claude-code/issues/59248) — a small bash script that copies any `*.jsonl` from `~/.claude/projects/` to `~/.claude-session-backups/` on every session launch, only when the live file has grown (mtime-immune, shrink-safe). Wired in via `~/.claude/settings.json` under `hooks.SessionStart`. Worth using as the reference implementation for our `backup_claude_history.py` — credit ojura, then extend with: a real CLI, restore-from-backup verb, retention policy, optional macOS LaunchAgent for continuous (not just session-start) coverage, and cross-platform stat handling (his script already handles GNU vs. BSD `stat`). One concrete reason to prioritize this over "just set `cleanupPeriodDays: 36500`": per ojura, processes started with `--setting-sources local` or SDK sessions with `settingSources: []` (including autonomously spawned subagents) bypass the setting and fall back to the 30-day default. A SessionStart-driven backup sidesteps that whole class of bypass.
