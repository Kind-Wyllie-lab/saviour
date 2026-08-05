"""
Compose a session's per-camera recordings into a single layout video,
aligned by each camera's real per-frame capture timestamp rather than by
raw frame index.

Prototype for the "aggregate session video" frontend feature described in
CLAUDE.md's Feature ideas section — currently a standalone CLI, not wired
into web.py/Flask yet. Cameras in a session commonly run at different real
framerates even when nominally "the same" (see Hardware gotchas in
CLAUDE.md), so pairing frame i<->i<->i drifts noticeably over a session.
This instead resamples every stream onto one common wall-clock grid built
from each camera's `_timestamps.csv` `timestamp_ns` column.

Unlike tools/make_aligned_video.py, this does not require PTP framesync
(camera.sync_mode) — it works from each camera's own capture timestamps,
so it also produces a (best-effort) result for unsynced sessions. It has
no ffmpeg dependency — composites directly through OpenCV's bundled
ffmpeg-backed VideoCapture/VideoWriter, so it also works on a machine
with no system ffmpeg install.

Usage:
    python3 src/controller/video_compose.py /path/to/session/date_dir [--output out.mp4]

    # e.g., matching the tools/analyse_framesync.py convention:
    python3 src/controller/video_compose.py \
        /home/pi/controller_share/my-session/20260703
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
from dataclasses import dataclass

import cv2
import numpy as np

DEFAULT_CANVAS_WIDTH = 1920
DEFAULT_FPS = 30


@dataclass
class CameraStream:
    name: str
    video_path: str
    csv_path: str


def discover_camera_streams(date_dir: str) -> list[CameraStream]:
    """Find every module subfolder in a session's date directory that looks
    like a camera recording: a video file plus a matching *_timestamps.csv
    with a timestamp_ns column. Non-camera modules (microphone, ttl, ...)
    are silently skipped since they don't produce per-frame video."""
    streams = []
    for entry in sorted(os.listdir(date_dir)):
        module_dir = os.path.join(date_dir, entry)
        if not os.path.isdir(module_dir):
            continue
        videos = sorted(
            glob.glob(os.path.join(module_dir, "*.ts"))
            + glob.glob(os.path.join(module_dir, "*.mp4"))
        )
        csvs = glob.glob(os.path.join(module_dir, "*_timestamps.csv"))
        if not videos or not csvs:
            continue
        with open(csvs[0], newline="") as f:
            header = next(csv.reader(f), [])
        if "timestamp_ns" not in header:
            continue
        streams.append(CameraStream(name=entry, video_path=videos[0], csv_path=csvs[0]))
    return streams


class _StreamCursor:
    """Sequential, forward-only frame+timestamp reader for one camera.

    Advances by decoding frames in order (never seeks — .ts/MPEG-TS
    seeking via OpenCV is not reliably frame-accurate) and tracks which
    decoded frame is currently the best match for a requested wall-clock
    time.
    """

    def __init__(self, stream: CameraStream):
        self.name = stream.name
        self.cap = cv2.VideoCapture(stream.video_path)
        with open(stream.csv_path, newline="") as f:
            self.timestamps_ns = [int(row["timestamp_ns"]) for row in csv.DictReader(f)]
        self.idx = -1
        self.frame = None
        self._advance()

    def _advance(self) -> bool:
        ok, frame = self.cap.read()
        if not ok:
            return False
        self.idx += 1
        self.frame = frame
        return True

    def sync_to(self, t_ns: int):
        """Advance while the *next* decoded frame is closer to t_ns than
        the current one, then return the current frame."""
        while self.idx + 1 < len(self.timestamps_ns):
            cur_ts = self.timestamps_ns[self.idx]
            nxt_ts = self.timestamps_ns[self.idx + 1]
            if abs(nxt_ts - t_ns) > abs(cur_ts - t_ns):
                break
            if not self._advance():
                break
        return self.frame

    @property
    def first_ts(self) -> int:
        return self.timestamps_ns[0]

    @property
    def last_ts(self) -> int:
        return self.timestamps_ns[-1]

    def release(self):
        self.cap.release()


