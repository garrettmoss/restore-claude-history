# Notes

Working context for this repo. Read top to bottom before contributing or asking an AI assistant to help.

## What this repo is for

Claude Code (the CLI / VS Code extension) stores chat transcripts as JSONL files under `~/.claude/projects/<encoded-cwd>/*.jsonl`. There's a cleanup job that prunes these files after `cleanupPeriodDays` (default: **30 days**, undocumented in the user-facing UI, no warning before deletion). Many users have lost months of conversation history this way — see the open issues on `anthropics/claude-code`.

This repo restores those lost JSONLs from a macOS Time Machine backup, by reading APFS snapshots on the TM drive and copying the freshest version of each chat back into place.

Scope: **macOS + APFS Time Machine only.** Not cross-platform. Not for any other backup system.

## Prevention vs. cure

This repo is the **cure** — recovering chats after they're already gone. The **prevention** is a one-line change to `~/.claude/settings.json`:

```json
"cleanupPeriodDays": 36500
```

(~100 years; no documented upper bound, schema only requires positive integer.) Set this immediately if you haven't. But there's at least one GitHub report of the setting being ignored after an app update, which is why having Time Machine backups is still essential.

## Prerequisites

- macOS with an APFS Time Machine drive that's been used at least once.
- **Full Disk Access** granted to whatever app will run the script: Terminal.app, iTerm, or VS Code (whichever spawns the shell). Without FDA, `mount_apfs` and reads under `/Volumes/.timemachine/` fail with `Operation not permitted` or misleading `could not resolve path` errors. Grant via System Settings → Privacy & Security → Full Disk Access → +.
- The TM drive must be physically plugged in and mounted before running.

## The bug, in one paragraph

`~/.claude/settings.json` accepts a key `cleanupPeriodDays` (positive integer, default 30). Claude Code's cleanup function reads it on startup and deletes any JSONL older than that many days. The default is too aggressive, the setting isn't exposed in the UI, and there's at least one open GitHub issue claiming the setting can be ignored after some app updates. First defense is to set the value high (`36500` ~= 100 years) in `~/.claude/settings.json`. Second defense is to have Time Machine snapshots so you can recover if it happens anyway. This repo is the recovery half.

## What we already know works

Verified manually in a real recovery session. The script should automate this:

1. **List APFS snapshots on the TM volume.** `diskutil apfs listSnapshots /dev/diskNsM` gives names like `com.apple.TimeMachine.2026-04-24-205237.backup`. `tmutil listbackups -d "/Volumes/<TM Volume>"` gives the same info as paths but those paths aren't always auto-mounted.

2. **Mount each snapshot manually.** macOS only auto-mounts one TM snapshot at a time via the `/Volumes/.timemachine/<UUID>/...` paths. To access more, mount them yourself:
   ```
   mkdir -p /tmp/tm-<label>
   mount_apfs -s com.apple.TimeMachine.<timestamp>.backup /dev/diskNsM /tmp/tm-<label>
   ```
   Read-only, no sudo needed if you have FDA.

3. **Find the Claude project dir inside each snapshot.** Path inside a mounted snapshot is `<mount>/<timestamp>.backup/Data/Users/<user>/.claude/projects/<encoded-project>/`. The leading `Data/` is the APFS data-volume firmlink. Don't omit it.

4. **For each JSONL filename (UUID.jsonl), pick the largest version across all snapshots.** JSONLs are append-only logs. Bigger file = longer conversation = more complete. Same UUID across multiple snapshots = same session captured at different points in time; you want the latest/biggest.

5. **Copy with mtime preservation, and re-stamp after any re-copy.** Use `cp -p` so the destination keeps the snapshot's original modification time — otherwise VS Code's "Recent chats" picker sorts restored files as "just now." **Important gotcha:** if a file fails on the first copy (e.g. ACL conflict) and you re-copy after stripping the ACL, `cp -p`'s mtime preservation doesn't always survive the second pass. Explicitly re-stamp with `touch -r <source> <dest>` after any re-copy. In the live recovery, 3 files showed up as "edited just now" in VS Code's chat picker until this explicit re-stamp ran.

