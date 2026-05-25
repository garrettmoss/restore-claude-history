# Notes

Design rationale and lessons learned from building `restore_claude_history.py`. The README covers usage; this is for anyone who wants to understand *why* the script makes the choices it does — including future-you, six months from now, wondering "why am I parsing this in such a weird way?"

## Prevention vs. restoration

Chats disappear for more than one reason. The documented one is `cleanupPeriodDays` in `~/.claude/settings.json` — a positive integer (default 30) that Claude Code reads on startup, then deletes any JSONL older than that. The default is too aggressive, the setting isn't exposed in the UI, and there's no warning before deletion.

Beyond the documented cleanup, **app updates appear to be the most-reported trigger for chat loss**, including in cases where the user had explicitly set `cleanupPeriodDays` to a high value. Pattern across the issue tracker:

- [#41458](https://github.com/anthropics/claude-code/issues/41458) — `cleanupPeriodDays: 99999` set, 490 sessions deleted anyway.
- [#38055](https://github.com/anthropics/claude-code/issues/38055) — "Minor version update permanently deletes chat history and scheduled tasks."
- [#12908](https://github.com/anthropics/claude-code/issues/12908) — "Conversation History disappeared after update."
- [#38691](https://github.com/anthropics/claude-code/issues/38691) — "All sessions lost after Claude Desktop update on Windows (data intact on disk)."
- [#48334](https://github.com/anthropics/claude-code/issues/48334) — "Desktop app update deletes session history."

These are user reports, not Anthropic-confirmed root causes — but the pattern is consistent enough that any prevention story needs to assume updates can ignore the setting. Anecdotally on this machine: closing and reopening VS Code (which can pull in an update silently) has been the precipitating event multiple times.

So there are two layers.

**Prevention** is one line in `~/.claude/settings.json`:
```json
"cleanupPeriodDays": 36500
```
~100 years; no documented upper bound. Set this on every machine. It defangs the documented cleanup, but per the issues above, it doesn't protect you from app updates that ignore the setting, future changes in cleanup behavior, or anything else Anthropic decides to do to your local files down the road.

**Restoration** is [`restore_claude_history.py`](restore_claude_history.py). It assumes the worst has already happened and pulls your chats back out of Time Machine. It's what catches you when prevention fails — which, given the track record, is a "when" not an "if."

## Why updates seem to trigger this

The precipitating event, in my experience, has not been "I left chats sitting around for 30+ days and `cleanupPeriodDays` finally got them." It's been: **I closed VS Code, reopened it, and the chats were gone.**

This has happened multiple times on this machine, even with `cleanupPeriodDays` set to a high value. The common factor across every occurrence is that *something updated* between close and reopen — but I can't always tell *what*. Candidates I've ruled in and not yet ruled out:

- **The Claude Code CLI updating** (it self-updates frequently and quietly).
- **The Claude Code VS Code extension updating** (extensions auto-update by default).
- **The extension or its host process restarting** — even without a version change, a restart appears to be enough to trip something.
- **VS Code itself updating.**
- **Claude Desktop updating** (when it's running in parallel; it shares some local state).

I haven't isolated which of these is sufficient on its own — I'd need to disable auto-updates on each surface and reproduce, which is more work than I've done. But several of the GitHub issues describe the same shape: close → update → reopen → chats are gone. See [#41458](https://github.com/anthropics/claude-code/issues/41458), [#38055](https://github.com/anthropics/claude-code/issues/38055), [#12908](https://github.com/anthropics/claude-code/issues/12908), [#38691](https://github.com/anthropics/claude-code/issues/38691), [#48334](https://github.com/anthropics/claude-code/issues/48334).

The practical takeaway: **don't treat `cleanupPeriodDays` as the only line of defense.** If you've been using Claude Code for more than a few weeks and care about the transcripts, assume an update can wipe them at any time, and have Time Machine running. This tool is the catch when that happens.

If you've reproduced this with a known-isolated trigger (just the CLI updating, just the extension, etc.), I'd genuinely like to know — open an issue on the repo or comment on the relevant `anthropics/claude-code` thread.

## What we verified by hand, before scripting

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

## Gotchas we learned the hard way

These are the things the script is silently working around. Document them here so they don't get re-discovered.

- **Spotlight indexes APFS Time Machine volumes the moment they mount, and you cannot turn it off.** `mdutil -i off` reports success but the index restarts. `.metadata_never_index` marker files do nothing. Apple's Spotlight Privacy UI refuses to add TM volumes. Result: high CPU (CGPDFService, mds_stores, mdworker_shared) for as long as the drive is mounted. Mitigation: **be fast** — mount, restore, unmount, eject.

- **macOS sometimes pre-mounts snapshots at `/Volumes/.timemachine/<UUID>/<ts>.backup/`.** Trying `mount_apfs` on those fails with `Resource busy`. The script detects existing mounts and uses them directly rather than trying to remount.

- **The auto-mount path has a doubled-`.backup` layout** (`<mp>/<ts>.backup/<ts>.backup/Data/...`) different from what `mount_apfs` produces yourself (`<mp>/<ts>.backup/Data/...`). The script probes both.

- **`cp -p` fails with "Permission denied" when the destination already exists** with the read-only ACL inherited from a previous copy. Strip the ACL on the destination first (`chmod -N`).

- **`tmutil` has no mount/unmount verbs.** It lists snapshots, restores files (limited), but does not let you mount one on demand. `mount_apfs` is the lower-level escape hatch.

- **`diskutil info <dev>` includes snapshot names** in its output. If you grep that output for "Time Machine" to identify the TM volume, you'll match the internal disk too (which has local TM snapshots). Use `tmutil destinationinfo` instead — it's purpose-built.

- **`diskutil apfs listSnapshots` formats output as an ASCII tree** with leading pipe characters. A naive `^\s*Name:` regex only matches the *last* block (which uses spaces, not pipes, for its prefix). Match `Name:` anywhere on the line, not just after whitespace.

- **`os.getlogin()` can return `root`** in non-TTY contexts (sudo, nested shells, some CI). `getpass.getuser()` reads `LOGNAME`/`USER` env vars and is more reliable.

- **argparse rejects values that start with `-`** because it thinks they're flags. Encoded Claude project names all start with `-`. The script pre-rewrites `--project FOO` → `--project=FOO` in argv before argparse sees it.

- **macOS ships bash 3.2** (frozen for licensing reasons since 2006). No associative arrays. We tried writing this in bash first; the resulting code was readable only via `sort | awk` pipeline tricks and a `trap` cleanup that turned out to be buggy. Python made all of this go away.

## Related GitHub issues

Open threads in `anthropics/claude-code` where users are hitting the disappearing-chats problem. Captured 2026-05-24 — comment counts will drift.

**My own filed issue:** [#62272 — Chat JSONLs deleted from `~/.claude/projects/` despite `cleanupPeriodDays` set high — appears triggered by updates/restarts](https://github.com/anthropics/claude-code/issues/62272). Filed 2026-05-25.

### Start here (highest-signal, lowest spam risk)

In order — comment on one, wait a few days, then the next. Tailor each comment; don't paste verbatim.

- [x] **[#59248 — Silent retention cleanup deletes session transcripts with no warning, opt-in, or recovery](https://github.com/anthropics/claude-code/issues/59248)** — the bug as described in the title *is* what this tool addresses. A recovery link is squarely on-topic, not spam. Best first comment. *Posted 2026-05-24.*
- [ ] **[#41458 — `cleanupPeriodDays: 99999` ignored — 490 sessions silently deleted despite explicit setting](https://github.com/anthropics/claude-code/issues/41458)** — 10 comments, active, and the most-affected users (who set the flag and *still* lost data) are exactly the people who need recovery. Also the evidence that prevention alone isn't enough.

If those land well (replies, thumbs-up, repo traffic in GitHub Insights), expand to the next tier. If they get ignored or pushback, stop and rethink the message before posting more.

### Next tier — high-traffic general threads

Many affected users, but the threads are broader than just `cleanupPeriodDays`, so the comment needs more framing ("if your JSONLs were deleted from disk on macOS, this can get them back; doesn't help if X").

- [ ] [#26452 — Session Disappeared After Logout / Restart of Claude Code Desktop - HOW to restore the sessions ASAP???](https://github.com/anthropics/claude-code/issues/26452) — 45 comments, very active.
- [ ] [#9258 — History Sessions lost in Vscode plugin](https://github.com/anthropics/claude-code/issues/9258) — 44 comments.
- [ ] [#38055 — Cowork: Minor version update permanently deletes chat history and scheduled tasks](https://github.com/anthropics/claude-code/issues/38055) — 18 comments.
- [ ] [#12908 — Conversation History disappeared after update](https://github.com/anthropics/claude-code/issues/12908) — 13 comments.

### Also relevant — core cleanup bug, smaller threads

- [ ] [#46621 — Critical: Claude Code silently deletes conversation history without user consent](https://github.com/anthropics/claude-code/issues/46621)
- [ ] [#46175 — Feature Request: Notify users before auto-deleting conversation history](https://github.com/anthropics/claude-code/issues/46175)
- [ ] [#60368 — Background-fleet `deleteJob` silently unlinks main session JSONL despite `cleanupPeriodDays: 36500`](https://github.com/anthropics/claude-code/issues/60368) — another path that bypasses the setting.
- [ ] [#16970 — claude is losing chat history](https://github.com/anthropics/claude-code/issues/16970)
- [ ] [#54092 — Local CLI conversations silently disappear from disk — multiple chats lost, JSONL files gone](https://github.com/anthropics/claude-code/issues/54092)
- [ ] [#61952 — ~20 sessions lost, only 11 survived - 2 months of work I paid for - gone](https://github.com/anthropics/claude-code/issues/61952)
- [ ] [#61038 — Old chats wiped, no session summary](https://github.com/anthropics/claude-code/issues/61038)
- [ ] [#49903 — Claude Code transcripts loss](https://github.com/anthropics/claude-code/issues/49903)
- [ ] [#61608 — Sessions not saved to disk — "Session not found on disk" on reopen](https://github.com/anthropics/claude-code/issues/61608) — has the exact UI string from the Desktop failure mode in TODO step 2.

### For the Desktop follow-up (later)

Not for the current script — relevant when the Claude Desktop recovery work in TODO kicks off.

- [ ] [#48334 — Desktop app update deletes session history (`sessions-index.json` + `.jsonl` files)](https://github.com/anthropics/claude-code/issues/48334)
- [ ] [#38691 — All sessions lost after Claude Desktop update on Windows (data intact on disk)](https://github.com/anthropics/claude-code/issues/38691)
- [ ] [#51412 — Desktop App 2.1.111 upgrade: Code session index wiped (recoverable via workaround); Cowork history disappeared](https://github.com/anthropics/claude-code/issues/51412)
- [ ] [#59736 — Desktop 3p Code sessions disappear from UI after restart while JSONL transcripts remain on disk](https://github.com/anthropics/claude-code/issues/59736)
- [ ] [#55418 — Code Desktop sessions display in sidebar but content is permanently inaccessible — audit.jsonl never recoverable, `sessiondata.img` is encrypted "shdw" container](https://github.com/anthropics/claude-code/issues/55418) — sobering: some Desktop data may be unrecoverable even with snapshots.

## Origin

This script was extracted from a real recovery session: months of Claude Code chats on a long-running personal project, gone overnight after an update. Working through the recovery by hand surfaced every gotcha listed above. The code here is the distilled, automated version of that work.
