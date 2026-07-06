#!/usr/bin/env python3
"""
make_aligned_video.py — frame-aligned side-by-side video from SAVIOUR recordings.

Finds camera modules in a session directory, checks PTP synchronisation quality
from health metadata, computes per-camera frame offsets from PTP timestamps, and
calls ffmpeg to produce a trimmed, frame-aligned output video.

Only cameras that used picamera2 framesync (sync_mode == 'server' or 'client' in
config.json) are included. Cameras with sync_mode == 'none' are skipped with a
warning unless --include-unsynced is passed.

Usage:
    python3 tools/make_aligned_video.py SESSION_DIR
    python3 tools/make_aligned_video.py SESSION_DIR/20260703
    python3 tools/make_aligned_video.py SESSION_DIR --output out.mp4 --layout stack
    python3 tools/make_aligned_video.py SESSION_DIR --ptp-threshold 100

Requirements: ffmpeg on PATH; pandas + numpy (source env2/bin/activate).
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


PTP_THRESHOLD_NS = 50_000  # 50 µs — matches the recording gate


# ---------------------------------------------------------------------------
# Directory discovery
# ---------------------------------------------------------------------------

def find_date_dirs(session_dir: Path) -> list[Path]:
    """Return sorted date subdirectories (YYYYMMDD) under session_dir.
    If session_dir itself looks like a date dir, return it directly."""
    if re.fullmatch(r"\d{8}", session_dir.name):
        return [session_dir]
    date_dirs = sorted(d for d in session_dir.iterdir()
                       if d.is_dir() and re.fullmatch(r"\d{8}", d.name))
    if not date_dirs:
        sys.exit(f"No YYYYMMDD subdirectories found in {session_dir}")
    return date_dirs


def find_camera_dirs(date_dir: Path) -> list[Path]:
    """Return sorted camera_* subdirectories under a date dir."""
    return sorted(d for d in date_dir.iterdir()
                  if d.is_dir() and d.name.startswith("camera_"))


# ---------------------------------------------------------------------------
# Config / sync mode
# ---------------------------------------------------------------------------

def load_config(camera_dir: Path) -> dict:
    cfg = camera_dir / "config.json"
    if not cfg.exists():
        return {}
    return json.loads(cfg.read_text())


def sync_mode(config: dict) -> str:
    return config.get("camera", {}).get("sync_mode", "none")


# ---------------------------------------------------------------------------
# PTP check
# ---------------------------------------------------------------------------

def check_ptp(camera_dir: Path, threshold_ns: int) -> tuple[bool, str]:
    """Read health_metadata CSV and check ptp4l_offset_ns + phc2sys_offset.
    Returns (ok, summary_string).
    """
    health_csvs = sorted(camera_dir.glob("*_health_metadata_*.csv"))
    if not health_csvs:
        return False, "no health_metadata CSV found"

    rows = []
    for p in health_csvs:
        df = pd.read_csv(p)
        rows.append(df)
    health = pd.concat(rows, ignore_index=True)

    ptp4l_abs  = health["ptp4l_offset_ns"].dropna().abs()
    phc2sys_abs = health["phc2sys_offset"].dropna().abs()

    ptp4l_p95  = ptp4l_abs.quantile(0.95)
    phc2sys_p95 = phc2sys_abs.quantile(0.95)
    ptp4l_med  = ptp4l_abs.median()
    phc2sys_med = phc2sys_abs.median()

    ok = (ptp4l_p95 < threshold_ns) and (phc2sys_p95 < threshold_ns)
    summary = (
        f"ptp4l p95={ptp4l_p95/1e3:.1f}µs med={ptp4l_med/1e3:.1f}µs  "
        f"phc2sys p95={phc2sys_p95/1e3:.1f}µs med={phc2sys_med/1e3:.1f}µs"
    )
    return ok, summary


# ---------------------------------------------------------------------------
# Timestamp loading and alignment
# ---------------------------------------------------------------------------

def load_timestamps(camera_dir: Path) -> np.ndarray:
    """Load and concatenate per-frame timestamp_ns arrays across all segments."""
    csvs = sorted(camera_dir.glob("*_timestamps.csv"),
                  key=lambda p: _segment_index(p))
    if not csvs:
        sys.exit(f"No *_timestamps.csv found in {camera_dir}")

    parts = []
    for p in csvs:
        df = pd.read_csv(p, usecols=["timestamp_ns"])
        ts = pd.to_numeric(df["timestamp_ns"], errors="coerce").dropna().values
        parts.append(ts)
    return np.concatenate(parts)


def _segment_index(p: Path) -> int:
    m = re.search(r'_\((\d+)_', p.name)
    return int(m.group(1)) if m else 0


def compute_alignment(timestamps: dict[str, np.ndarray]) -> dict[str, int]:
    """Return {camera_id: frame_skip} so all cameras start at the same PTP time.

    Strategy: take the camera whose first frame is latest (i.e. the one that
    started last) as the common start. For every other camera, find the frame
    index whose timestamp is nearest to that common start.
    """
    t0s = {cam: ts[0] for cam, ts in timestamps.items()}
    common_start = max(t0s.values())  # latest first-frame across cameras

    skips: dict[str, int] = {}
    for cam, ts in timestamps.items():
        idx = int(np.argmin(np.abs(ts - common_start)))
        skips[cam] = idx

    return skips


def alignment_residuals(timestamps: dict[str, np.ndarray],
                        skips: dict[str, int]) -> dict[str, float]:
    """Return residual offset in µs after alignment for each camera relative to
    the reference (skip=0) camera."""
    aligned_t0s = {cam: timestamps[cam][skips[cam]] for cam in timestamps}
    ref_t0 = min(aligned_t0s.values())
    return {cam: (t0 - ref_t0) / 1e3 for cam, t0 in aligned_t0s.items()}


# ---------------------------------------------------------------------------
# Video file discovery
# ---------------------------------------------------------------------------

def find_video_segments(camera_dir: Path) -> list[Path]:
    """Return sorted .ts files for this camera, in segment order."""
    return sorted(camera_dir.glob("*.ts"), key=lambda p: _segment_index(p))


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------

def build_ffmpeg_cmd(
    inputs: list[tuple[Path, int]],   # [(video_path, skip_frames), ...]
    output: Path,
    layout: str,
    fps: int,
    n_frames: int,
) -> list[str]:
    """Build the ffmpeg command for frame-aligned multi-camera video output.

    Uses the select filter to drop leading frames rather than time-based seek,
    so alignment is exact to the frame.
    """
    n = len(inputs)
    cmd = ["ffmpeg", "-y"]

    for video, _ in inputs:
        cmd += ["-i", str(video)]

    filter_parts = []
    labels = []
    for i, (_, skip) in enumerate(inputs):
        label = f"v{i}"
        if skip > 0:
            filter_parts.append(
                f"[{i}:v]select=gte(n\\,{skip}),setpts=PTS-STARTPTS[{label}]"
            )
        else:
            filter_parts.append(f"[{i}:v]setpts=PTS-STARTPTS[{label}]")
        labels.append(f"[{label}]")

    joined = "".join(labels)
    if layout == "stack":
        filter_parts.append(f"{joined}vstack=inputs={n}[out]")
    elif layout == "grid" and n == 4:
        filter_parts.append(f"[v0][v1]hstack[top];[v2][v3]hstack[bot];[top][bot]vstack[out]")
    else:  # side (hstack) — default
        filter_parts.append(f"{joined}hstack=inputs={n}[out]")

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", "[out]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-frames:v", str(n_frames),
        str(output),
    ]
    return cmd


def concat_segments(segments: list[Path], tmp_dir: str) -> Path:
    """Concatenate multiple .ts segments into a single file using ffmpeg concat."""
    list_path = Path(tmp_dir) / "concat.txt"
    with open(list_path, "w") as f:
        for seg in segments:
            f.write(f"file '{seg.resolve()}'\n")
    out_path = Path(tmp_dir) / "concat.ts"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(list_path), "-c", "copy", str(out_path)],
        check=True, capture_output=True,
    )
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_dir", type=Path,
                    help="Session directory or date subdirectory")
    ap.add_argument("--output", "-o", type=Path, default=None,
                    help="Output video path (default: SESSION_DIR/aligned.mp4)")
    ap.add_argument("--layout", choices=["side", "stack", "grid"], default="side",
                    help="Output layout: side-by-side (default), stacked, or 2×2 grid")
    ap.add_argument("--ptp-threshold", type=int, default=50,
                    help="Max allowed PTP offset p95 in µs (default: 50)")
    ap.add_argument("--include-unsynced", action="store_true",
                    help="Include cameras with sync_mode='none' (not recommended)")
    args = ap.parse_args()

    session_dir = args.session_dir.resolve()
    if not session_dir.exists():
        sys.exit(f"Directory not found: {session_dir}")

    threshold_ns = args.ptp_threshold * 1_000

    # Find date dirs
    date_dirs = find_date_dirs(session_dir)
    if len(date_dirs) > 1:
        print(f"Multiple date dirs found; using {date_dirs[0].name} (pass a specific date dir to override)")
    date_dir = date_dirs[0]

    camera_dirs = find_camera_dirs(date_dir)
    if not camera_dirs:
        sys.exit(f"No camera_* directories found under {date_dir}")

    print(f"\nSession : {session_dir.name}")
    print(f"Date    : {date_dir.name}")
    print(f"Cameras : {[d.name for d in camera_dirs]}\n")

    # --- Filter by sync_mode ---
    included: list[Path] = []
    for cam_dir in camera_dirs:
        cfg = load_config(cam_dir)
        mode = sync_mode(cfg)
        fps = cfg.get("camera", {}).get("fps", None)
        if mode == "none" and not args.include_unsynced:
            print(f"  SKIP  {cam_dir.name}  sync_mode=none  (use --include-unsynced to force)")
        else:
            label = f"sync_mode={mode}" if mode != "none" else "sync_mode=none (forced)"
            print(f"  OK    {cam_dir.name}  {label}  fps={fps}")
            included.append(cam_dir)

    if len(included) < 2:
        sys.exit("\nNeed at least 2 synced cameras to produce an aligned video.")

    # --- PTP check ---
    print("\n--- PTP synchronisation ---")
    ptp_ok_all = True
    for cam_dir in included:
        ok, summary = check_ptp(cam_dir, threshold_ns)
        status = "OK  " if ok else "WARN"
        print(f"  {status}  {cam_dir.name}  {summary}")
        if not ok:
            ptp_ok_all = False

    if not ptp_ok_all:
        print(f"\n  WARNING: one or more cameras exceeded the {args.ptp_threshold}µs PTP threshold.")
        print("  Frame timestamps may not be reliably synchronised. Proceeding anyway.\n")
    else:
        print(f"\n  All cameras within {args.ptp_threshold}µs threshold.\n")

    # --- Load timestamps and compute alignment ---
    print("--- Frame alignment ---")
    timestamps: dict[str, np.ndarray] = {}
    fps_per_cam: dict[str, int] = {}
    for cam_dir in included:
        ts = load_timestamps(cam_dir)
        timestamps[cam_dir.name] = ts
        cfg = load_config(cam_dir)
        fps_per_cam[cam_dir.name] = cfg.get("camera", {}).get("fps", 30)
        print(f"  {cam_dir.name}  {len(ts)} frames  t0={ts[0]/1e9:.3f}s")

    fps_values = list(fps_per_cam.values())
    if len(set(fps_values)) > 1:
        print(f"\n  WARNING: cameras have different fps {fps_per_cam} — alignment may be imprecise.")
    fps = fps_values[0]
    frame_period_us = 1e6 / fps

    skips = compute_alignment(timestamps)
    residuals = alignment_residuals(timestamps, skips)

    print()
    for cam, skip in skips.items():
        res = residuals[cam]
        print(f"  {cam}  skip={skip} frames ({skip/fps*1000:.2f}ms)  residual={res:.1f}µs  ({abs(res)/frame_period_us*100:.1f}% of frame period)")

    # Usable frame count = shortest aligned sequence
    n_frames = min(len(timestamps[cam]) - skips[cam] for cam in timestamps)
    print(f"\n  Aligned duration: {n_frames} frames = {n_frames/fps:.2f}s @ {fps}fps\n")

    # --- Find video files, concatenate segments if needed ---
    print("--- Video files ---")
    inputs: list[tuple[Path, int]] = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        for cam_dir in included:
            segments = find_video_segments(cam_dir)
            if not segments:
                sys.exit(f"No .ts files found in {cam_dir}")
            if len(segments) > 1:
                print(f"  {cam_dir.name}  {len(segments)} segments — concatenating...")
                video = concat_segments(segments, tmp_dir)
            else:
                video = segments[0]
                print(f"  {cam_dir.name}  {video.name}")
            inputs.append((video, skips[cam_dir.name]))

        # --- Build output path ---
        if args.output:
            output = args.output.resolve()
        else:
            output = session_dir / f"{session_dir.name}_aligned.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)

        # --- Run ffmpeg ---
        cmd = build_ffmpeg_cmd(inputs, output, args.layout, fps, n_frames)
        print(f"\n--- Encoding → {output} ---")
        print(f"  {' '.join(cmd)}\n")

        result = subprocess.run(cmd)
        if result.returncode != 0:
            sys.exit(f"\nffmpeg failed (exit {result.returncode})")

    print(f"\nDone: {output}")
    print(f"  {n_frames} frames  {n_frames/fps:.2f}s  {fps}fps  layout={args.layout}")
    if not ptp_ok_all:
        print("  (PTP threshold exceeded — verify timestamps before use)")


if __name__ == "__main__":
    main()
