#!/usr/bin/env python3
"""
evaluate_dataset.py -- aggregate recall/false-positive evaluation of the real
HabitatMotionDetector + hysteresis trigger (same logic as
replay_habitat_motion.py) across every labeled video found under a root
directory, instead of checking one video at a time by hand. Finds every
subfolder containing a <name>.ts + <name>_timestamps.csv + <name>_labels.csv
triple (from label_activity.py).

Decodes and scores each video exactly ONCE regardless of how many
--threshold/--activity-min values are given -- decoding is the expensive
part (per video, on the order of a minute or two), while re-running the
hysteresis trigger over already-computed scores for a different
threshold/duration is nearly free. So a sweep across many combinations
costs about the same as evaluating one.

Reports, per (threshold, activity_min) combination, aggregate totals across
the whole dataset: recall (labeled segments caught) and a false-positive
rate expressed per hour of footage (there's no natural "negative class"
size for a time-series detection task, so a flat fraction isn't meaningful
the way it would be for classification). If exactly one combination is
given, also prints the full per-video missed/unmatched breakdown.

Always crops the top of the frame (see --crop-top-frac) -- the recorded .ts
has the timestamp overlay burned in, which live scoring on hardware never
sees (camera_base.py scores the frame before drawing the overlay).

Usage:
    python3 .../analysis/evaluate_dataset.py ROOT_DIR
        [--config habitat_camera_config.json] [--no-ae-gate]
        [--crop-top-frac 0.08] [--threshold T [T ...]] [--activity-min S [S ...]]
        [--inactivity-min S]
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

import cv2

from experimental_scorers import ALGORITHM_NAMES, build_scorer  # noqa: E402
from replay_habitat_motion import (  # noqa: E402
    ReplayTrigger,
    _overlaps,
    load_habitat_motion_config,
)


def _find_labeled_videos(root: Path):
    """Yield (video_path, timestamps_path, labels_path) triples."""
    for labels_path in sorted(root.glob("*/*_labels.csv")):
        stem = labels_path.name[: -len("_labels.csv")]
        video_path = labels_path.parent / f"{stem}.ts"
        ts_path = labels_path.parent / f"{stem}_timestamps.csv"
        if video_path.exists() and ts_path.exists():
            yield video_path, ts_path, labels_path


def _score_one(video_path: Path, ts_path: Path, algorithm: str, process_width: int,
                pixel_threshold: int, crop_top_frac: float, algorithm2: str | None = None):
    """Decode + score a video exactly once. Returns per-frame arrays --
    everything the trigger simulation needs -- independent of any
    threshold/duration setting. `algorithm` is any name from
    experimental_scorers.ALGORITHM_NAMES, not just the production frame_diff/
    mog2 pair -- pixel_threshold/process_width only matter for the scorers
    that accept them (see build_scorer), ignored otherwise.

    algorithm2, if given, scores a SECOND algorithm on the same decoded frame
    in the same pass (for OR-ensemble evaluation, see _simulate_or) -- so
    comparing two algorithms never costs a second video decode."""
    with open(ts_path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None

    detector = build_scorer(algorithm, process_width=process_width, pixel_threshold=pixel_threshold)
    detector2 = (
        build_scorer(algorithm2, process_width=process_width, pixel_threshold=pixel_threshold)
        if algorithm2 else None
    )
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"  Could not open {video_path}", file=sys.stderr)
        return None

    scores, scores2, exposures, gains, ts_ns_list, ts_dt_list = [], [], [], [], [], []
    frame_idx = 0
    while frame_idx < len(rows):
        ok, frame = cap.read()
        if not ok:
            break
        if crop_top_frac > 0:
            crop_rows = int(frame.shape[0] * crop_top_frac)
            frame = frame[crop_rows:, :]
        row = rows[frame_idx]
        scores.append(detector.score(frame))
        if detector2 is not None:
            scores2.append(detector2.score(frame))
        exposures.append(row.get("exposure_time_us", ""))
        gains.append(row.get("analogue_gain", ""))
        ts_ns_list.append(int(row["timestamp_ns"]))
        ts_dt_list.append(datetime.fromisoformat(row["timestamp_utc"]))
        frame_idx += 1
    cap.release()

    if not ts_dt_list:
        return None
    return {
        "scores": scores, "scores2": scores2 or None,
        "exposures": exposures, "gains": gains,
        "ts_ns": ts_ns_list, "ts_dt": ts_dt_list,
        "span_s": (ts_dt_list[-1] - ts_dt_list[0]).total_seconds(),
    }


def _simulate(scored: dict, threshold: float, activity_min_s: float,
              inactivity_min_s: float, ae_settle_s: float, ae_gate_enabled: bool,
              threshold2: float | None = None):
    """Cheap, in-memory re-run of the hysteresis trigger over already-computed
    scores -- no video decode, so trying another threshold/duration is fast.

    If scored["scores2"] and threshold2 are both given, this becomes an
    OR-ensemble: a frame counts as "above" if EITHER algorithm's own score
    crosses ITS OWN threshold. Reuses ReplayTrigger's real hysteresis/AE-gate
    logic unchanged by feeding it a synthetic 0.0/1.0 score against a fixed
    0.5 threshold instead of the raw per-algorithm score -- so the sustained-
    duration and AE-gate behavior is identical to the single-algorithm path,
    just fed a boolean OR instead of one algorithm's continuous score."""
    scores2 = scored.get("scores2")
    ensemble = scores2 is not None and threshold2 is not None

    trigger = ReplayTrigger(
        activity_threshold=(0.5 if ensemble else threshold),
        activity_min_duration_s=activity_min_s,
        inactivity_min_duration_s=inactivity_min_s, ae_settle_s=ae_settle_s,
        ae_gate_enabled=ae_gate_enabled,
    )
    segments = []
    current_open_ts = None
    last_ts_dt = None
    scores2_iter = scores2 if ensemble else [None] * len(scored["scores"])
    for score, score2, exposure, gain, ts_ns, ts_dt in zip(
        scored["scores"], scores2_iter, scored["exposures"], scored["gains"],
        scored["ts_ns"], scored["ts_dt"],
    ):
        fed_score = (
            1.0 if (score >= threshold or score2 >= threshold2) else 0.0
        ) if ensemble else score
        result = trigger.process_frame(fed_score, exposure, gain, ts_ns)
        last_ts_dt = ts_dt
        if result["opened"]:
            current_open_ts = ts_dt
        if result["closed"] and current_open_ts is not None:
            segments.append((current_open_ts, ts_dt))
            current_open_ts = None
    if current_open_ts is not None and last_ts_dt is not None:
        segments.append((current_open_ts, last_ts_dt))
    return segments


