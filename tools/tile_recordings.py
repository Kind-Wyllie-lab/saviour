#!/usr/bin/env python3
"""
tile_recordings.py — Consolidate 16-camera SAVIOUR recordings into 4x4 AV1 tiled video.

Scans RECORDING_DIR for .ts files matching the SAVIOUR filename convention:
  <session>/<date>/<module>/<session>_<module>_(<seg_id>_<YYYYMMDD-HHMMSS>).ts

Groups by session + date + hour, then tiles all 16 cameras (A1–D4) into a single
4000×4000 AV1 video. Missing cameras are replaced with a black placeholder.

Output:
  OUTPUT_DIR/<session>/<date>/<session>_<YYYYMMDD-HH>_4x4.mkv

Usage:
  python3 tile_recordings.py [--dry-run] [--session NAME] [--date YYYYMMDD] [--workers N]
  python3 tile_recordings.py --help

Cron example (run every 30 minutes, process all complete hour-groups):
  */30 * * * * python3 /usr/local/src/saviour/tools/tile_recordings.py >> /var/log/tile_recordings.log 2>&1
"""

import argparse
import fcntl
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

RECORDING_DIR = Path("/home/saviour-smb/habitat_recording")
OUTPUT_DIR    = Path("/home/saviour-smb/habitat_tiled")
LOCK_FILE     = Path("/var/tmp/tile_recordings.lock")

# ── Grid layout ───────────────────────────────────────────────────────────────

GRID = [
    "A4", "A3", "A2", "A1",
    "B4", "B3", "B2", "B1",
    "C4", "C3", "C2", "C1",
    "D4", "D3", "D2", "D1",
]
TILE_SIZE = 1000  # px per camera; scale filter handles non-exact source sizes

# ── Filename pattern: <session>_<module>_(<seg_id>_<YYYYMMDD-HHMMSS>).ts ─────

_FILENAME_RE = re.compile(
    r"^(?P<session>.+)_(?P<module>[A-D][1-4])_\((?P<seg>\d+)_(?P<ts>\d{8}-\d{6})\)\.ts$"
)


# ── Discovery ─────────────────────────────────────────────────────────────────

def find_groups(recording_dir: Path) -> dict:
    """
    Return {(session, date, hour): {module: Path}}

    Groups by the first 11 characters of the timestamp (YYYYMMDD-HH) so that
    small per-camera start-time skew doesn't create phantom duplicates.
    """
    groups: dict = defaultdict(dict)

    for f in recording_dir.glob("*/*/*/*.ts"):
        m = _FILENAME_RE.match(f.name)
        if not m:
            continue
        session = m.group("session")
        module  = m.group("module")
        hour    = m.group("ts")[:11]          # "20260411-08"
        date    = f.parent.parent.name        # directory name, e.g. "20260411"
        key     = (session, date, hour)
        if module not in groups[key]:          # keep earliest segment if >1 per hour
            groups[key][module] = f

    return groups


# ── AV1 encoder detection ─────────────────────────────────────────────────────

def detect_av1_encoder() -> str:
    result = subprocess.run(
        ["ffmpeg", "-encoders", "-v", "quiet"], capture_output=True, text=True
    )
    for enc in ("libsvtav1", "libaom-av1"):
        if enc in result.stdout:
            return enc
    raise RuntimeError("No AV1 encoder found — install libsvtav1 or libaom-av1")


# ── ffmpeg command builder ────────────────────────────────────────────────────

def build_ffmpeg_cmd(
    group_files: dict,
    output_path: Path,
    encoder: str,
    threads_per_job: int,
) -> list:
    """
    Build an ffmpeg xstack command for 16 tiles.
    Each missing grid position gets its own lavfi black source so ffmpeg never
    needs to read the same input pad twice (avoids filter_complex fan-out issues).
    """
    cmd  = ["ffmpeg", "-y"]
    idx  = 0
    slot_index: dict = {}

    for pos in GRID:
        if pos in group_files:
            cmd += ["-i", str(group_files[pos])]
        else:
            cmd += ["-f", "lavfi", "-i",
                    f"color=c=black:s={TILE_SIZE}x{TILE_SIZE}:r=25:d=3600"]
        slot_index[pos] = idx
        idx += 1

    scale_parts = [
        f"[{slot_index[pos]}:v]scale={TILE_SIZE}:{TILE_SIZE}:force_original_aspect_ratio=decrease,"
        f"pad={TILE_SIZE}:{TILE_SIZE}:(ow-iw)/2:(oh-ih)/2[v{i}]"
        for i, pos in enumerate(GRID)
    ]
    input_labels = "".join(f"[v{i}]" for i in range(16))
    layout = "|".join(
        f"{(i % 4) * TILE_SIZE}_{(i // 4) * TILE_SIZE}" for i in range(16)
    )
    xstack = f"{input_labels}xstack=inputs=16:layout={layout}[out]"
    filter_complex = ";".join(scale_parts) + ";" + xstack

    if encoder == "libsvtav1":
        enc_args = [
            "-c:v", "libsvtav1", "-crf", "35", "-preset", "10",
            "-svtav1-params", f"lp={threads_per_job}",
        ]
    else:  # libaom-av1
        enc_args = [
            "-c:v", "libaom-av1", "-crf", "35", "-cpu-used", "8",
            "-row-mt", "1", "-threads", str(threads_per_job),
        ]

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-an",
        *enc_args,
        str(output_path),
    ]
    return cmd