6. **Strip ACLs after copy.** TM snapshot files carry an inherited ACL: `group:everyone deny write,delete,append,writeattr,writeextattr,chown`. This sticks to the copy and blocks future overwrites. `chmod -N <file>` removes the ACL. `chmod u+w <file>` ensures user write bit.

7. **Restore subdirectories too.** `~/.claude/projects/<project>/<session-uuid>/subagents/` contains subagent transcripts. These can exist alongside the top-level `<uuid>.jsonl`. Use `cp -R` on the dirs.

8. **Unmount everything on exit.** `diskutil unmount /tmp/tm-<label>` for each. Then `rmdir /tmp/tm-<label>`. Use a `trap` so this runs even on Ctrl-C or script error.

## What we learned the hard way (don't repeat these)

- **Spotlight indexes APFS Time Machine volumes the moment they mount, and you cannot turn it off.** `mdutil -i off` reports success but the index restarts. Adding `.metadata_never_index` marker files does nothing on these volumes. Apple's own Spotlight Privacy UI refuses to add TM volumes ("you cannot add it to the privacy list"). Result: high CPU (CGPDFService chewing PDF thumbnails, mds_stores and mdworker_shared spinning) for as long as the drive is mounted. The only mitigation is **be fast** — mount, restore, unmount, eject. Don't leave the drive plugged in for hours.

- **Only one TM snapshot is auto-mounted at a time.** Even though `/Volumes/.timemachine/<UUID>/<timestamp>.backup/` directories all exist, listing the inner contents returns empty for any snapshot that isn't actively mounted. `mount_apfs` is the workaround.

- **`cp -p` fails with "Permission denied" when the destination already exists** with the read-only ACL inherited from a previous snapshot copy. Two fixes: strip the ACL on the destination before copying (`chmod -N`), or rip-and-replace the file (delete + copy fresh). The script should strip ACLs after every copy to keep restored files writable.

- **macOS ships bash 3.2.** No associative arrays, no `mapfile`. Either require bash 4+ (Homebrew install required, friction for users), or write portable bash 3 using sort/awk pipelines.

- **The macOS Spotlight Privacy GUI cannot exclude TM volumes**, but `mdutil` from a terminal with Full Disk Access can mostly do it (with the caveats above). FDA is required for both Terminal and any IDE that spawns subprocesses needing TM access.

- **`tmutil` has no mount/unmount verbs.** It lists snapshots, restores files (limited), but does not let you mount one on demand. `mount_apfs` is the lower-level escape hatch.

## What the script needs to do (rough algorithm)

```
1. Verify a Time Machine drive is mounted; bail with a clear error if not.
2. Find the TM volume's disk device (diskutil list).
3. List APFS snapshots on it.
4. For each snapshot:
   a. mount_apfs into a temp dir under /tmp.
   b. Walk every <encoded-project> directory inside.
   c. Index every .jsonl with (project, filename, size, snapshot-path).
5. After walking all snapshots, group by (project, filename) and pick the largest.
6. For each chosen file:
   a. Skip if a file with same name and size >= snapshot version already exists on disk.
   b. Otherwise, strip ACL on destination (if it exists), cp from snapshot, touch -r to preserve mtime, chmod -N to strip inherited ACL.
7. Restore matching subagent subdirectories the same way.
8. Trap-driven cleanup: unmount all temp mounts, rmdir temp dirs.
9. Print summary: N files restored, M skipped, total bytes.
```

## Open questions

- Should the script support recovering for *all* Claude projects in one run, or just one project per invocation? (Current thinking: all by default, `--project <name>` flag to scope it.)
- Should ACL stripping be on by default or behind a `--strip-acls` flag? (Current thinking: on by default — the ACL is useless on restored files and actively harmful for future overwrites.)
- Dry-run mode for safety. (Yes, definitely. `--dry-run` should be standard.)
- Should it also restore `~/.claude/projects/<project>/memory/` directories? (Probably yes, same logic — pick the most recent version.)

