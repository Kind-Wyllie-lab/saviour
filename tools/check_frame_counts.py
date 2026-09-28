#!/usr/bin/env python3
"""
check_frame_counts.py -- per-camera CSV-rows vs .ts-frames reconciliation
for a session date dir.

Diagnoses the sync-client `.ts`/CSV frame-count skew (a client camera lags
the sync server in a composite/ethogram -- see
plans/multicam-frame-alignment-and-sync-provenance.md). Run it against a
session recorded with `recording.fix_positioning_timestamps` on and again
with it off (A4): if the deficit vanishes with the remux off, the remux is
the cause.

    python3 tools/check_frame_counts.py /path/to/session/DATEDIR

Needs ffprobe on PATH for the container counts; falls back to OpenCV's
decode count if ffprobe is absent.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys


def _csv_rows(path: str) -> int:
    with open(path, newline="") as f:
        return max(0, sum(1 for _ in f) - 1)  # minus header


def _ffprobe(video: str, entry: str) -> int | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             f"-count_{'frames' if entry == 'nb_read_frames' else 'packets'}",
             "-show_entries", f"stream={entry}", "-of", "csv=p=0", video],
            capture_output=True, text=True, check=True,
        )
        for line in out.stdout.splitlines():
            if line.strip().isdigit():
                return int(line.strip())
    except (OSError, subprocess.CalledProcessError):
        return None
    return None


def _cv2_frames(video: str) -> int | None:
    try:
        import cv2
    except ImportError:
        return None
    cap = cv2.VideoCapture(video)
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    return n


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    date_dir = sys.argv[1]

    hdr = (f"{'camera':<16} {'csv_rows':>9} {'ts_frames':>9} {'ts_pkts':>9} "
           f"{'cv2':>7} {'deficit':>8} {'json_rows':>9} {'remuxed':>8}")
    print(hdr)
    print("-" * len(hdr))

    any_found = False
    for entry in sorted(os.listdir(date_dir)):
        cam_dir = os.path.join(date_dir, entry)
        if not os.path.isdir(cam_dir):
            continue
        for video in sorted(glob.glob(os.path.join(cam_dir, "*.ts"))
                            + glob.glob(os.path.join(cam_dir, "*.mp4"))):
            stem = os.path.splitext(video)[0]
            csv_path = f"{stem}_timestamps.csv"
            json_path = f"{stem}_recording.json"
            if not os.path.isfile(csv_path):
                continue
            any_found = True
            rows = _csv_rows(csv_path)
            frames = _ffprobe(video, "nb_read_frames")
            pkts = _ffprobe(video, "nb_read_packets")
            cv2n = _cv2_frames(video) if frames is None else None
            ref = frames if frames is not None else cv2n
            deficit = (rows - ref) if ref is not None else None
            jrows = jremux = "-"
            if os.path.isfile(json_path):
                try:
                    j = json.load(open(json_path))
                    jrows = j.get("csv_rows_written", "-")
                    jremux = j.get("positioning_timestamps_remuxed", "-")
                except (OSError, ValueError):
                    pass
            flag = ""
            if deficit is not None and abs(deficit) > 1:
                flag = "  <-- skew"
            print(f"{entry:<16} {rows:>9} "
                  f"{('?' if frames is None else frames):>9} "
                  f"{('?' if pkts is None else pkts):>9} "
                  f"{('?' if cv2n is None else cv2n):>7} "
                  f"{('?' if deficit is None else f'{deficit:+d}'):>8} "
                  f"{str(jrows):>9} {str(jremux):>8}{flag}")

    if not any_found:
        print(f"(no camera streams with a *_timestamps.csv found under {date_dir})")
        return 1
    print("\ncsv_rows = frames the capture callback logged (authoritative).")
    print("deficit  = csv_rows - ts_frames; >1 on a sync client => the skew.")
    print("Compare a `fix_positioning_timestamps` on vs off pair: if the "
          "deficit vanishes with it off, the remux is the cause.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
