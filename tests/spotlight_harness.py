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
import datetime as _dt
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"


class _Tee:
    """Write to two streams (stdout/stderr + log file)."""
    def __init__(self, *streams):
        self._streams = streams
    def write(self, s):
        for st in self._streams:
            st.write(s)
            st.flush()
    def flush(self):
        for st in self._streams:
            st.flush()

BUCKETS = ["mds", "mds_stores", "mdworker_shared", "CGPDFService", "mdsync", "corespotlightd"]
# Sample offsets in seconds, relative to each phase marker.
OFFSETS = [0.0, 1.0, 5.0, 15.0, 30.0]


def count_procs() -> tuple[dict[str, int], dict[str, float]]:
    """Snapshot process counts AND summed %CPU per bucket via `ps`.

    `ps -axo %cpu=,comm=` keeps comm untruncated (putting comm first or pairing
    it with other columns triggers a 16-char truncation). %CPU is leading
    numeric; comm is everything after.
    """
    r = subprocess.run(["ps", "-axo", "%cpu=,comm="], capture_output=True, text=True)
    counts = {b: 0 for b in BUCKETS}
    pcts = {b: 0.0 for b in BUCKETS}
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pct_s, comm = line.split(None, 1)
            pct = float(pct_s)
        except ValueError:
            continue
        name = comm.rsplit("/", 1)[-1]
        if name in counts:
            counts[name] += 1
            pcts[name] += pct
    return counts, pcts


def _col_widths() -> list[int]:
    """Width per column: phase(14) elapsed(8), then one slot per bucket
    sized to fit the wider of '<bucket>_n' / '<bucket>_pct' headers."""
    widths = [14, 8]
    for b in BUCKETS:
        widths.append(max(len(f"{b}_n"), 5))
    for b in BUCKETS:
        widths.append(max(len(f"{b}_pct"), 6))
    return widths


def _fmt_row(cells: list[str]) -> str:
    widths = _col_widths()
    # First two cols left-aligned, the rest right-aligned (numeric).
    parts = [f"{cells[0]:<{widths[0]}}", f"{cells[1]:<{widths[1]}}"]
    for cell, w in zip(cells[2:], widths[2:]):
        parts.append(f"{cell:>{w}}")
    return "  ".join(parts)


def sample(label: str, t0: float) -> None:
    """Emit one fixed-width row: phase, elapsed, count cols, then %cpu cols."""
    elapsed = time.monotonic() - t0
    c, p = count_procs()
    cells = [label, f"{elapsed:.2f}"]
    cells += [str(c[b]) for b in BUCKETS]
    cells += [f"{p[b]:.1f}" for b in BUCKETS]
    print(_fmt_row(cells), flush=True)


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
    ap.add_argument("--label", default=None,
                    help="short tag for the log filename (default: derived from mode/strategy)")
    args = ap.parse_args()

    # Tee stdout + stderr to a per-run log file under tests/logs/
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    label = args.label or ("idle" if args.idle else f"{args.kind or 'mount'}-{args.strategy}")
    stamp = _dt.datetime.now().strftime("%Y-%m-%dT%H-%M")
    log_path = LOG_DIR / f"{stamp}_{label}.tsv"
    log_fh = open(log_path, "w")
    sys.stdout = _Tee(sys.__stdout__, log_fh)
    sys.stderr = _Tee(sys.__stderr__, log_fh)
    print(f"# run: label={label} args={vars(args)}", file=sys.stderr)

    # Header: count cols first, then %cpu cols
    cols = ["phase", "elapsed_s"]
    cols += [f"{b}_n" for b in BUCKETS]
    cols += [f"{b}_pct" for b in BUCKETS]
    print(_fmt_row(cols), flush=True)

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
            low = r.stderr.lower()
            # mount_apfs emits "Operation not permitted" (EPERM) without FDA.
            if "not permitted" in low or "permission" in low:
                print(
                    "# mount FAILED — likely no Full Disk Access (FDA). Grant FDA to\n"
                    "# whatever terminal you are running this from (Terminal.app, iTerm,\n"
                    "# VS Code, Cursor, etc.): System Settings → Privacy & Security →\n"
                    "# Full Disk Access → toggle on, then re-run.\n"
                    f"# (Underlying error: {r.stderr.strip()})",
                    file=sys.stderr,
                )
            else:
                print(f"# mount FAILED rc={r.returncode} stderr={r.stderr.strip()!r}", file=sys.stderr)
            return 2

        t_mount = time.monotonic()
        apply_strategy(args.strategy, mp)

        # Serial: sample immediately after mount, then walk, then keep sampling
        # so we see the mount-attributable churn and the walk-attributable churn
        # as separate phases. Mirrors how the production script behaves.
        sample_series("post-mount", t_mount, [0.0, 1.0])

        t_walk = time.monotonic()
        walked = walk_data(mp)
        walk_secs = time.monotonic() - t_walk
        print(f"# walked {walked} files in {walk_secs:.2f}s", file=sys.stderr, flush=True)

        t_post_walk = time.monotonic()
        sample_series("post-walk", t_post_walk, [0.0, 1.0, 5.0, 15.0, 30.0])

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
