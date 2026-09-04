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
import subprocess
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

    def __init__(self, stream: CameraStream, skip: int = 0):
        self.name = stream.name
        self.cap = cv2.VideoCapture(stream.video_path)
        with open(stream.csv_path, newline="") as f:
            ts = [int(row["timestamp_ns"]) for row in csv.DictReader(f)]
        # Drop leading rows the CSV logged for frames captured before the
        # encoder started, so timestamps_ns[i] lines up with video frame i.
        self.timestamps_ns = ts[skip:] if 0 < skip < len(ts) else ts
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


def _probe_frame_count(video_path: str) -> int:
    """Video frame count via ffprobe packet count; 0 if ffprobe is absent
    or fails (this module otherwise has no ffmpeg dependency)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-count_packets", "-show_entries", "stream=nb_read_packets",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, check=True,
        )
        return int(out.stdout.strip() or 0)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 0


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


def _fit_into(frame: np.ndarray, w: int, h: int) -> np.ndarray:
    """Resize `frame` to fill (w, h) while preserving its aspect ratio,
    letterboxed onto black. Keeps a portrait pane from being squashed
    into a landscape box (and vice versa)."""
    src_h, src_w = frame.shape[:2]
    scale = min(w / src_w, h / src_h)
    new_w, new_h = max(1, int(src_w * scale)), max(1, int(src_h * scale))
    resized = cv2.resize(frame, (new_w, new_h))
    pane = np.zeros((h, w, 3), dtype=np.uint8)
    y0, x0 = (h - new_h) // 2, (w - new_w) // 2
    pane[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return pane


def compose_session_video(
    date_dir: str,
    output_path: str,
    layout: str = "auto",
    fps: int = DEFAULT_FPS,
    streams: list[str] | None = None,
    regions: list[tuple[int, int, int, int]] | None = None,
    canvas: tuple[int, int] | None = None,
    progress=None,
    csv_skip: dict[str, int] | None = None,
) -> str:
    """Compose the session's cameras into one layout video.

    `streams` restricts to those module folder names (order preserved).
    `regions` + `canvas` supply a pre-planned layout (from
    compose.plan_regions) and, when given, override `layout`. `progress`
    is called `progress(done, total, stage)` every ~1 % of frames.
    """
    found = discover_camera_streams(date_dir)
    if streams is not None:
        by_name = {s.name: s for s in found}
        missing = [s for s in streams if s not in by_name]
        if missing:
            raise ValueError(f"streams not found under {date_dir}: {missing}")
        found = [by_name[s] for s in streams]
    if not found:
        raise ValueError(
            f"No camera streams (video + *_timestamps.csv) found under {date_dir}"
        )

    ordered = found
    if regions is not None and canvas is not None:
        canvas_w, canvas_h = canvas
    else:
        loom_layout = _loom_regions(found) if layout in ("auto", "loom") else None
        if loom_layout is not None:
            regions, canvas_w, canvas_h, ordered = loom_layout
        else:
            if layout == "loom":
                raise ValueError(
                    "--layout loom requires exactly one "
                    "LoomCam/Home/ScreenCam-named stream each"
                )
            regions, canvas_w, canvas_h = _grid_regions(len(found))

    _skip = csv_skip or {}
    cursors = [_StreamCursor(s, _skip.get(s.name, 0)) for s in ordered]
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

    report_every = max(1, n_out // 100)
    try:
        for i in range(n_out):
            t = t_start + i * step_ns
            frame = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
            for cursor, (x, y, w, h) in zip(cursors, regions, strict=True):
                pane = _label(_fit_into(cursor.sync_to(t), w, h), cursor.name)
                frame[y : y + h, x : x + w] = pane
            writer.write(frame)
            if progress is not None and (i % report_every == 0 or i == n_out - 1):
                progress(i + 1, n_out, "rendering")
    finally:
        writer.release()
        for cursor in cursors:
            cursor.release()

    return output_path


def _csv_bounds(csv_path: str, skip: int = 0) -> tuple[int, int]:
    """(first_ts_ns, last_ts_ns) from a camera's per-frame CSV, without
    touching the (much more expensive to open) video file."""
    with open(csv_path, newline="") as f:
        ts = [int(row["timestamp_ns"]) for row in csv.DictReader(f)]
    if 0 < skip < len(ts):
        ts = ts[skip:]
    if not ts:
        raise ValueError(f"{csv_path} has no timestamps")
    return ts[0], ts[-1]


def _representative_frame(stream: CameraStream, t_ns: int, skip: int = 0) -> np.ndarray:
    """Decode the one frame closest to `t_ns` for a single camera."""
    cursor = _StreamCursor(stream, skip)
    try:
        return cursor.sync_to(t_ns)
    finally:
        cursor.release()


def _stream_thumb(
    stream: CameraStream, t_ns: int, cache_dir: str | None, skip: int = 0
) -> np.ndarray:
    """A representative frame for `stream`, read from a cached PNG in
    `cache_dir` when present, otherwise decoded from the video and written
    there for next time. The cache makes repeated "Preview layout" presses
    cheap -- no video decode at all once every stream is cached."""
    if cache_dir:
        thumb_path = os.path.join(cache_dir, f"{stream.name}.png")
        cached = cv2.imread(thumb_path) if os.path.isfile(thumb_path) else None
        if cached is not None:
            return cached
        frame = _representative_frame(stream, t_ns, skip)
        os.makedirs(cache_dir, exist_ok=True)
        cv2.imwrite(thumb_path, frame)
        return frame
    return _representative_frame(stream, t_ns, skip)


def _attach_audio_preview(
    frame: np.ndarray, audio_png: str, audio_mode: str
) -> np.ndarray:
    """Composite a spectrogram PNG into the preview the same way a real
    render places it: `panel` stacked below the video, `strip` overlaid on
    the bottom of the video."""
    spec_img = cv2.imread(audio_png)
    if spec_img is None:
        return frame
    ch, cw = frame.shape[:2]
    if audio_mode == "panel":
        panel_h = max(2, round(ch * 0.4) // 2 * 2)
        panel = cv2.resize(spec_img, (cw, panel_h))
        panel = _label(panel, "audio")
        return np.vstack([frame, panel])
    # strip
    strip_h = max(2, round(ch * 0.18) // 2 * 2)
    strip = cv2.resize(spec_img, (cw, strip_h))
    out = frame.copy()
    out[ch - strip_h : ch, 0:cw] = strip
    return out


def compose_preview_frame(
    date_dir: str, output_png: str,
    streams: list[str] | None = None,
    regions: list[tuple[int, int, int, int]] | None = None,
    canvas: tuple[int, int] | None = None,
    at_fraction: float = 0.5,
    cache_dir: str | None = None,
    audio_png: str | None = None,
    audio_mode: str | None = None,
    csv_skip: dict[str, int] | None = None,
) -> str:
    """Composite a single frame (at `at_fraction` through the overlap
    window) to a PNG -- a fast layout preview before a full render.

    `cache_dir`, when given, holds one representative PNG per camera so
    repeated previews skip video decode entirely. `audio_png` + `audio_mode`
    ("strip" | "panel") composite a spectrogram into the preview so audio
    layout modes are visible before rendering.
    """
    found = discover_camera_streams(date_dir)
    if streams is not None:
        by_name = {s.name: s for s in found}
        found = [by_name[s] for s in streams if s in by_name]
    if not found:
        raise ValueError(f"No camera streams found under {date_dir}")
    if regions is None or canvas is None:
        regions, cw, ch = _grid_regions(len(found))
    else:
        cw, ch = canvas

    _skip = csv_skip or {}
    bounds = [_csv_bounds(s.csv_path, _skip.get(s.name, 0)) for s in found]
    t_start = max(b[0] for b in bounds)
    t_end = min(b[1] for b in bounds)
    t = t_start + int((t_end - t_start) * max(0.0, min(1.0, at_fraction)))

    frame = np.zeros((ch, cw, 3), dtype=np.uint8)
    for stream, (x, y, w, h) in zip(found, regions, strict=True):
        thumb = _stream_thumb(stream, t, cache_dir, _skip.get(stream.name, 0))
        pane = _label(_fit_into(thumb, w, h), stream.name)
        frame[y : y + h, x : x + w] = pane

    if audio_png and audio_mode in ("strip", "panel"):
        frame = _attach_audio_preview(frame, audio_png, audio_mode)

    os.makedirs(os.path.dirname(output_png) or ".", exist_ok=True)
    if not cv2.imwrite(output_png, frame):
        raise RuntimeError("cv2.imwrite failed for the preview frame")
    return output_png


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

    # Best-effort pre-stage-row skip so a standalone run is aligned too. Needs
    # ffprobe; falls back to 0 (old behaviour) if it isn't on PATH.
    csv_skip = {}
    for s in discover_camera_streams(args.date_dir):
        try:
            with open(s.csv_path, newline="") as f:
                n_rows = sum(1 for _ in csv.DictReader(f))
            n_frames = _probe_frame_count(s.video_path)
            if n_frames:
                csv_skip[s.name] = max(0, n_rows - n_frames)
        except OSError:
            pass

    result = compose_session_video(
        args.date_dir, output, layout=args.layout, fps=args.fps, csv_skip=csv_skip
    )
    print(f"Wrote {result}")


if __name__ == "__main__":
    main()