## Reference: the live recovery this was built from

Original recovery session happened from inside `~/projects/young-ladys-primer` on 2026-05-23. ~26 lost JSONLs recovered from 4 TM snapshots dating March 11 through April 24, 2026. Full transcript of the session is at `~/.claude/projects/-Users-garrettstone-projects-young-ladys-primer/a2144d30-9891-47ea-810d-9a124d6b7497.jsonl` (about 450KB). Don't load it as context for new sessions — this NOTES.md is the distilled version. Reference it only if a specific detail is missing here.

## Related GitHub issues to track / comment on

(To fill in once we link this repo from those threads. Search `anthropics/claude-code` for "history" "deleted" "cleanupPeriodDays" "lost chats".)

## Reference: working commands from the live recovery

These are the literal commands that worked in the original recovery, captured as evidence-of-incantation, not as a polished script. Paths and snapshot names are specific to that session — adapt for yours. The script we build will generalize all of this.

### Identify the TM disk device

```
diskutil list | grep -A 2 "Time Machine"
# → e.g. APFS Volume "Sapphire Time Machine" disk5s2
```

### List APFS snapshots on the TM volume

```
diskutil apfs listSnapshots /dev/disk5s2
# → prints names like com.apple.TimeMachine.2026-04-24-205237.backup
```

### Mount each snapshot to a temp dir

```
mkdir -p /tmp/tm-apr24
mount_apfs -s com.apple.TimeMachine.2026-04-24-205237.backup /dev/disk5s2 /tmp/tm-apr24
# Mounts read-only. Repeat per snapshot with unique tmp dirs.
```

### Find the project JSONLs inside a mounted snapshot

```
SNAP=/tmp/tm-apr24/2026-04-24-205237.backup
ls "$SNAP/Data/Users/<user>/.claude/projects/<encoded-project>/"*.jsonl
```

Note the `Data/` firmlink — required.

### Restore loop (largest version wins)

```
DEST=/Users/<user>/.claude/projects/<encoded-project>
SUB=Data/Users/<user>/.claude/projects/<encoded-project>

for snap in /tmp/tm-mar11/... /tmp/tm-mar20/... /tmp/tm-apr17/... /tmp/tm-apr24/...; do
  for src in "$snap/$SUB"/*.jsonl; do
    name=$(basename "$src")
    dst="$DEST/$name"
    src_size=$(stat -f%z "$src")
    if [ ! -e "$dst" ]; then
      cp -p "$src" "$dst"
    else
      dst_size=$(stat -f%z "$dst")
      if [ "$src_size" -gt "$dst_size" ]; then
        chmod -N "$dst" 2>/dev/null    # strip inherited ACL so overwrite works
        chmod u+w "$dst"
        cp "$src" "$dst"
        touch -r "$src" "$dst"          # re-stamp mtime since -p was dropped
        chmod -N "$dst" 2>/dev/null
      fi
    fi
  done
done
```

### Restore subagent subdirectories

```
for snap in /tmp/tm-*/; do
  snapdir=$(ls -d "$snap"*.backup/$SUB 2>/dev/null)
  [ -z "$snapdir" ] && continue
  for d in "$snapdir"/*/; do
    uuid=$(basename "$d")
    if [ ! -d "$DEST/$uuid" ]; then
      cp -R "$d" "$DEST/$uuid"
      find "$DEST/$uuid" -exec chmod -N {} \; 2>/dev/null
      find "$DEST/$uuid" -exec chmod u+w {} \; 2>/dev/null
    fi
  done
done
```

### Cleanup

```
for snap in /tmp/tm-*/; do
  diskutil unmount "$snap"
done
rmdir /tmp/tm-*
```

A real script must put the cleanup in a `trap '...' EXIT INT TERM` so it runs even on error or Ctrl-C.
