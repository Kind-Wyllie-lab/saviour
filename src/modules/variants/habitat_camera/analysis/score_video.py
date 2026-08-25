#!/usr/bin/env python3
"""
score_video.py -- decode a video once and compute a chosen motion-scoring
algorithm's raw per-frame score, written to a CSV for sweep_motion_params.py.

Exists so trying an experimental algorithm (experimental_scorers.py) against
real footage doesn't mean duplicating replay_habitat_motion.py's video-decode
loop -- that script stays a pure mirror of production and only knows about
HabitatMotionDetector (frame_diff/mog2); this one knows about every
candidate scorer under evaluation, production or experimental, and just
writes timestamp_utc,score rows -- no hysteresis/threshold logic here, that's
sweep_motion_params.py's job, run separately (and fast) against the output.

Usage:
    python3 .../analysis/score_video.py VIDEO.ts --algorithm ALGO
        [--timestamps CSV] [--out SCORES.csv]
        [--pixel-threshold N] [--process-width N] [--max-frames N]

    Algorithms: see experimental_scorers.ALGORITHM_NAMES (frame_diff,
    three_frame_diff, knn, optical_flow, edge_diff, blob_size, mog2 -- this
    last one is the real production detector).
"""

import argparse
import csv
import sys
from pathlib import Path

import cv2

from experimental_scorers import ALGORITHM_NAMES, build_scorer  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("video")
    ap.add_argument("--algorithm", required=True, choices=sorted(ALGORITHM_NAMES))
    ap.add_argument("--timestamps", help="paired _timestamps.csv (default: derived from video path)")
    ap.add_argument("--out", help="output scores CSV (default: <video_stem>_<algorithm>_scores.csv)")
    ap.add_argument("--pixel-threshold", type=int, default=15,
                     help="frame_diff/three_frame_diff only (default: 15, production's own value)")
    ap.add_argument("--process-width", type=int, default=256)
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--crop-top-frac", type=float, default=0.0,
                     help="crop this fraction off the top of each frame before scoring -- "
                          "the recorded .ts has the timestamp overlay burned in, which live "
                          "scoring on hardware never sees (camera_base.py scores the frame "
                          "before drawing the overlay); this makes offline scoring match that")
    args = ap.parse_args()

    video_path = Path(args.video)
    ts_path = Path(args.timestamps) if args.timestamps else (
        video_path.parent / f"{video_path.stem}_timestamps.csv"
    )
    if not ts_path.exists():
        sys.exit(f"No timestamps CSV found at {ts_path} -- pass --timestamps")

    out_path = Path(args.out) if args.out else (
        video_path.parent / f"{video_path.stem}_{args.algorithm}_scores.csv"
    )

    with open(ts_path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"No rows in {ts_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        sys.exit(f"Could not open video: {video_path}")

    scorer = build_scorer(args.algorithm, args.process_width, args.pixel_threshold)

    print(f"Scoring {video_path.name} with algorithm={args.algorithm} "
          f"process_width={args.process_width}"
          + (f" pixel_threshold={args.pixel_threshold}"
             if args.algorithm in ("frame_diff", "three_frame_diff") else ""),
          file=sys.stderr)

    n = 0
    with open(out_path, "w", newline="") as out_f:
        writer = csv.writer(out_f)
        writer.writerow(["timestamp_utc", "score"])
        while True:
            if args.max_frames is not None and n >= args.max_frames:
                break
            if n >= len(rows):
                break
            ok, frame = cap.read()
            if not ok:
                break
            if args.crop_top_frac > 0:
                crop_rows = int(frame.shape[0] * args.crop_top_frac)
                frame = frame[crop_rows:, :]
            score = scorer.score(frame)
            writer.writerow([rows[n]["timestamp_utc"], f"{score:.6f}"])
            n += 1
            if n % 5000 == 0:
                print(f"  ...{n} frames", file=sys.stderr)

    cap.release()
    print(f"Wrote {n} rows to {out_path}")


if __name__ == "__main__":
    main()