# ── Per-group processing ──────────────────────────────────────────────────────

def process_group(
    session: str,
    date: str,
    hour: str,
    group_files: dict,
    output_dir: Path,
    encoder: str,
    threads_per_job: int,
    dry_run: bool,
    show_progress: bool,
) -> tuple[str, str]:
    """Returns (status, label) where status is ok/skipped/failed/dry_run."""
    logger = logging.getLogger("tile_recordings")
    label  = f"{session}/{date}/{hour}"

    out_dir = output_dir / session / date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{session}_{hour}_4x4.mkv"

    if out_path.exists():
        return "skipped", label

    present = sorted(group_files)
    missing = [p for p in GRID if p not in group_files]
    logger.info(
        f"{label}: {len(present)}/16 cameras"
        + (f" — MISSING: {missing}" if missing else " — all present")
    )

    if not present:
        logger.warning(f"{label}: no camera files, skipping")
        return "skipped", label

    cmd = build_ffmpeg_cmd(group_files, out_path, encoder, threads_per_job)

    if dry_run:
        logger.info("DRY RUN: " + " ".join(str(c) for c in cmd))
        return "dry_run", label

    logger.info(f"Encoding → {out_path.name}")
    stderr_lines = []
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        for line in proc.stderr:
            line = line.rstrip()
            stderr_lines.append(line)
            if show_progress and line.startswith("frame="):
                print(f"\r  {line}", end="", flush=True)
        proc.wait()
        if show_progress:
            print()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
        out_path.unlink(missing_ok=True)
        logger.info(f"{label}: interrupted — partial output removed")
        raise

    if proc.returncode != 0:
        logger.error(
            f"{label}: ffmpeg failed (exit {proc.returncode}):\n"
            + "\n".join(stderr_lines[-50:])
        )
        out_path.unlink(missing_ok=True)
        return "failed", label

    size_mb = out_path.stat().st_size / 1_048_576
    logger.info(f"Done: {out_path.name} ({size_mb:.1f} MB)")
    return "ok", label


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    cpu_count = os.cpu_count() or 1

    parser = argparse.ArgumentParser(
        description="Tile 16-camera SAVIOUR recordings into 4×4 AV1 video"
    )
    parser.add_argument("--recording-dir", type=Path, default=RECORDING_DIR)
    parser.add_argument("--output-dir",    type=Path, default=OUTPUT_DIR)
    parser.add_argument("--session", help="Process only this session name")
    parser.add_argument("--date",    help="Process only this date (YYYYMMDD)")
    parser.add_argument("--workers", type=int, default=1,
                        help=f"Parallel encode jobs (default: 1, this machine has {cpu_count} cores)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logger = logging.getLogger("tile_recordings")

    # Prevent concurrent cron runs
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.info("Another instance is already running — exiting")
        sys.exit(0)

    try:
        encoder = detect_av1_encoder()
        # Divide CPU cores evenly across parallel jobs so they don't fight each other
        threads_per_job = max(1, cpu_count // args.workers)
        logger.info(
            f"AV1 encoder: {encoder} | workers: {args.workers} | "
            f"threads/job: {threads_per_job} | total cores: {cpu_count}"
        )

        groups = find_groups(args.recording_dir)
        all_groups = sorted(groups.items())
        if args.session:
            all_groups = [(k, v) for k, v in all_groups if k[0] == args.session]
        if args.date:
            all_groups = [(k, v) for k, v in all_groups if k[1] == args.date]
        logger.info(f"Discovered {len(all_groups)} hour-groups to process")

        counts: dict = {"ok": 0, "skipped": 0, "failed": 0, "dry_run": 0}
        show_progress = args.workers == 1

        if args.workers == 1:
            for i, ((session, date, hour), group_files) in enumerate(all_groups, 1):
                logger.info(f"[{i}/{len(all_groups)}]")
                status, _ = process_group(
                    session, date, hour, group_files,
                    args.output_dir, encoder, threads_per_job, args.dry_run, show_progress,
                )
                counts[status] += 1
                done = counts["ok"] + counts["failed"]
                if done:
                    logger.info(
                        f"Progress: {done} encoded, {counts['skipped']} skipped, "
                        f"{counts['failed']} failed"
                    )
        else:
            completed = 0
            total = len(all_groups)
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {
                    pool.submit(
                        process_group,
                        session, date, hour, group_files,
                        args.output_dir, encoder, threads_per_job, args.dry_run, False,
                    ): (session, date, hour)
                    for (session, date, hour), group_files in all_groups
                }
                for future in as_completed(futures):
                    completed += 1
                    try:
                        status, label = future.result()
                    except Exception as exc:
                        logger.error(f"Worker raised exception: {exc}")
                        status = "failed"
                    counts[status] += 1
                    logger.info(
                        f"[{completed}/{total}] {status} — "
                        f"ok:{counts['ok']} skipped:{counts['skipped']} failed:{counts['failed']}"
                    )

        logger.info(
            f"Finished — ok:{counts['ok']} skipped:{counts['skipped']} "
            f"failed:{counts['failed']}"
            + (f" dry_run:{counts['dry_run']}" if args.dry_run else "")
        )

    finally:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


if __name__ == "__main__":
    main()
