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
  python3 tile_recordings.py [--dry-run] [--session NAME] [--date YYYYMMDD]
  python3 tile_recordings.py --help

Cron example (run every 30 minutes, process all complete hour-groups):
  */30 * * * * /usr/local/src/saviour/env/bin/python3 /usr/local/src/saviour/tools/tile_recordings.py >> /var/log/tile_recordings.log 2>&1
"""

import argparse
import fcntl
import logging
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

RECORDING_DIR = Path("/home/saviour-smb/habitat_recording")
OUTPUT_DIR    = Path("/home/saviour-smb/habitat_tiled")
LOCK_FILE     = Path("/tmp/tile_recordings.lock")

# ── Grid layout ───────────────────────────────────────────────────────────────

GRID = [
    "A1", "A2", "A3", "A4",
    "B1", "B2", "B3", "B4",
    "C1", "C2", "C3", "C4",
    "D1", "D2", "D3", "D4",
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
) -> list:
    """
    Build an ffmpeg xstack command for 16 tiles.
    Each missing grid position gets its own lavfi black source so ffmpeg never
    needs to read the same input pad twice (avoids filter_complex fan-out issues).
    """
    cmd  = ["ffmpeg", "-y"]
    idx  = 0          # running input index
    slot_index: dict = {}  # grid pos -> input index

    for pos in GRID:
        if pos in group_files:
            cmd += ["-i", str(group_files[pos])]
        else:
            cmd += ["-f", "lavfi", "-i",
                    f"color=c=black:s={TILE_SIZE}x{TILE_SIZE}:r=25:d=3600"]
        slot_index[pos] = idx
        idx += 1

    # Scale each slot to TILE_SIZE×TILE_SIZE and feed xstack
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

    # AV1 settings: CRF 35 is visually good for surveillance-style content
    if encoder == "libsvtav1":
        enc_args = ["-c:v", "libsvtav1", "-crf", "35", "-preset", "4"]
    else:  # libaom-av1
        enc_args = ["-c:v", "libaom-av1", "-crf", "35", "-cpu-used", "4", "-row-mt", "1"]

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
    dry_run: bool,
    logger: logging.Logger,
) -> str:
    out_dir = output_dir / session / date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{session}_{hour}_4x4.mkv"

    if out_path.exists():
        logger.debug(f"Skip (exists): {out_path.name}")
        return "skipped"

    present = sorted(group_files)
    missing = [p for p in GRID if p not in group_files]
    logger.info(
        f"{session}/{date}/{hour}: {len(present)}/16 cameras"
        + (f" — MISSING: {missing}" if missing else " — all present")
    )

    if not present:
        logger.warning("No camera files for this group, skipping")
        return "skipped"

    cmd = build_ffmpeg_cmd(group_files, out_path, encoder)

    if dry_run:
        logger.info("DRY RUN: " + " ".join(str(c) for c in cmd))
        return "dry_run"

    logger.info(f"Encoding → {out_path.name}")
    # Stream stderr so ffmpeg progress is visible; stdout is unused by ffmpeg
    stderr_lines = []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        for line in proc.stderr:
            line = line.rstrip()
            stderr_lines.append(line)
            # ffmpeg writes "frame=... fps=... time=..." progress lines to stderr
            if line.startswith("frame="):
                print(f"\r  {line}", end="", flush=True)
        proc.wait()
        print()  # newline after progress
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
        out_path.unlink(missing_ok=True)
        logger.info("Interrupted — partial output removed")
        raise

    if proc.returncode != 0:
        logger.error(f"ffmpeg failed (exit {proc.returncode}):\n" + "\n".join(stderr_lines[-50:]))
        out_path.unlink(missing_ok=True)
        return "failed"

    size_mb = out_path.stat().st_size / 1_048_576
    logger.info(f"Done: {out_path.name} ({size_mb:.1f} MB)")
    return "ok"


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Tile 16-camera SAVIOUR recordings into 4×4 AV1 video")
    parser.add_argument("--recording-dir", type=Path, default=RECORDING_DIR,
                        help=f"Root recording directory (default: {RECORDING_DIR})")
    parser.add_argument("--output-dir",    type=Path, default=OUTPUT_DIR,
                        help=f"Root output directory (default: {OUTPUT_DIR})")
    parser.add_argument("--session", help="Process only this session name")
    parser.add_argument("--date",    help="Process only this date (YYYYMMDD)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print ffmpeg commands without running them")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logger = logging.getLogger("tile_recordings")

    # Prevent concurrent runs (cron-safe)
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.info("Another instance is already running — exiting")
        sys.exit(0)

    try:
        encoder = detect_av1_encoder()
        logger.info(f"AV1 encoder: {encoder}")

        groups = find_groups(args.recording_dir)
        all_groups = sorted(groups.items())
        if args.session:
            all_groups = [(k, v) for k, v in all_groups if k[0] == args.session]
        if args.date:
            all_groups = [(k, v) for k, v in all_groups if k[1] == args.date]
        logger.info(f"Discovered {len(all_groups)} hour-groups to process")

        counts: dict = {"ok": 0, "skipped": 0, "failed": 0, "dry_run": 0}

        for i, ((session, date, hour), group_files) in enumerate(all_groups, 1):
            logger.info(f"[{i}/{len(all_groups)}]")
            if args.session and session != args.session:
                continue
            if args.date and date != args.date:
                continue

            status = process_group(
                session, date, hour, group_files,
                args.output_dir, encoder, args.dry_run, logger,
            )
            counts[status] = counts.get(status, 0) + 1
            done = counts["ok"] + counts["failed"]
            if done:
                logger.info(f"Progress: {done} encoded, {counts['skipped']} skipped, {counts['failed']} failed")

        logger.info(
            f"Finished — ok:{counts['ok']} skipped:{counts['skipped']} "
            f"failed:{counts['failed']}" +
            (f" dry_run:{counts['dry_run']}" if args.dry_run else "")
        )

    finally:
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


if __name__ == "__main__":
    main()
