#!/usr/bin/env python3
"""
sweep_motion_params.py -- fast parameter sweep over an already-computed
per-frame motion score CSV (from replay_habitat_motion.py's --diagnostic-csv),
checking many (smoothing window, activity_threshold, activity_min_duration_s)
combinations against a ground-truth labels CSV (from label_activity.py),
without re-decoding video for every combination. Decoding + scoring is the
expensive part (frame differencing / MOG2 + video I/O); once scores are
cached to a CSV, simulating the hysteresis trigger over them is nearly free,
so this can try dozens of combinations in under a second.

This only varies the raw per-frame score's SMOOTHING and the surrounding
hysteresis constants -- it does NOT vary frame_diff vs MOG2 (that changes the
raw scores themselves, so comparing base algorithms means generating a
separate --diagnostic-csv per algorithm first, e.g.:
    replay_habitat_motion.py video.ts --diagnostic-csv fd_scores.csv
    replay_habitat_motion.py video.ts --config mog2.json --diagnostic-csv mog2_scores.csv
then running this sweep against each and comparing the two reports.

Also deliberately does not include the AE-stability gate -- that's about
rejecting exposure/gain jumps, an orthogonal concern to the
score/smoothing/duration tuning question this tool is for.

Usage:
    python3 .../analysis/sweep_motion_params.py DIAGNOSTIC.csv --labels LABELS.csv
        [--smooth 1 5 10 15] [--threshold 0.005 0.01 0.015 0.02]
        [--activity-min 0.25 0.5 1.0] [--inactivity-min 30]
"""

import argparse
import csv
from collections import deque
from datetime import datetime


def _load_scores(path: str) -> tuple[list[datetime], list[float]]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    timestamps = [datetime.fromisoformat(r["timestamp_utc"]) for r in rows]
    scores = [float(r["score"]) for r in rows]
    return timestamps, scores


def _load_labels(path: str) -> list[tuple[datetime, datetime]]:
    with open(path, newline="") as f:
        return [
            (datetime.fromisoformat(r["start_utc"]), datetime.fromisoformat(r["end_utc"]))
            for r in csv.DictReader(f)
        ]


def _smooth(scores: list[float], window: int) -> list[float]:
    """Causal moving average -- only past+current frames, matching what a
    live system could actually compute (no lookahead into future frames)."""
    if window <= 1:
        return scores
    out = []
    buf: deque = deque(maxlen=window)
    for s in scores:
        buf.append(s)
        out.append(sum(buf) / len(buf))
    return out


def _simulate(timestamps, scores, threshold, activity_min_s, inactivity_min_s):
    """Minimal idle<->active hysteresis, mirroring the shape of
    HabitatCameraModule._process_main_frame/ReplayTrigger but without the
    AE-gate -- see module docstring."""
    state = "idle"
    since = None
    last_above = None
    clip_open = False
    open_ts = None
    segments = []
    for ts, score in zip(timestamps, scores):
        above = score >= threshold
        if last_above is None or above != last_above:
            since = ts
            last_above = above
        elapsed = (ts - since).total_seconds() if since else 0.0
        if state != "active":
            if above and elapsed >= activity_min_s:
                state = "active"
                if not clip_open:
                    clip_open, open_ts = True, ts
        elif not above and elapsed >= inactivity_min_s:
            state = "idle"
            if clip_open:
                segments.append((open_ts, ts))
                clip_open = False
    if clip_open:
        segments.append((open_ts, timestamps[-1]))
    return segments


def _overlaps(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("diagnostic_csv")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--smooth", type=int, nargs="+", default=[1])
    ap.add_argument("--threshold", type=float, nargs="+", required=True)
    ap.add_argument("--activity-min", type=float, nargs="+", default=[0.5, 1.0])
    ap.add_argument("--inactivity-min", type=float, default=30.0)
    args = ap.parse_args()

    timestamps, raw_scores = _load_scores(args.diagnostic_csv)
    labels = _load_labels(args.labels)

    print(f"{len(timestamps)} frames, {len(labels)} labeled segment(s)\n")
    header = f"{'smooth':>6} {'thresh':>7} {'act_min':>8} | {'caught':>7} {'detected':>9} {'unmatched':>10}"
    print(header)
    print("-" * len(header))

    results = []
    smoothed_cache: dict[int, list[float]] = {}
    for window in args.smooth:
        smoothed_cache[window] = _smooth(raw_scores, window)

    for window in args.smooth:
        smoothed = smoothed_cache[window]
        for thr in args.threshold:
            for act_min in args.activity_min:
                segs = _simulate(timestamps, smoothed, thr, act_min, args.inactivity_min)
                caught = sum(
                    1 for ls, le in labels
                    if any(_overlaps(ls, le, ds, de) for ds, de in segs)
                )
                unmatched = sum(
                    1 for ds, de in segs
                    if not any(_overlaps(ls, le, ds, de) for ls, le in labels)
                )
                results.append((window, thr, act_min, caught, len(segs), unmatched))

    results.sort(key=lambda r: (-r[3], r[5]))  # most caught first, fewest unmatched clips
    for window, thr, act_min, caught, n_seg, unmatched in results:
        print(f"{window:>6} {thr:>7.4f} {act_min:>8.2f} | "
              f"{caught:>3}/{len(labels):<3} {n_seg:>9} {unmatched:>10}")


if __name__ == "__main__":
    main()