def _grid_regions(
    n: int, canvas_width: int = DEFAULT_CANVAS_WIDTH, pane_aspect: float = 16 / 9
) -> tuple[list[tuple[int, int, int, int]], int, int]:
    """Evenly-sized grid fallback for an arbitrary number of cameras.
    Returns (regions, canvas_width, canvas_height); each region is
    (x, y, w, h), w/h forced even for codec compatibility."""
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    pane_w = (canvas_width // cols) // 2 * 2
    pane_h = round(pane_w / pane_aspect / 2) * 2
    regions = [
        ((i % cols) * pane_w, (i // cols) * pane_h, pane_w, pane_h) for i in range(n)
    ]
    return regions, pane_w * cols, pane_h * rows


def _loom_regions(
    streams: list[CameraStream], right_width: int = 640
) -> tuple[list[tuple[int, int, int, int]], int, int] | None:
    """Named layout for the loom rig's 3-camera set: LoomCam large on the
    left, Home top-right (square), ScreenCam bottom-right (16:9). Matches
    stream folder names case-insensitively; returns None (falls back to
    the grid layout) if the rig doesn't have exactly this camera set."""
    expected_stream_count = 3
    by_key = {s.name.lower(): s for s in streams}
    loom = next((s for k, s in by_key.items() if "loom" in k), None)
    home = next((s for k, s in by_key.items() if "home" in k), None)
    screen = next((s for k, s in by_key.items() if "screen" in k), None)
    if not (loom and home and screen) or len(streams) != expected_stream_count:
        return None

    home_h = right_width
    screen_h = round(right_width * 9 / 16 / 2) * 2
    right_h = home_h + screen_h
    loom_w = round(right_h * 16 / 9 / 2) * 2

    order = [loom, home, screen]
    regions = {
        loom.name: (0, 0, loom_w, right_h),
        home.name: (loom_w, 0, right_width, home_h),
        screen.name: (loom_w, home_h, right_width, screen_h),
    }
    ordered_regions = [regions[s.name] for s in order]
    return ordered_regions, loom_w + right_width, right_h, order


def _label(frame: np.ndarray, text: str) -> np.ndarray:
    cv2.rectangle(frame, (0, 0), (14 * len(text) + 12, 34), (0, 0, 0), -1)
    cv2.putText(frame, text, (6, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return frame


def compose_session_video(
    date_dir: str,
    output_path: str,
    layout: str = "auto",
    fps: int = DEFAULT_FPS,
) -> str:
    streams = discover_camera_streams(date_dir)
    if not streams:
        raise ValueError(
            f"No camera streams (video + *_timestamps.csv) found under {date_dir}"
        )

    ordered = streams
    loom_layout = _loom_regions(streams) if layout in ("auto", "loom") else None
    if loom_layout is not None:
        regions, canvas_w, canvas_h, ordered = loom_layout
    else:
        if layout == "loom":
            raise ValueError(
                "--layout loom requires exactly one "
                "LoomCam/Home/ScreenCam-named stream each"
            )
        regions, canvas_w, canvas_h = _grid_regions(len(streams))

    cursors = [_StreamCursor(s) for s in ordered]
    t_start = max(c.first_ts for c in cursors)
    t_end = min(c.last_ts for c in cursors)
    if t_end <= t_start:
        raise ValueError(
            "Camera streams in this session have no overlapping time window"
        )

    step_ns = int(1e9 / fps)
    n_out = int((t_end - t_start) / step_ns)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, fps, (canvas_w, canvas_h))
    if not writer.isOpened():
        raise RuntimeError("VideoWriter failed to open — codec unavailable")

    try:
        for i in range(n_out):
            t = t_start + i * step_ns
            canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
            for cursor, (x, y, w, h) in zip(cursors, regions, strict=True):
                pane = _label(cv2.resize(cursor.sync_to(t), (w, h)), cursor.name)
                canvas[y : y + h, x : x + w] = pane
            writer.write(canvas)
    finally:
        writer.release()
        for cursor in cursors:
            cursor.release()

    return output_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "date_dir", help="Session date directory, e.g. /path/to/session/20260804"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output .mp4 path (default: <date_dir>/../<session>_aggregated.mp4)",
    )
    parser.add_argument("--layout", choices=["auto", "loom"], default="auto")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    args = parser.parse_args()

    session_dir = os.path.dirname(os.path.normpath(args.date_dir))
    session_name = os.path.basename(session_dir)
    output = args.output or os.path.join(session_dir, f"{session_name}_aggregated.mp4")
    result = compose_session_video(
        args.date_dir, output, layout=args.layout, fps=args.fps
    )
    print(f"Wrote {result}")


if __name__ == "__main__":
    main()
