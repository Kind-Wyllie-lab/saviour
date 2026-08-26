#!/usr/bin/env python3
"""
interpolate_boxes.py -- fill the gaps between label_activity.py's hand-drawn
box "keyframes" by linear interpolation, turning a sparsely box-mode-labeled
video into a fully labeled one without hand-drawing every single frame.

label_activity.py's box mode already treats every committed frame as a
keyframe (drawn fresh, or a carried-over draft you dragged/resized/
confirmed) -- an animal moving smoothly rarely needs every frame labeled by
hand, just often enough that the path between two labeled frames is a
straight line. This script reads those keyframes back out, and for every
frame strictly between two consecutive keyframes, linearly interpolates
each box's position (by the frame's real timestamp fraction between the two
keyframes' timestamps, not raw frame index -- consistent with how this
whole toolkit treats the _timestamps.csv as ground truth) and writes out a
full image+YOLO-label pair for it.

Two situations are deliberately NOT bridged, and are reported rather than
guessed at:
  - the gap between two consecutive keyframes exceeds --max-gap-s (default
    2.0s) -- past that, this assumes it's more likely the animal left frame
    and came back (or you just labeled sparsely on purpose) than that it
    moved in a straight line the whole way, so guessing would risk
    injecting a wrong label rather than just leaving a frame unlabeled.
  - the two keyframes don't have the same number of boxes -- an animal
    entering/leaving between them means there's no single correct
    correspondence to interpolate, so the gap is skipped outright rather
    than guessing which box(es) to drop or invent.
When the counts do match, boxes from the earlier keyframe are paired with
the later keyframe's boxes by nearest center distance (not list order --
nothing guarantees you drew/dragged them in the same order both times).
This is a simple greedy match, not a real tracker: fine for the handful of
simultaneous animals a habitat/APA session ever has, not validated beyond
that.

Output is a SEPARATE dataset directory by default (<boxes-dir>_interpolated),
not written back into the hand-labeled --boxes-dir -- so the ground truth
you actually drew stays distinguishable from machine-interpolated labels,
in case a later pass wants to weight or spot-check them differently. Every
keyframe is also copied into the output (interpolation fills the frames
*between* keyframes; the keyframes themselves are already-correct labels),
so --out ends up as one complete, standalone, YOLO-ready dataset.

Usage:
    python3 src/modules/variants/habitat_camera/analysis/interpolate_boxes.py \
        VIDEO.ts [--timestamps CSV] [--boxes-dir DIR] [--out DIR]
        [--max-gap-s 2.0]
"""

import argparse
import sys
from datetime import datetime
from itertools import pairwise
from pathlib import Path

import cv2
from label_activity import BoxDataset, _load_frame_metadata

Box = tuple[int, int, int, int]


def _center(box: Box) -> tuple[float, float]:
    x1, y1, x2, y2 = box
    return (x1 + x2) / 2, (y1 + y2) / 2


def _match_boxes(a: list[Box], b: list[Box]) -> list[tuple[Box, Box]]:
    """Pair each box in `a` with the nearest (by center distance) unused box
    in `b`, greedily. Only meaningful when len(a) == len(b) -- callers only
    invoke this once that's already been checked."""
    remaining = list(range(len(b)))
    pairs = []
    for box_a in a:
        ca = _center(box_a)
        j = min(remaining, key=lambda j: (_center(b[j])[0] - ca[0]) ** 2
                + (_center(b[j])[1] - ca[1]) ** 2)
        remaining.remove(j)
        pairs.append((box_a, b[j]))
    return pairs


def _lerp_box(b0: Box, b1: Box, t: float) -> Box:
    return tuple(round(v0 + (v1 - v0) * t) for v0, v1 in zip(b0, b1, strict=True))


