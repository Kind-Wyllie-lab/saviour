"""A camera module folder's recording segments, in recording order.

A session's day folder holds one file set per segment per camera (hourly by
default, `recording.segment_length_mins`), e.g.

    camera_0fcc/<session>_camera_0fcc_(3_20261013-151400).ts
    camera_0fcc/<session>_camera_0fcc_(3_20261013-151400)_timestamps.csv

Each video pairs with the `_timestamps.csv` of the same stem (a salvaged
`…_PARTIAL.ts` with `…_timestamps_PARTIAL.csv`). Order comes from each CSV's
first timestamp, not the file name: the segment index restarts at 0 when a
module re-arms mid-session, so names don't sort chronologically.

Compose used to take only the first video and an arbitrary CSV per camera,
so anything past the first segment was silently dropped (and could be
timed by the wrong segment's CSV). Shared by compose.py (ffmpeg render
planning) and video_compose.py (OpenCV frames), with no OpenCV dependency.
"""

from __future__ import annotations

import csv
import glob
import os
import threading
from dataclasses import dataclass

VIDEO_EXTS = (".ts", ".mp4")
_PARTIAL = "_PARTIAL"


@dataclass(frozen=True)
class Segment:
    video_path: str
    csv_path: str
    first_ns: int   # first per-frame timestamp in the CSV
    last_ns: int    # last


def timestamps_csv_for(video_path: str) -> str:
    """The per-frame CSV that belongs to `video_path`."""
    stem = os.path.splitext(video_path)[0]
    if stem.endswith(_PARTIAL):
        return stem[: -len(_PARTIAL)] + "_timestamps" + _PARTIAL + ".csv"
    return stem + "_timestamps.csv"


_BOUNDS_CACHE: dict[tuple, tuple[int, int] | None] = {}
_BOUNDS_LOCK = threading.Lock()


def csv_bounds(csv_path: str) -> tuple[int, int] | None:
    """(first, last) `timestamp_ns` of a per-frame CSV, or None if it has no
    such column or no rows. Reads only the head and the tail, so listing a
    long multi-segment session stays quick; cached per (path, size, mtime)."""
    try:
        st = os.stat(csv_path)
    except OSError:
        return None
    key = (csv_path, st.st_size, st.st_mtime_ns)
    with _BOUNDS_LOCK:
        if key in _BOUNDS_CACHE:
            return _BOUNDS_CACHE[key]
    value = _read_bounds(csv_path, st.st_size)
    with _BOUNDS_LOCK:
        if len(_BOUNDS_CACHE) > 4096:
            _BOUNDS_CACHE.clear()
        _BOUNDS_CACHE[key] = value
    return value


def _read_bounds(csv_path: str, size: int) -> tuple[int, int] | None:
    with open(csv_path, newline="") as f:
        header = next(csv.reader(f), [])
        if "timestamp_ns" not in header:
            return None
        col = header.index("timestamp_ns")
        first = None
        for row in csv.reader(f):
            first = _ts(row, col)
            if first is not None:
                break
    if first is None:
        return None
    with open(csv_path, "rb") as f:
        f.seek(max(0, size - 8192))
        tail = f.read().decode("utf-8", errors="ignore").splitlines()
    last = None
    for line in reversed(tail):
        last = _ts(next(csv.reader([line]), []), col)
        if last is not None:
            break
    return first, (last if last is not None and last >= first else first)


def _ts(row: list[str], col: int) -> int | None:
    try:
        return int(row[col])
    except (IndexError, ValueError):
        return None


def camera_segments(module_dir: str) -> list[Segment]:
    """Every usable video + timestamps pair in `module_dir`, oldest first.
    Skips files mid-export (`PENDING_`), empty videos (a salvage after a
    power cut can leave zero bytes), and videos with no usable CSV."""
    segments = []
    for ext in VIDEO_EXTS:
        for video in glob.glob(os.path.join(module_dir, f"*{ext}")):
            if os.path.basename(video).startswith("PENDING_"):
                continue
            csv_path = timestamps_csv_for(video)
            try:
                if os.path.getsize(video) == 0 or not os.path.isfile(csv_path):
                    continue
                bounds = csv_bounds(csv_path)
            except OSError:
                continue
            if bounds is not None:
                segments.append(Segment(video, csv_path, *bounds))
    segments.sort(key=lambda s: (s.first_ns, s.video_path))
    return segments


def segment_at(segments: list[Segment], t_ns: int) -> Segment:
    """The segment covering `t_ns`: the latest one starting at or before it
    (so a time in a gap maps to the segment before the gap)."""
    chosen = segments[0]
    for seg in segments:
        if seg.first_ns <= t_ns:
            chosen = seg
        else:
            break
    return chosen
