# Notes

Design rationale and lessons learned from building `restore_claude_code.py`. The README covers usage; this is for anyone who wants to understand *why* the script makes the choices it does — including future-you, six months from now, wondering "why am I parsing this in such a weird way?"

## Strategy: prevention vs. restoration

Chats disappear for more than one reason. The documented one is `cleanupPeriodDays` in `~/.claude/settings.json` — a positive integer (default 30) that Claude Code reads on startup, then deletes any JSONL older than that. The default is too aggressive, the setting isn't exposed in the UI, and there's no warning before deletion.

Beyond the documented cleanup, **app updates appear to be the most-reported trigger for chat loss**, including in cases where the user had explicitly set `cleanupPeriodDays` to a high value. Pattern across the issue tracker:

- [#41458](https://github.com/anthropics/claude-code/issues/41458) — `cleanupPeriodDays: 99999` set, 490 sessions deleted anyway.
- [#38055](https://github.com/anthropics/claude-code/issues/38055) — "Minor version update permanently deletes chat history and scheduled tasks."
- [#12908](https://github.com/anthropics/claude-code/issues/12908) — "Conversation History disappeared after update."
- [#38691](https://github.com/anthropics/claude-code/issues/38691) — "All sessions lost after Claude Desktop update on Windows (data intact on disk)."
- [#48334](https://github.com/anthropics/claude-code/issues/48334) — "Desktop app update deletes session history."

These are user reports, not Anthropic-confirmed root causes — but the pattern is consistent enough that any prevention story needs to assume updates can ignore the setting. (Why updates specifically — the mtime mechanism and the close→reopen pattern — is its own section below.)

So there are two layers.

**Prevention** is one line in `~/.claude/settings.json`:
```json
"cleanupPeriodDays": 36500
```
~100 years; no documented upper bound. Set this on every machine. It *should* defang the documented cleanup in the common path, but it is a stopgap, not a panacea:

- **Avoid `cleanupPeriodDays: 0`.** It reads as "off" but means *delete everything now* — the cutoff resolves to "now" and a separate check treats `0` as "don't persist new sessions either." Per [@ojura on #59248](https://github.com/anthropics/claude-code/issues/59248).
- **The setting is bypassed by some session types.** Processes started with `--setting-sources local`, and SDK sessions with `settingSources: []` (including autonomously spawned subagents), don't read `~/.claude/settings.json` and fall back to the 30-day default. So even with `36500` set globally, a subagent or SDK-driven run can still wipe your transcripts. See [#41458](https://github.com/anthropics/claude-code/issues/41458), [#45735](https://github.com/anthropics/claude-code/issues/45735), and [@ojura on #59248](https://github.com/anthropics/claude-code/issues/59248).
- **App updates may ignore the setting entirely.** See the issues listed above.

A forward-looking backup is the natural complement here — a `SessionStart` hook that copies JSONLs out of `~/.claude/projects/` on every session launch, before the next cleanup pass can touch them. @ojura sketched one on [#59248](https://github.com/anthropics/claude-code/issues/59248) (a small bash script wired into `~/.claude/settings.json`).

**Decided 2026-06-16: this becomes `backup_claude_history.py`, a committed in-repo script** (not the "stretch / sibling project" it was first scoped as). The shape, locked that session:

- **Backup (prevention) and TM restore (failsafe) stay separate, routed by docs.** `backup_claude_history.py restore` reads the user's own continuous backups — the everyday path. `restore_claude_code.py` stays the Time Machine / APFS failsafe for chats older than the backups reach, unchanged. We deliberately do *not* merge the TM script into a "check backups first, fall back to TM" router: that would couple a tested, working script to a backup format and give it two code paths. Two scripts, two sources, one job each; the docs do the routing. (Renaming the old scripts for clarity, e.g. to signal "TM failsafe," is a *later* consideration once the backup layer is running — not now.)
- **On-disk layout is mirror + manifest.** Mirror the `~/.claude/projects/` tree into `~/.claude-session-backups/`, plus a `manifest.json` of per-file `{bytes, src_mtime, backed_up_at}`. The manifest exists to detect *silent hook failure* — a backup that quietly stopped running is the classic way backups betray you. Layout locks before the hook is installed, so hook and tool agree from day one.
- **Why a hook fits the maintainer's case specifically:** he runs WiFi-off when idle and only ever observes deletions on app close/restart — there is no background-sweep window for him, so a `SessionStart` hook (fires on reopen, right after the close that triggers the sweep) captures the high-water mark before the next sweep. Users with background/auto-update exposure would want the optional LaunchAgent instead. The hook's one residual gap: content written in a session that's then closed-and-swept *before* the next reopen isn't captured until reopen — acceptable for a stopgap; LaunchAgent closes it.
- **Credit, don't copy:** write our own implementation of ojura's copy-on-grow logic, credit him, link [#59248](https://github.com/anthropics/claude-code/issues/59248). His snippet already runs on macOS unmodified (`stat -c%s || stat -f%z`). Verbatim archive + his stated-safe "write your own + cite" preference in [personal-notes.md](personal-notes.md).

Full versioned roadmap (backup-v0.1 prevention half, backup-v0.2 restore-with-metadata-repair) in [TODO.md](TODO.md).

**Restoration** is [`restore_claude_code.py`](restore_claude_code.py). It assumes the worst has already happened and pulls your chats back out of Time Machine. It's what catches you when prevention fails — which, given the track record, is a "when" not an "if."

## Mechanism: why updates seem to trigger this

**Update 2026-05-25:** [@ojura on #59248](https://github.com/anthropics/claude-code/issues/59248) identified one likely mechanism — almost certainly not the only one, given how long and how many ways this bug has manifested across updates: `cleanupOldSessionFiles` in `src/utils/cleanup.ts` deletes any `*.jsonl` whose **filesystem mtime** is older than `cleanupPeriodDays` ago — *not* the timestamp of the last message inside the file. Because mtime is externally mutable, anything that touches it without preserving the original flips a current session into "looks old, delete it" territory:

- `cp` without `-p`, `tar -x`, `rsync` without `-a`
- Cloud sync clients (Dropbox, iCloud) that rewrite files on conflict
- Any script that normalizes mtimes — including, ironically, scripts written *to repair* the picker's chronology

This is also why `restore_claude_code.py` goes out of its way to preserve the snapshot's original mtime and explicitly re-stamp after any retry (NOTES step 5 below). If a restore landed with a fresh `now` mtime, the next cleanup pass would happily delete months of work all over again.

That said, the mtime story doesn't explain everything. Even with the flag set high *and* mtimes preserved, sessions still vanish around updates. The precipitating event, in my experience, has not been "I left chats sitting around for 30+ days and `cleanupPeriodDays` finally got them." It's been: **I closed VS Code, reopened it, and the chats were gone.**

This has happened multiple times on this machine, even with `cleanupPeriodDays` set to a high value. The common factor across every occurrence is that *something updated* between close and reopen — but I can't always tell *what*. Candidates I've ruled in and not yet ruled out:

- **The Claude Code CLI updating** (it self-updates frequently and quietly).
- **The Claude Code VS Code extension updating** (extensions auto-update by default).
- **The extension or its host process restarting** — even without a version change, a restart appears to be enough to trip something.
- **VS Code itself updating.**
- **Claude Desktop updating** (when it's running in parallel; it shares some local state).

I haven't isolated which of these is sufficient on its own — I'd need to disable auto-updates on each surface and reproduce, which is more work than I've done. But several of the GitHub issues listed at the top of this file describe the same shape: close → update → reopen → chats are gone.

The practical takeaway: **don't treat `cleanupPeriodDays` as the only line of defense.** If you've been using Claude Code for more than a few weeks and care about the transcripts, assume an update can wipe them at any time, and have Time Machine running. This tool is the catch when that happens.

If you've reproduced this with a known-isolated trigger (just the CLI updating, just the extension, etc.), I'd genuinely like to know — open an issue on the repo or comment on the relevant `anthropics/claude-code` thread.

## Procedure: hand-verified recovery sequence

Before writing any code, we worked through a real recovery in a Claude Code session. The script automates exactly this sequence:

1. **List APFS snapshots on the TM volume.** `diskutil apfs listSnapshots /dev/diskNsM` gives names like `com.apple.TimeMachine.2026-04-24-205237.backup`.

2. **Mount each snapshot.** macOS only auto-mounts one snapshot at a time via `/Volumes/.timemachine/<UUID>/...`. To access more, mount yourself:
   ```
   mkdir -p /tmp/tm-<label>
   mount_apfs -s com.apple.TimeMachine.<timestamp>.backup /dev/diskNsM /tmp/tm-<label>
   ```
   Read-only, no sudo needed if you have Full Disk Access.

3. **Find the Claude project dir inside each snapshot.** Path is `<mount>/<timestamp>.backup/Data/Users/<user>/.claude/projects/<encoded-project>/`. The leading `Data/` is the APFS data-volume firmlink — don't omit it.

4. **For each JSONL filename, pick the largest version across all snapshots.** JSONLs are append-only logs. Bigger file = longer conversation = more complete.

5. **Copy with mtime preservation, and re-stamp after any re-copy.** `cp -p` keeps the snapshot's original mtime — otherwise VS Code's "Recent chats" picker sorts restored files as "just now." Important gotcha: if a file fails on the first copy (e.g. ACL conflict) and you re-copy after stripping the ACL, mtime preservation doesn't always survive the second pass. Explicitly re-stamp.

6. **Strip ACLs after copy.** TM snapshot files carry an inherited ACL (`group:everyone deny write,delete,append,writeattr,writeextattr,chown`). This sticks to the copy and blocks future overwrites. `chmod -N <file>` removes the ACL. `chmod u+w <file>` ensures the user write bit.

7. **Restore subdirectories too.** `~/.claude/projects/<project>/<session-uuid>/subagents/` contains subagent transcripts. Use `cp -R`.

8. **Unmount everything on exit.** Use a trap (or `try/finally` in Python) so cleanup runs even on Ctrl-C or error.

## Gotchas: things the script silently works around

These are the things the script is silently working around. Document them here so they don't get re-discovered.

- **Spotlight indexes the macOS-owned auto-mount of APFS Time Machine volumes the moment you unlock the drive — before any script runs — and macOS auto-re-enables indexing if you try to turn it off.** Measured 2026-06-01 with `tests/spotlight_harness.py`:
  - Drive plugged + unlocked, *no mount, no script*: 11 CGPDFService processes at 195% summed CPU, mdworker_shared 15→31 procs. Pure macOS reaction to drive presence.
  - Mounting one of our temp snapshots adds no measurable churn on top of this — the workers are scanning the macOS-owned `/Volumes/Sapphire Time Machine` auto-mount (or your equivalent), not our `/tmp/sh-harness-*/...` mount. `mdutil -s` on our mount returns "unknown indexing state," meaning Spotlight has no record of it.
  - `mdutil -i off /Volumes/<TM drive>` only downgrades to `kMDConfigSearchLevelFSSearchOnly` (name search retained, content indexing off), not full off.
  - `mdutil -d /Volumes/<TM drive>` reaches the real `kMDConfigSearchLevelOff`, briefly pauses workers (~10–20s observed) — *and is auto-re-enabled before mdutil's own status poll returns.* mdutil prints `Indexing enabled.` on the very next line. We don't yet know which process is re-enabling; candidates are `backupd`, `fseventsd`-tied volume-state hooks, or Spotlight's own self-healing for protected volume types. Apple's Spotlight Privacy UI deliberately refuses to add TM volumes to the exclusion list, consistent with there being a whitelist somewhere that makes them un-disable-able by normal means.
  - **Practical implications:** the CPU pain is the macOS auto-mount, not us. Mitigations open to the script (mdutil flags from the temp mountpoint, `.metadata_never_index` markers, mount-time flags) target the wrong path. Real fixes would require either Spotlight Privacy plist injection at `/Volumes/<TM drive>` (sudo, persists across mounts, modifies user system state — too invasive to ship), or staying off the TM drive entirely. v1.1's local-snapshot path does exactly that for the common case. **Mitigation if you must use the TM drive: be fast** — mount, restore, unmount, eject. The swarm is fundamentally outside our control.

- **Sequential mounting (v1.0.1) bounds what *we* contribute, but can't quiet the auto-mount.** Earlier versions mounted every snapshot up-front; v1.0.1 mounts one at a time, walks it, unmounts, moves on. This *did* visibly reduce the concurrent-mount worker pile-up on the TM drive (no more 4-up CGPDFService at 20–50% CPU each on a 4-snapshot drive). But the residual churn after script exit is the macOS-owned auto-mount we deliberately don't touch — see the entry above — so sequential mounting can't reach it. (v1.2 investigation paused, not abandoned; harness in `tests/spotlight_harness.py`.)

- **Local APFS snapshots (v1.1) are tied to Time Machine activity, not a separate hourly cron.** Apple docs describe "hourly local snapshots retained 24h," and that's true *if Time Machine is configured to run automatically*. If you back up manually and rarely (as the maintainer does), local snapshots fire only when TM runs — so your "safety net" is effectively one snapshot from your last manual backup, not a rolling 24-hour window. Verified 2026-05-28 on a machine that hadn't backed up since 2026-04-24: exactly one local snapshot, dated to that day. Sized accordingly when you tell users what local-snapshot recovery can do for them.

- **Local-volume snapshot mounts do NOT trigger Spotlight reindex** (verified 2026-05-28). Mounting a `.local` snapshot of `/dev/disk?s?` (the boot Data volume) produced zero new mdworker_shared or CGPDFService workers. Best read: the live Data volume is already indexed and APFS COW means the snapshot exposes the same blocks, so Spotlight has nothing new to chew on. This is the silver lining behind the v1.1 work — the local-snapshot path is both drive-free *and* Spotlight-quiet, which the TM-drive path is neither.

- **Local snapshots have a different mountpoint layout than TM-drive snapshots.** TM-drive mounts give `<mp>/<ts>.backup/Data/Users/...` (with a `Data/` firmlink wrapper). Local-volume snapshots are snapshots of the Data volume *itself*, so the layout is `<mp>/Users/...` directly — no `Data/` wrapper, no `<ts>.backup/` wrapper. `find_data_root` probes all three layouts (TM-direct, TM-auto-mount, local-direct).

- **macOS sometimes pre-mounts snapshots at `/Volumes/.timemachine/<UUID>/<ts>.backup/`.** Trying `mount_apfs` on those fails with `Resource busy`. The script detects existing mounts and uses them directly rather than trying to remount.

- **The auto-mount path has a doubled-`.backup` layout** (`<mp>/<ts>.backup/<ts>.backup/Data/...`) different from what `mount_apfs` produces yourself (`<mp>/<ts>.backup/Data/...`). The script probes both.

- **`cp -p` fails with "Permission denied" when the destination already exists** with the read-only ACL inherited from a previous copy. Strip the ACL on the destination first (`chmod -N`).

- **`mount_apfs` without Full Disk Access reports "Operation not permitted" (EPERM) — the word "permission" never appears.** A naive `"permission" in stderr` FDA-detection check silently never fires. The predicate must also match `"not permitted"`: `"not permitted" in low or "permission" in low`. Same treatment lives in `tests/spotlight_harness.py`.

- **`tmutil` has no mount/unmount verbs.** It lists snapshots, restores files (limited), but does not let you mount one on demand. `mount_apfs` is the lower-level escape hatch.

- **`diskutil info <dev>` includes snapshot names** in its output. If you grep that output for "Time Machine" to identify the TM volume, you'll match the internal disk too (which has local TM snapshots). Use `tmutil destinationinfo` instead — it's purpose-built.

- **`diskutil apfs listSnapshots` formats output as an ASCII tree** with leading pipe characters. A naive `^\s*Name:` regex only matches the *last* block (which uses spaces, not pipes, for its prefix). Match `Name:` anywhere on the line, not just after whitespace.

- **`os.getlogin()` can return `root`** in non-TTY contexts (sudo, nested shells, some CI). `getpass.getuser()` reads `LOGNAME`/`USER` env vars and is more reliable.

- **argparse rejects values that start with `-`** because it thinks they're flags. Encoded Claude project names all start with `-`. The script pre-rewrites `--project FOO` → `--project=FOO` in argv before argparse sees it.

- **macOS ships bash 3.2** (frozen for licensing reasons since 2006). No associative arrays. We tried writing this in bash first; the resulting code was readable only via `sort | awk` pipeline tricks and a `trap` cleanup that turned out to be buggy. Python made all of this go away.

- **Claude Code re-appends identical `ai-title` events to JSONLs on every session resume, bumping mtime each time.** Observed in `young-ladys-primer` 2026-05-28: three JSONLs had identical second-precision mtimes (`May 21 20:53:03`) that didn't match their in-file message timestamps (May 10–19). The files each had 41–67 `ai-title` events appended over time, mostly identical to one prior — i.e. Claude is regenerating the same title and rewriting the line on every resume (or similar trigger). Two consequences for anyone reading restored files: (a) mtime is a poor proxy for "when the user last touched this chat" — use the last in-file `timestamp` field for chronological display; (b) this is concrete evidence for [@ojura's argument on #59248](https://github.com/anthropics/claude-code/issues/59248#issuecomment-4535863101) that retention should key off in-file timestamps, not stat.mtime — Claude's own code is mutating mtime in ways unrelated to user activity. Not something the restore script can fix; documented here so future readers don't blame the restore for "wrong" mtimes.

## Claude Desktop: session-recovery failure-mode taxonomy

Investigated 2026-06-04 on this machine. Survey of Claude Desktop's session-storage surfaces revealed **two distinct failure modes** that need different fixes. Both show up in the UI as "broken sessions," but only one is recoverable from snapshots.

**Verified Claude Desktop compatibility:** `restore_claude_desktop.py` `desktop-v0.1.0` end-to-end repair confirmed working on Claude Desktop **1.11187.4** (verified 2026-06-08, both pre- and post-auto-update on the same machine). The single-field recipe survived an in-place Desktop update — repaired sessions stayed healthy; no schema changes that broke detection. Treat the version as a known-good marker, not a ceiling: if future Desktop releases break the recipe, this is the last known-working version to bisect against. Recheck on any major Desktop update before assuming the script still applies.

**Mode A — "Session not found on disk" / "Message not found on disk":** Metadata in `~/Library/Application Support/Claude/claude-code-sessions/<acct>/<org>/local_*.json` is present but damaged. The JSONL transcript is **still on disk** in `~/.claude/projects/<encoded-cwd>/`. Verified on this machine for `young-ladys-primer`: both `[X froze]` and the corresponding working session had on-disk JSONLs whose first-record timestamps matched the metadata's `createdAt` within 1-3 seconds, but Desktop couldn't link them because `cliSessionId` had been stripped from every metadata file (11/11). Snapshot diff (live vs 2026-04-17) showed `cliSessionId` was *present* in every historical metadata file — i.e. Desktop stripped it during some later migration/cleanup pass. The same diff showed the files also bloated from 400–2,500 bytes (compact, working) to 10–11 KB (mostly inlined `enabledMcpTools` / `remoteMcpServersConfig`), with `transcriptUnavailable: true` newly set on 8 of 11.

**The fix is a single field.** Verified by hand 2026-06-04 on two `young-ladys-primer` sessions (`local_ece5671d-*` via schema rollback from the 2026-04-17 snapshot, then `local_229c1e5b-*` via surgical edit on the live file): **adding `cliSessionId` to the broken `local_*.json` and removing `transcriptUnavailable` is sufficient.** On next Desktop launch, the transcript loads and Desktop re-stamps the file with current schema bloat (`enabledMcpTools`, `remoteMcpServersConfig`, etc.) — but it preserves `cliSessionId` and does *not* re-add `transcriptUnavailable`. So `transcriptUnavailable: true` is **symptom, not cause**: Desktop writes it when `cliSessionId` is missing at load time. The schema-rollback worked not because the old schema was right, but because that schema happened to carry `cliSessionId`.

The right `cliSessionId` value is the JSONL filename (UUID-without-extension) in `~/.claude/projects/<encoded-cwd>/`. Linkage is via `createdAt` (metadata) ↔ first-record `timestamp` (JSONL): matches on this machine were sub-second; nearest non-matches were >16 days away, so the window has plenty of margin in practice — but the script needs to refuse to act on ambiguous matches and fall back to historical-metadata restore for those.

**Gotcha: Desktop must be fully quit before editing.** Verified the same day — copying the snapshot file over the live file while Desktop was open (window closed but app still running) had no visible effect; within seconds Desktop re-wrote the file from in-memory state, clobbering our edit and re-adding `transcriptUnavailable: true`. Only after `Cmd-Q` quit + edit + relaunch did the session load. Script preflight: detect a running `Claude.app/Contents/MacOS` process and refuse to mutate metadata until the user quits it. Don't kill it ourselves — too invasive.

**Mode B — "No messages yet":** Both metadata and JSONL transcript are gone. The project directory in `~/.claude/projects/<encoded-cwd>/` exists but contains only session subdirectories (subagent transcripts) and a top-level `sessions-index.json`. Verified on this machine for `data-of-being`: 5 JSONL transcripts referenced by `sessions-index.json` were missing from every available TM snapshot back to 2026-03-11 (oldest). The actual conversation text appears nowhere on disk in any reachable location (grep for known firstPrompt strings across `~/.claude/`, `~/Library/Application Support/Claude/`, all 5 snapshots — zero hits). For this machine, deletion happened before the snapshot window opens, so the content is **genuinely unrecoverable** — `sessions-index.json` and the subagent fragments document what existed, but the parent transcripts are gone.

A few new artifacts surfaced during this investigation that the original recovery work hadn't touched:

- **`sessions-index.json` lives inside each project dir at `~/.claude/projects/<encoded>/sessions-index.json`** (not in Desktop's AppSupport tree). Contains per-session: `sessionId` (= JSONL UUID filename), `fullPath`, `fileMtime`, `firstPrompt` (truncated to ~200 chars), `messageCount`, `created`, `modified`, `gitBranch`, `projectPath`, `isSidechain`. It's an authoritative manifest of *what JSONLs should be there*, written by Claude Desktop's Claude Code area. Useful in two ways: (1) for Mode A recovery, it lets us link a JSONL to a metadata file without timestamp-matching; (2) for Mode B, it tells us exactly what's missing, even when the JSONLs themselves are unrecoverable.

- **Subagent transcripts survive in `<session-uuid>/subagents/agent-*.jsonl`** even when the parent JSONL is deleted. Verified on `data-of-being` 2026-06-04 — three of five missing sessions still had their subagent files (1-135 KB each). Only contains the subagent's slice, not the parent conversation, so it's a partial-context hint, not a transcript replacement.

- **The Desktop metadata schema has changed.** All 11 live `local_*.json` files on this machine were missing `cliSessionId`; all 11 snapshot files from 2026-04-17 had it. Unclear if this is sunset (schema migration), stripped-on-error (cleanup pass), or a bug. Don't assume the field will be there on a freshly-created session — should test on Desktop > Claude Code > New Session before relying on it.

- **Out of scope for transcript recovery: `claude-code/`, `claude-code-vm/`, `local-agent-mode-sessions/skills-plugin/`.** TODO.md flagged these as "may matter" but the survey ruled them out: `claude-code/` and `claude-code-vm/` hold the bundled CLI/VM binaries (not session data), and `skills-plugin/` is extension config. `local-agent-mode-sessions/<acct>/<org>/` does hold Cowork (agent-mode) session content alongside metadata, but that's a different surface from Claude Code chat sessions and out of scope for this tool's first cut.

**Implementation status** (the prose above explains the mechanism; this is just where each path stands):

- **Mode A — surgical edit.** *Implemented in v0.1.0.* Primary path; no TM drive needed.
- **Mode A fallback — snapshot restore on ambiguous match.** *Deferred to v0.2.0.* Surfaced in the report as "NEEDS REVIEW," no action taken.
- **Mode B — `sessions-index.json` diff + JSONL restore + Mode A repair.** *Deferred to v0.3.0.* Surfaced as "LOST" with a callout pointing users at `restore_claude_code.py` for the transcript-restore half.
- **Preflight (both modes).** *Implemented in v0.1.0.* Refuses to act while Desktop is running (`pgrep -f Claude.app/Contents/MacOS`).

## Origin

This script was extracted from a real recovery session: months of Claude Code chats on a long-running personal project, gone overnight after an update. Working through the recovery by hand surfaced every gotcha listed above. The code here is the distilled, automated version of that work.