def _compare(segments, labels):
    """labels: list of (start, end, severity). Returns (caught, missed) as
    lists of (start, end, severity) -- severity kept through so callers can
    break recall down by major/minor -- plus unmatched detected clips."""
    caught, missed = [], []
    for ls, le, severity in labels:
        if any(_overlaps(ls, le, ds, de) for ds, de in segments):
            caught.append((ls, le, severity))
        else:
            missed.append((ls, le, severity))
    unmatched = [
        (ds, de) for ds, de in segments
        if not any(_overlaps(ls, le, ds, de) for ls, le, _ in labels)
    ]
    return caught, missed, unmatched


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("root")
    ap.add_argument("--config", help="habitat_camera_config.json to read habitat_motion.* from")
    ap.add_argument("--no-ae-gate", action="store_true")
    ap.add_argument("--crop-top-frac", type=float, default=0.08)
    ap.add_argument("--threshold", type=float, nargs="+", default=None,
                     help="one or more activity_threshold values to sweep "
                          "(default: the value from --config / built-in default)")
    ap.add_argument("--activity-min", type=float, nargs="+", default=None,
                     help="one or more activity_min_duration_s values to sweep")
    ap.add_argument("--inactivity-min", type=float, default=None)
    ap.add_argument("--algorithm", choices=sorted(ALGORITHM_NAMES), default=None,
                     help="default: the value from --config / built-in default (frame_diff)")
    ap.add_argument("--pixel-threshold", type=int, default=15,
                     help="frame_diff/three_frame_diff/blob_size only (default: 15)")
    ap.add_argument("--algorithm2", choices=sorted(ALGORITHM_NAMES), default=None,
                     help="second algorithm for an OR-ensemble: a frame counts as active if "
                          "EITHER algorithm's own score crosses ITS OWN threshold (this one's "
                          "fixed via --threshold2, not swept -- --threshold still sweeps the "
                          "first algorithm). Both are scored in the same decode pass.")
    ap.add_argument("--threshold2", type=float, default=None,
                     help="fixed activity_threshold for --algorithm2 (required if it's given)")
    args = ap.parse_args()

    if args.algorithm2 and args.threshold2 is None:
        sys.exit("--algorithm2 requires --threshold2")

    cfg = load_habitat_motion_config(args.config)
    algorithm = args.algorithm or cfg["algorithm"]
    process_width = int(cfg["process_width"])
    thresholds = args.threshold or [float(cfg["activity_threshold"])]
    activity_mins = args.activity_min or [float(cfg["activity_min_duration_s"])]
    inactivity_min = args.inactivity_min if args.inactivity_min is not None else float(
        cfg["inactivity_min_duration_s"])
    ae_gate_enabled = not args.no_ae_gate
    root = Path(args.root)

    triples = list(_find_labeled_videos(root))
    if not triples:
        sys.exit(f"No labeled videos (*_labels.csv with matching .ts) found under {root}")

    print(f"Found {len(triples)} labeled video(s) under {root}")
    ensemble_note = f" + {args.algorithm2}@{args.threshold2} (OR)" if args.algorithm2 else ""
    print(f"algorithm={algorithm}{ensemble_note} ae_gate={'ON' if ae_gate_enabled else 'OFF'} "
          f"crop_top_frac={args.crop_top_frac}")
    print(f"sweeping threshold={thresholds} activity_min={activity_mins} "
          f"inactivity_min={inactivity_min}s\n")

    scored_videos = []  # list of (name, scored_dict, labels, span_s)
    for video_path, ts_path, labels_path in triples:
        print(f"Scoring {video_path.parent.name}/{video_path.name} ...", file=sys.stderr)
        with open(labels_path, newline="") as f:
            labels = [
                (datetime.fromisoformat(r["start_utc"]), datetime.fromisoformat(r["end_utc"]),
                 r.get("severity", ""))
                for r in csv.DictReader(f)
            ]
        if not labels:
            continue
        scored = _score_one(video_path, ts_path, algorithm, process_width,
                             args.pixel_threshold, args.crop_top_frac, args.algorithm2)
        if scored:
            scored_videos.append((video_path.name, scored, labels))

    total_span_h = sum(s["span_s"] for _, s, _ in scored_videos) / 3600
    total_labels = sum(len(labels) for _, _, labels in scored_videos)
    total_major = sum(1 for _, _, labels in scored_videos for *_, sv in labels if sv == "major")
    total_minor = sum(1 for _, _, labels in scored_videos for *_, sv in labels if sv == "minor")

    combos = [(t, a) for t in thresholds for a in activity_mins]
    total_span_s = total_span_h * 3600
    sweep_results = []
    for thr, act_min in combos:
        total_caught = total_unmatched = caught_major = caught_minor = 0
        total_kept_s = 0.0
        per_video = []
        for name, scored, labels in scored_videos:
            segments = _simulate(scored, thr, act_min, inactivity_min,
                                  float(cfg["ae_settle_s"]), ae_gate_enabled,
                                  threshold2=args.threshold2)
            caught, missed, unmatched = _compare(segments, labels)
            total_caught += len(caught)
            total_unmatched += len(unmatched)
            caught_major += sum(1 for *_, sv in caught if sv == "major")
            caught_minor += sum(1 for *_, sv in caught if sv == "minor")
            total_kept_s += sum((de - ds).total_seconds() for ds, de in segments)
            per_video.append((name, caught, missed, unmatched))
        sweep_results.append(
            (thr, act_min, total_caught, total_unmatched, caught_major, caught_minor,
             total_kept_s, per_video)
        )

    sweep_results.sort(key=lambda r: (-r[2], r[3]))

    header = (f"{'threshold':>10} {'act_min':>8} | {'caught':>8} {'major':>9} {'minor':>9} "
              f"{'unmatched':>10} {'fp/hour':>8} {'kept':>10}")
    print(header)
    print("-" * len(header))
    for thr, act_min, caught, unmatched, c_major, c_minor, kept_s, _ in sweep_results:
        fp_per_hour = unmatched / total_span_h if total_span_h else 0.0
        kept_pct = 100 * kept_s / total_span_s if total_span_s else 0.0
        print(f"{thr:>10} {act_min:>8} | {caught:>3}/{total_labels:<4} "
              f"{c_major:>3}/{total_major:<5} {c_minor:>3}/{total_minor:<5} "
              f"{unmatched:>10} {fp_per_hour:>8.2f} {kept_pct:>9.1f}%")

    if len(combos) == 1:
        thr, act_min, caught, unmatched, c_major, c_minor, kept_s, per_video = sweep_results[0]
        print(f"\n=== detail for threshold={thr} activity_min={act_min} "
              f"({total_span_h:.2f}h across {len(scored_videos)} video(s)) ===")
        print(f"  kept: {kept_s/60:.1f} min of {total_span_s/60:.1f} min "
              f"({100*kept_s/total_span_s:.1f}% of footage)")
        for name, caught_list, missed, unmatched_list in per_video:
            if missed:
                print(f"\n  missed in {name}:")
                for ls, le, severity in missed:
                    tag = f" [{severity}]" if severity else ""
                    print(f"    {ls.strftime('%H:%M:%S')} -> {le.strftime('%H:%M:%S')}{tag}")
            if unmatched_list:
                print(f"\n  false positive(s) in {name}:")
                for ds, de in unmatched_list:
                    print(f"    {ds.strftime('%H:%M:%S')} -> {de.strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