def fill_gaps(frame_boxes: dict[int, list[Box]], rows: list[dict], max_gap_s: float
              ) -> tuple[dict[int, list[Box]], list[tuple[int, int, str]]]:
    """Returns (filled, skipped) -- filled includes every original keyframe
    plus every successfully interpolated in-between frame; skipped is
    (keyframe_a, keyframe_b, reason) for consecutive keyframe pairs that
    weren't bridged."""
    keyframes = sorted(frame_boxes.keys())
    filled: dict[int, list[Box]] = {k: list(frame_boxes[k]) for k in keyframes}
    skipped: list[tuple[int, int, str]] = []
    for k0, k1 in pairwise(keyframes):
        t0 = datetime.fromisoformat(rows[k0]["timestamp_utc"])
        t1 = datetime.fromisoformat(rows[k1]["timestamp_utc"])
        gap_s = (t1 - t0).total_seconds()
        boxes0, boxes1 = frame_boxes[k0], frame_boxes[k1]
        if gap_s > max_gap_s:
            skipped.append((k0, k1, f"{gap_s:.1f}s gap exceeds --max-gap-s"))
            continue
        if len(boxes0) != len(boxes1):
            skipped.append(
                (k0, k1, f"box count changed ({len(boxes0)} -> {len(boxes1)})")
            )
            continue
        pairs = _match_boxes(boxes0, boxes1)
        for idx in range(k0 + 1, k1):
            idx_t = datetime.fromisoformat(rows[idx]["timestamp_utc"])
            t = (idx_t - t0).total_seconds() / gap_s
            filled[idx] = [_lerp_box(pb0, pb1, t) for pb0, pb1 in pairs]
    return filled, skipped


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("video", help="video file the boxes were labeled against")
    ap.add_argument("--timestamps",
                     help="paired _timestamps.csv (default: derived from video path)")
    ap.add_argument("--boxes-dir",
                     help="label_activity.py box-mode output to interpolate from "
                          "(default: <video_stem>_boxes)")
    ap.add_argument("--out",
                     help="output dir for the fully-filled dataset "
                          "(default: <boxes-dir>_interpolated)")
    ap.add_argument("--max-gap-s", type=float, default=2.0,
                     help="don't bridge two consecutive keyframes more than this "
                          "many seconds apart (default: 2.0)")
    return ap


def _write_filled_dataset(cap, probe, filled: dict[int, list[Box]],
                           out_dataset: BoxDataset, frame_size: tuple[int, int]) -> int:
    # Sequential cap.read() only, no seeking -- label_activity.py's own
    # get_frame() documents CAP_PROP_POS_MSEC/POS_FRAMES seeking as
    # unreliable for this .ts container (can land ~17% short of the
    # requested frame). Sequential reads from frame 0 are exact, so that's
    # the only way to guarantee every written image is genuinely the frame
    # its interpolated/keyframe box was computed for.
    frame_w, frame_h = frame_size
    last_wanted = max(filled)
    written = 0
    frame_idx = 0
    frame = probe
    while frame_idx <= last_wanted:
        boxes = filled.get(frame_idx)
        if boxes:
            out_dataset.write(frame_idx, frame, boxes, frame_w, frame_h)
            written += 1
        if frame_idx == last_wanted:
            break
        ok, frame = cap.read()
        if not ok:
            print(f"Video ended early at frame {frame_idx} "
                  f"(wanted up to {last_wanted}) -- stopping")
            break
        frame_idx += 1
    return written


def main() -> None:
    args = _build_argparser().parse_args()

    video_path = Path(args.video)
    ts_path = Path(args.timestamps) if args.timestamps else (
        video_path.parent / f"{video_path.stem}_timestamps.csv"
    )
    if not ts_path.exists():
        sys.exit(f"No timestamps CSV found at {ts_path} -- pass --timestamps")

    boxes_dir = Path(args.boxes_dir) if args.boxes_dir else (
        video_path.parent / f"{video_path.stem}_boxes"
    )
    src_dataset = BoxDataset(boxes_dir, video_path.stem)

    rows = _load_frame_metadata(ts_path)
    n_frames = len(rows)
    if n_frames == 0:
        sys.exit(f"No rows in {ts_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        sys.exit(f"Could not open video: {video_path}")
    ok, probe = cap.read()
    if not ok:
        sys.exit(f"Could not read a frame from {video_path}")
    frame_h, frame_w = probe.shape[:2]

    frame_boxes = src_dataset.load_existing(frame_w, frame_h)
    if not frame_boxes:
        sys.exit(f"No keyframes found in {boxes_dir} -- label some with "
                  f"label_activity.py's box mode first")
    print(f"Loaded {len(frame_boxes)} keyframe(s) from {boxes_dir}")

    filled, skipped = fill_gaps(frame_boxes, rows, args.max_gap_s)
    for k0, k1, reason in skipped:
        print(f"  Not bridging frame {k0} -> {k1}: {reason}")
    n_interpolated = len(filled) - len(frame_boxes)
    print(f"Interpolated {n_interpolated} additional frame(s) "
          f"({len(skipped)} gap(s) not bridged, see above)")

    out_dir = Path(args.out) if args.out else Path(f"{boxes_dir}_interpolated")
    out_dataset = BoxDataset(out_dir, video_path.stem)
    (out_dir / "classes.txt").write_text((boxes_dir / "classes.txt").read_text()
                                          if (boxes_dir / "classes.txt").exists()
                                          else "rat\n")

    written = _write_filled_dataset(cap, probe, filled, out_dataset, (frame_w, frame_h))
    cap.release()

    print(f"Wrote {written} frame(s) to {out_dir}")


if __name__ == "__main__":
    main()
