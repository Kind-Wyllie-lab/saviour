#!/usr/bin/env python3
"""
Extract candidate frames from recorded APA session video for labelling.

Pulls frames from a SAVIOUR recording (the .ts/.mp4 segment files a session
produces) at a fixed sampling interval, with an optional motion filter to
skip long stretches of an empty/static arena -- useful since a typical
session is mostly "nothing happening" and hand-labelling every frame would
waste effort on near-duplicates.

Usage:
    # every 30th frame from one segment file
    python extract_frames.py --video /path/to/segment.ts \
        --out training_data/images --every-n-frames 30

    # every session video under a date directory, motion-filtered
    python extract_frames.py --session-dir /path/to/session/20260817 \
        --out training_data/images --every-n-frames 10 --motion-only

Recommended: collect the source recordings with object_detection.enabled
and shock_zone.shock_zone_display both OFF. Frames extracted from a
recording made while an (imperfect, or even nonexistent) detector was
already drawing overlays will have the detection dot / shock-zone arc baked
into the pixels (see camera_base.py / apa_camera_module.py -- overlays are
drawn on the recorded main stream, not just the live preview), which
contaminates the label images with a signal you don't want the new model
learning to reproduce. The per-frame timestamp overlay (small, fixed
top-of-frame text) is generally fine to leave -- YOLO training augmentation
and dataset diversity make a small fixed watermark a non-issue in practice,
but crop it out first if you want to be careful.
"""

import argparse
import glob
from pathlib import Path

import cv2
import numpy as np


def find_videos(session_dir: Path) -> list[Path]:
    videos = []
    for ext in ("*.ts", "*.mp4"):
        pattern = str(session_dir / "**" / ext)
        videos.extend(Path(p) for p in glob.glob(pattern, recursive=True))
    return sorted(videos)


def has_motion(prev_gray, gray, threshold: float) -> bool:
    if prev_gray is None:
        return True
    diff = cv2.absdiff(prev_gray, gray)
    return float(np.mean(diff)) > threshold


def extract_from_video(video_path: Path, out_dir: Path, every_n_frames: int,
                        motion_only: bool, motion_threshold: float) -> int:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"[WARN] Could not open {video_path}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video_path.stem
    frame_idx = 0
    saved = 0
    prev_gray = None

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        if frame_idx % every_n_frames == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if not motion_only or has_motion(prev_gray, gray, motion_threshold):
                out_path = out_dir / f"{stem}_f{frame_idx:07d}.jpg"
                cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                saved += 1
            prev_gray = gray

        frame_idx += 1

    cap.release()
    print(f"      {video_path.name}: {saved} frames saved (of {frame_idx} total)")
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", type=Path, help="Single video file")
    src.add_argument("--session-dir", type=Path,
                      help="Session date directory -- recurses for .ts/.mp4 files")
    parser.add_argument("--out", type=Path, required=True,
                         help="Output directory for extracted .jpg frames")
    parser.add_argument("--every-n-frames", type=int, default=30,
                         help="Sample one frame every N (default: 30)")
    parser.add_argument("--motion-only", action="store_true",
                         help="Skip sampled frames with little change "
                              "from the previous one")
    parser.add_argument("--motion-threshold", type=float, default=3.0,
                         help="Mean abs pixel diff threshold for "
                              "--motion-only (default: 3.0)")
    args = parser.parse_args()

    videos = [args.video] if args.video else find_videos(args.session_dir)
    if not videos:
        print("[ERROR] No video files found")
        return

    print(f"Extracting frames from {len(videos)} video(s) -> {args.out}")
    total = 0
    for video in videos:
        total += extract_from_video(
            video, args.out, args.every_n_frames,
            args.motion_only, args.motion_threshold,
        )

    print(f"\nDone: {total} frames written to {args.out}")
    print("Next: label them (Roboflow / CVAT / LabelImg, YOLO-format .txt "
          "per image), then run train.py -- see this folder's README.md.")


if __name__ == "__main__":
    main()
