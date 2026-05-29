#!/usr/bin/env python3
"""
spotlight_harness.py

Measure Spotlight-worker churn caused by mounting an APFS snapshot, with and
without various countermeasures. Standalone — does not import the main script.

Usage:
    sudo python3 tests/spotlight_harness.py --device disk5s2 --snapshot <name> \\
        --kind tm --strategy none

    --strategy:
        none                  baseline (mount, walk, unmount)
        mdutil-off            run `mdutil -i off <mp>` right after mount
        metadata-marker       touch .metadata_never_index at <mp> right after mount
        tmutil-exclude        `tmutil addexclusion -p <mp>` right after mount
        mount-flags           use mount_apfs -o noexec,noatime (also implies nobrowse)
        plist                 NOT IMPLEMENTED (would mutate Spotlight prefs system-wide)

Process buckets we count over time:
    mdworker_shared, mds, mds_stores, CGPDFService, corespotlightd

Output: a TSV row per sample to stdout, plus a single summary line at the end.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

BUCKETS = ["mds", "mds_stores", "mdworker_shared", "CGPDFService", "mdsync", "corespotlightd"]
# Sample offsets in seconds, relative to each phase marker.
OFFSETS = [0.0, 1.0, 5.0, 15.0, 30.0]


def count_procs() -> dict[str, int]:
    """Snapshot current process counts for our buckets via `ps`."""
    r = subprocess.run(["ps", "-axo", "comm="], capture_output=True, text=True)
    counts = {b: 0 for b in BUCKETS}
    for line in r.stdout.splitlines():
        name = line.strip().rsplit("/", 1)[-1]
        if name in counts:
            counts[name] += 1
    return counts


def sample(label: str, t0: float) -> None:
    """Emit one TSV row: phase, elapsed, then one column per bucket."""
    elapsed = time.monotonic() - t0
    c = count_procs()
    row = [label, f"{elapsed:.2f}"] + [str(c[b]) for b in BUCKETS]
    print("\t".join(row), flush=True)


def sample_series(label: str, t0: float, offsets: list[float]) -> None:
    """Sleep to each offset (absolute from t0) and sample once."""
    for off in offsets:
        target = t0 + off
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        sample(label, t0)


def walk_data(mp: Path, max_files: int = 200) -> int:
    """Light walk so we approximate what the real restore would do.

    Cap files we stat so we're measuring mount-time triggers, not walk-time IO.
    """
    seen = 0
    for root, dirs, files in os.walk(mp):
        # Skip macOS plumbing dirs that would dominate the walk
        dirs[:] = [d for d in dirs if d not in (".Spotlight-V100", ".fseventsd", ".Trashes")]
        for f in files:
            try:
                os.stat(os.path.join(root, f))
            except OSError:
                pass
            seen += 1
            if seen >= max_files:
                return seen
    return seen


def apply_strategy(strategy: str, mp: Path) -> None:
    """Run the chosen countermeasure immediately post-mount."""
    if strategy == "none":
        return
    if strategy == "mdutil-off":
        r = subprocess.run(["mdutil", "-i", "off", str(mp)], capture_output=True, text=True)
        print(f"# mdutil -i off -> rc={r.returncode} stdout={r.stdout.strip()!r} stderr={r.stderr.strip()!r}",
              file=sys.stderr, flush=True)
    elif strategy == "metadata-marker":
        marker = mp / ".metadata_never_index"
        try:
            marker.touch()
            print(f"# touched {marker}", file=sys.stderr, flush=True)
        except OSError as e:
            print(f"# touch {marker} FAILED: {e}", file=sys.stderr, flush=True)
    elif strategy == "tmutil-exclude":
        r = subprocess.run(["tmutil", "addexclusion", "-p", str(mp)], capture_output=True, text=True)
        print(f"# tmutil addexclusion -> rc={r.returncode} stdout={r.stdout.strip()!r} stderr={r.stderr.strip()!r}",
              file=sys.stderr, flush=True)
    elif strategy == "mount-flags":
        # Handled at mount time, not here.
        pass
    else:
        raise SystemExit(f"unknown strategy: {strategy}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", help="e.g. disk5s2 (omit for --idle)")
    ap.add_argument("--snapshot", help="snapshot name com.apple.TimeMachine.YYYY-... (omit for --idle)")
    ap.add_argument("--kind", choices=["tm", "local"], help="omit for --idle")
    ap.add_argument("--idle", action="store_true",
                    help="don't mount anything; just sample current process counts over 30s")
    ap.add_argument("--strategy", default="none",
                    choices=["none", "mdutil-off", "metadata-marker", "tmutil-exclude", "mount-flags"])
    ap.add_argument("--post-unmount-window", type=float, default=30.0,
                    help="how long to keep sampling after unmount (default 30s)")
    args = ap.parse_args()

    # Header
    print("phase\telapsed_s\t" + "\t".join(BUCKETS), flush=True)

    if args.idle:
        t0 = time.monotonic()
        sample_series("idle", t0, [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0])
        print("# done idle", file=sys.stderr, flush=True)
        return 0

    if not (args.device and args.snapshot and args.kind):
        ap.error("--device, --snapshot, and --kind are required unless --idle is set")

    with tempfile.TemporaryDirectory(prefix="sh-harness-") as tmp:
        mp = Path(tmp) / f"snap-{args.kind}"
        mp.mkdir()

        t0 = time.monotonic()
        sample("pre-mount", t0)

        mount_cmd = ["mount_apfs", "-s", args.snapshot, f"/dev/{args.device}", str(mp)]
        if args.strategy == "mount-flags":
            mount_cmd = ["mount_apfs", "-o", "noexec,noatime,nobrowse",
                         "-s", args.snapshot, f"/dev/{args.device}", str(mp)]

        print(f"# mount: {' '.join(mount_cmd)}", file=sys.stderr, flush=True)
        r = subprocess.run(mount_cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"# mount FAILED rc={r.returncode} stderr={r.stderr.strip()!r}", file=sys.stderr)
            return 2

        t_mount = time.monotonic()
        apply_strategy(args.strategy, mp)

        # Sample post-mount in a thread so we can also do a small walk.
        sampler = threading.Thread(target=sample_series, args=("post-mount", t_mount, OFFSETS))
        sampler.start()

        # Wait long enough that the 15s sample is captured, then walk.
        time.sleep(15.5)
        walked = walk_data(mp)
        print(f"# walked {walked} files", file=sys.stderr, flush=True)
        sampler.join()

        # Unmount
        print("# unmount", file=sys.stderr, flush=True)
        subprocess.run(["diskutil", "unmount", str(mp)], capture_output=True, text=True)

        t_unmount = time.monotonic()
        post_offsets = [o for o in OFFSETS if o <= args.post_unmount_window]
        sample_series("post-unmount", t_unmount, post_offsets)

    print(f"# done strategy={args.strategy}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
