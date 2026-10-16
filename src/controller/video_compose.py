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
import logging
import math
import os
import subprocess
from dataclasses import dataclass, field

import cv2
import numpy as np

try:
    from src.controller.camera_segments import (
        Segment,
        camera_segments,
        csv_bounds,
        segment_at,
    )
except ImportError:  # run as a script: python3 src/controller/video_compose.py
    from camera_segments import Segment, camera_segments, csv_bounds, segment_at

_LOG = logging.getLogger(__name__)

DEFAULT_CANVAS_WIDTH = 1920
DEFAULT_FPS = 30


@dataclass
class CameraStream:
    name: str
    video_path: str          # first segment (dimensions, labels)
    csv_path: str
    # Every segment of this camera for the day, oldest first (see
    # camera_segments.py). Empty means the single video_path/csv_path pair.
    segments: list[Segment] = field(default_factory=list)

    def segment_list(self) -> list[Segment]:
        if self.segments:
            return self.segments
        bounds = csv_bounds(self.csv_path)
        if bounds is None:
            raise ValueError(f"{self.csv_path} has no timestamps")
        return [Segment(self.video_path, self.csv_path, *bounds)]


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
        segments = camera_segments(module_dir)
        if not segments:
            continue
        first = segments[0]
        streams.append(CameraStream(
            name=entry, video_path=first.video_path, csv_path=first.csv_path,
            segments=segments,
        ))
    return streams


class _StreamCursor:
    """Sequential, forward-only frame+timestamp reader for one camera.

    Advances by decoding frames in order (never seeks — .ts/MPEG-TS
    seeking via OpenCV is not reliably frame-accurate) and tracks which
    decoded frame is currently the best match for a requested wall-clock
    time.

    The naive mapping is decoded-frame `i` <-> CSV row `i`. That breaks
    on a libcamera sync *client* camera: its encoder discards frames until
    `SyncReady`, but the per-frame CSV logged a row for each of them, so
    the CSV runs ahead of the `.ts` and the client drifts behind the sync
    server in the composite (found 2026-09-07,
    plans/multicam-frame-alignment-and-sync-provenance.md). When
    `frame_count` is supplied and disagrees with the row count by more
    than a frame, map CSV rows onto decoded frames *proportionally*
    (assumes the drops are evenly spread — bounds the residual at ~half
    the deficit) and record the mismatch for the caller to surface.
    """

    def __init__(
        self, stream: CameraStream, skip: int | None = 0,
        frame_count: int | None = None,
    ):
        self.name = stream.name
        self.cap = cv2.VideoCapture(stream.video_path)
        with open(stream.csv_path, newline="") as f:
            ts = [int(row["timestamp_ns"]) for row in csv.DictReader(f)]
        if skip is None:
            # Same rule as compose._prestage_skip: a couple of leading rows
            # with no video frame; a bigger deficit is remapped below.
            skip = (min(_MAX_PRESTAGE_SKIP, max(0, len(ts) - frame_count))
                    if frame_count else 0)
        # Drop leading rows the CSV logged for frames captured before the
        # encoder started, so timestamps_ns[i] lines up with video frame i.
        self.timestamps_ns = ts[skip:] if 0 < skip < len(ts) else ts
        self.n_rows = len(self.timestamps_ns)
        self.n_frames = (
            frame_count if frame_count and frame_count > 0 else self.n_rows
        )
        # +ve: CSV has more rows than the .ts has frames (sync-client discards).
        self.deficit = self.n_rows - self.n_frames
        self._remap = (
            abs(self.deficit) > 1 and self.n_frames > 1 and self.n_rows > 1
        )
        self.row = 0        # CSV row pointer — drives the time lookup
        self.idx = -1       # decoded frame index
        self.frame = None
        self._advance()

    @property
    def mismatch_ms(self) -> float:
        """Worst-case residual alignment error this stream carries after
        the proportional remap (half the deficit, in ms at its own rate)."""
        if not self._remap or self.n_rows < 2:
            return 0.0
        span_s = (self.timestamps_ns[-1] - self.timestamps_ns[0]) / 1e9
        per_frame_ms = span_s * 1e3 / max(1, self.n_rows - 1)
        return abs(self.deficit) / 2.0 * per_frame_ms

    def _advance(self) -> bool:
        ok, frame = self.cap.read()
        if not ok:
            return False
        self.idx += 1
        self.frame = frame
        return True

    def _frame_for_row(self, row: int) -> int:
        if not self._remap:
            return row
        return round(row * (self.n_frames - 1) / (self.n_rows - 1))

    def sync_to(self, t_ns: int):
        """Walk the CSV row pointer to the row nearest `t_ns`, then decode
        forward to that row's mapped frame. Called with monotonically
        increasing `t_ns`, so both pointers only move forward."""
        while self.row + 1 < self.n_rows:
            if abs(self.timestamps_ns[self.row + 1] - t_ns) > abs(
                self.timestamps_ns[self.row] - t_ns
            ):
                break
            self.row += 1
        target = self._frame_for_row(self.row)
        while self.idx < target:
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


_MAX_PRESTAGE_SKIP = 2  # mirrors compose._MAX_PRESTAGE_SKIP

# Longer than any segment-rotation join (a frame or two) but short enough
# that a real recording gap shows as black rather than a frozen frame.
_SEGMENT_GAP_NS = 500_000_000


class _SegmentedCursor:
    """`_StreamCursor` across a camera's segments, opened one at a time as
    the requested time reaches them (so only one video is open per camera).
    Times past a segment's end hold its last frame across a rotation join and
    show black across a real gap (the module was offline or not recording).

    Frame/CSV mismatches are checked per segment as each opens; `messages`
    collects the warnings."""

    def __init__(self, stream: CameraStream, first_skip: int = 0):
        self.name = stream.name
        self.segments = stream.segment_list()
        self._first_skip = first_skip
        self._i = -1
        self._cur: _StreamCursor | None = None
        self.messages: list[str] = []
        self._open(0)

    def _open(self, i: int) -> None:
        if self._cur is not None:
            self._cur.release()
        seg = self.segments[i]
        single = CameraStream(self.name, seg.video_path, seg.csv_path)
        self._cur = _StreamCursor(
            single, self._first_skip if i == 0 else None,
            _probe_frame_count(seg.video_path),
        )
        self._i = i
        c = self._cur
        if c.deficit and abs(c.deficit) > 1:
            where = (f" segment {os.path.basename(seg.video_path)}"
                     if len(self.segments) > 1 else "")
            msg = (
                f"camera '{self.name}'{where}: {c.n_rows} CSV rows vs "
                f"{c.n_frames} .ts frames ({c.deficit:+d}); frames remapped "
                f"proportionally, up to ~{c.mismatch_ms:.0f} ms residual "
                f"alignment error"
            )
            _LOG.warning(msg)
            self.messages.append(msg)

    @property
    def first_ts(self) -> int:
        return self.segments[0].first_ns if self._i else self._cur.first_ts

    @property
    def last_ts(self) -> int:
        return self.segments[-1].last_ns

    def sync_to(self, t_ns: int):
        while (self._i + 1 < len(self.segments)
               and self.segments[self._i + 1].first_ns <= t_ns):
            self._open(self._i + 1)
        cur = self._cur
        if t_ns > cur.last_ts + _SEGMENT_GAP_NS and cur.frame is not None:
            return np.zeros_like(cur.frame)
        return cur.sync_to(t_ns)

    def release(self):
        if self._cur is not None:
            self._cur.release()


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
        # Some ffprobe builds (7.x, MPEG-TS) print the stream's csv row
        # twice -- once under `programs[].streams[]`, once under the
        # top-level `streams[]` -- with a blank line between. Both copies
        # carry the same value, so take the first non-empty line rather
        # than int()-ing the whole (multi-line) blob.
        lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
        return int(lines[0]) if lines else 0
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


def _label(frame: np.ndarray, text: str, scale: float = 1.0) -> np.ndarray:
    """A name label top-left; `scale` < 1 for a preview drawn smaller than
    the render, so the label keeps its size relative to the frame."""
    s = scale
    cv2.rectangle(frame, (0, 0), (round((14 * len(text) + 12) * s), round(34 * s)),
                  (0, 0, 0), -1)
    cv2.putText(frame, text, (round(6 * s), round(24 * s)), cv2.FONT_HERSHEY_SIMPLEX,
                0.8 * s, (255, 255, 255), max(1, round(2 * s)))
    return frame


def _fit_into(frame: np.ndarray, w: int, h: int,
              background: tuple[int, int, int] = (0, 0, 0)) -> np.ndarray:
    """Resize `frame` to fill (w, h) while preserving its aspect ratio,
    letterboxed onto `background`. Keeps a portrait pane from being
    squashed into a landscape box (and vice versa)."""
    src_h, src_w = frame.shape[:2]
    scale = min(w / src_w, h / src_h)
    new_w, new_h = max(1, int(src_w * scale)), max(1, int(src_h * scale))
    resized = cv2.resize(frame, (new_w, new_h))
    pane = np.empty((h, w, 3), dtype=np.uint8)
    pane[:] = background
    y0, x0 = (h - new_h) // 2, (w - new_w) // 2
    pane[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return pane


_ROTATE = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
           270: cv2.ROTATE_90_COUNTERCLOCKWISE}


def _crop_rotate(frame: np.ndarray, options: dict) -> np.ndarray:
    """A camera tile's crop (`[x, y, w, h]` as fractions of the frame), then
    its rotation (degrees clockwise)."""
    crop = options.get("crop")
    if crop:
        fh, fw = frame.shape[:2]
        x0 = min(fw - 1, int(round(crop[0] * fw)))
        y0 = min(fh - 1, int(round(crop[1] * fh)))
        x1 = max(x0 + 1, min(fw, int(round((crop[0] + crop[2]) * fw))))
        y1 = max(y0 + 1, min(fh, int(round((crop[1] + crop[3]) * fh))))
        frame = frame[y0:y1, x0:x1]
    rot = _ROTATE.get(options.get("rotate") or 0)
    return cv2.rotate(frame, rot) if rot is not None else frame


def camera_pane(frame: np.ndarray, w: int, h: int, options: dict,
                background: tuple[int, int, int] = (0, 0, 0)) -> np.ndarray:
    """One camera tile's pixels: crop, rotate, then `fit`: `contain`
    (whole frame, letterboxed onto the background), `cover` (fills the
    tile, edges cut off) or `stretch` (fills the tile, aspect ignored)."""
    src = _crop_rotate(frame, options)
    fit = options.get("fit", "contain")
    if fit == "stretch":
        return cv2.resize(src, (w, h))
    if fit == "cover":
        sh, sw = src.shape[:2]
        scale = max(w / sw, h / sh)
        nw, nh = max(w, math.ceil(sw * scale)), max(h, math.ceil(sh * scale))
        resized = cv2.resize(src, (nw, nh))
        x0, y0 = (nw - w) // 2, (nh - h) // 2
        return resized[y0:y0 + h, x0:x0 + w]
    return _fit_into(src, w, h, background)


_PANEL_BG = (0x14, 0x10, 0x10)          # BGR of the old ffmpeg 0x101014 backing
_PLAYHEAD = (0, 215, 255)               # BGR yellow


@dataclass
class _SpectrogramStrip:
    """One spectrogram tile's whole-window image, padded by one tile-window
    each side so the window can run off either end."""
    padded: np.ndarray
    pps: float          # strip pixels per second of output time
    win: int            # strip pixels the tile shows at once


def _load_strip(path: str, height: int, pps: float, win: int) -> _SpectrogramStrip:
    img = cv2.imread(path)
    if img is None:
        raise ValueError(f"could not read spectrogram image {path}")
    if img.shape[0] != height:
        img = cv2.resize(img, (img.shape[1], height))
    padded = np.empty((height, img.shape[1] + 2 * win, 3), dtype=np.uint8)
    padded[:] = _PANEL_BG
    padded[:, win:win + img.shape[1]] = img
    return _SpectrogramStrip(padded, pps, win)


def _draw_playhead(pane: np.ndarray, playhead: float) -> None:
    """The dashed 'now' line, `playhead` (0-1) of the way across the tile."""
    h, w = pane.shape[:2]
    x = max(0, min(w - 3, int(playhead * (w - 3))))
    for dy in range(0, h, 23):
        pane[dy:dy + 14, x:x + 3] = _PLAYHEAD


def spectrogram_pane(strip: _SpectrogramStrip, t_s: float, w: int, h: int,
                     playhead: float = 0.5) -> np.ndarray:
    """The spectrogram tile at output time `t_s`: the strip column for
    `t_s` sits `playhead` of the way across, under the dashed line. With
    playhead 0.5 and a tile-wide window this is the mapping the old
    full-width panel used (image x = width/2 - pps*t)."""
    x0 = int(round(strip.pps * t_s - playhead * strip.win)) + strip.win
    x0 = max(0, min(x0, strip.padded.shape[1] - strip.win))
    window = strip.padded[:, x0:x0 + strip.win]
    pane = cv2.resize(window, (w, h)) if window.shape[:2] != (h, w) else window.copy()
    _draw_playhead(pane, playhead)
    return pane


def _blank_pane(w: int, h: int, colour=_PANEL_BG) -> np.ndarray:
    pane = np.empty((h, w, 3), dtype=np.uint8)
    pane[:] = colour
    return pane


def _tile_order(tiles: list[dict]) -> list[dict]:
    """Bottom to top: by z, then list position (compose_layout.draw_order)."""
    return [t for _i, t in sorted(enumerate(tiles),
                                  key=lambda p: (p[1].get("z", 0), p[0]))]


def _finish_pane(pane: np.ndarray, tile: dict) -> np.ndarray:
    if tile.get("options", {}).get("label") and tile.get("label_text"):
        _label(pane, tile["label_text"], tile.get("label_scale", 1.0))
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
    warnings: list[str] | None = None,
) -> str:
    """Compose the session's cameras into one layout video (the CLI path:
    a camera-only layout, rendered by `render_layout`).

    `streams` restricts to those module folder names (order preserved).
    `regions` + `canvas` supply a pre-planned layout and, when given,
    override `layout`. `progress` is called `progress(done, total, stage)`
    every ~1 % of frames. `warnings` collects frame/CSV mismatch notes
    (see `render_layout`).
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

    tiles = [
        {"kind": "camera", "source": s.name, "label_text": s.name,
         "rect": list(r), "z": 0, "options": {"fit": "contain", "label": True}}
        for s, r in zip(ordered, regions, strict=True)
    ]
    return render_layout(date_dir, output_path, (canvas_w, canvas_h), tiles, fps,
                         csv_skip=csv_skip, progress=progress, warnings=warnings)


def render_layout(
    date_dir: str,
    output_path: str,
    canvas: tuple[int, int],
    tiles: list[dict],
    fps: int = DEFAULT_FPS,
    background: tuple[int, int, int] = (0, 0, 0),
    csv_skip: dict[str, int] | None = None,
    progress=None,
    warnings: list[str] | None = None,
) -> str:
    """Render a resolved layout (compose_layout.py, resolved by compose.py)
    over the window every camera tile's stream covers.

    Each tile is a dict with `kind`, `rect` [x, y, w, h], `z`, `options`
    and `label_text`. A camera tile names its module folder in `source`. A
    spectrogram tile carries `strip` (a wide chrome-free PNG of its aligned
    audio over the whole window, or None when there's no audio), `pps`
    (strip pixels per second) and `win` (strip pixels shown at once). Each
    wide PNG is loaded once and a tile-wide window copied per frame (an
    ffmpeg overlay of the whole image per frame took ~650 MB per panel on
    the controller). One decoder per camera however many tiles show it.

    If `warnings` is a list, one string per stream whose `.ts` frame count
    disagrees with its CSV row count (a sync-client discard skew) is
    appended to it; the composite is still produced, with the drops
    remapped proportionally.
    """
    found = {s.name: s for s in discover_camera_streams(date_dir)}
    names: list[str] = []
    for t in tiles:
        if t["kind"] == "camera" and t["source"] not in names:
            names.append(t["source"])
    missing = [n for n in names if n not in found]
    if missing:
        raise ValueError(f"streams not found under {date_dir}: {missing}")
    if not names:
        raise ValueError("a layout needs at least one camera tile")

    _skip = csv_skip or {}
    cursors = {n: _SegmentedCursor(found[n], _skip.get(n, 0)) for n in names}
    try:
        t_start = max(c.first_ts for c in cursors.values())
        t_end = min(c.last_ts for c in cursors.values())
        if t_end <= t_start:
            raise ValueError(
                "Camera streams in this session have no overlapping time window"
            )
        strips = {
            i: _load_strip(t["strip"], t["rect"][3], t["pps"], t["win"])
            for i, t in enumerate(tiles)
            if t["kind"] == "spectrogram" and t.get("strip")
        }
        order = [(i, t) for i, t in sorted(
            enumerate(tiles), key=lambda p: (p[1].get("z", 0), p[0]))]

        step_ns = int(1e9 / fps)
        n_out = int((t_end - t_start) / step_ns)
        canvas_w, canvas_h = canvas
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        writer = cv2.VideoWriter(output_path, cv2.VideoWriter_fourcc(*"mp4v"),
                                 fps, (canvas_w, canvas_h))
        if not writer.isOpened():
            raise RuntimeError("VideoWriter failed to open — codec unavailable")
        report_every = max(1, n_out // 100)
        try:
            for i in range(n_out):
                t_ns = t_start + i * step_ns
                frame = np.empty((canvas_h, canvas_w, 3), dtype=np.uint8)
                frame[:] = background
                # A camera shown in two tiles is synced once per frame.
                synced = {n: c.sync_to(t_ns) for n, c in cursors.items()}
                for idx, tile in order:
                    x, y, w, h = tile["rect"]
                    if tile["kind"] == "camera":
                        pane = camera_pane(synced[tile["source"]], w, h,
                                           tile["options"], background)
                    elif idx in strips:
                        pane = spectrogram_pane(strips[idx], i / fps, w, h,
                                                tile["options"].get("playhead", 0.5))
                    else:
                        pane = _blank_pane(w, h)
                    frame[y:y + h, x:x + w] = _finish_pane(pane, tile)
                writer.write(frame)
                if progress is not None and (i % report_every == 0 or i == n_out - 1):
                    progress(i + 1, n_out, "rendering")
        finally:
            writer.release()
    finally:
        for cursor in cursors.values():
            cursor.release()
        if warnings is not None:
            for cursor in cursors.values():
                warnings.extend(m for m in cursor.messages if m not in warnings)

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


def _stream_bounds(stream: CameraStream, skip: int = 0) -> tuple[int, int]:
    """(first, last) timestamp across all of a camera's segments."""
    segments = stream.segment_list()
    first = (_csv_bounds(segments[0].csv_path, skip)[0] if skip
             else segments[0].first_ns)
    return first, segments[-1].last_ns


def _representative_frame(stream: CameraStream, t_ns: int, skip: int = 0) -> np.ndarray:
    """A frame near `t_ns` for a single camera, for the layout preview.

    Seeks (CAP_PROP_POS_MSEC -> nearest keyframe, then a short decode)
    rather than walking `_StreamCursor` forward from frame 0: that decoded
    every frame up to mid-session -- tens of thousands on a long .ts, read
    over the share -- so a first preview could take minutes (field report,
    2026-10). The seek isn't frame-accurate on MPEG-TS, which is fine for a
    layout preview; real renders still use the cursor. Seeks within the
    segment covering `t_ns` (a seek into the first segment used to overshoot
    it on a multi-segment day and fall back to its first frame). Falls back
    to that segment's first frame if the seek reads nothing."""
    seg = segment_at(stream.segment_list(), t_ns)
    first_ns = seg.first_ns
    if seg.video_path == stream.video_path and skip:
        first_ns, _last_ns = _csv_bounds(seg.csv_path, skip)
    offset_ms = max(0.0, (min(t_ns, seg.last_ns) - first_ns) / 1e6)
    cap = cv2.VideoCapture(seg.video_path)
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, offset_ms)
        ok, frame = cap.read()
        if ok and frame is not None:
            return frame
    finally:
        cap.release()
    cap = cv2.VideoCapture(seg.video_path)
    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise ValueError(f"could not decode a frame from {seg.video_path}")
        return frame
    finally:
        cap.release()


def _stream_thumb(
    stream: CameraStream, t_ns: int, cache_dir: str | None, skip: int = 0
) -> np.ndarray:
    """A representative frame for `stream`, read from a cached PNG in
    `cache_dir` when present, otherwise decoded from the video and written
    there for next time. The cache makes repeated "Preview layout" presses
    cheap -- no video decode at all once every stream is cached."""
    if cache_dir:
        thumb_path = os.path.join(cache_dir, f"{stream.name}.seg.png")
        cached = cv2.imread(thumb_path) if os.path.isfile(thumb_path) else None
        if cached is not None:
            return cached
        frame = _representative_frame(stream, t_ns, skip)
        os.makedirs(cache_dir, exist_ok=True)
        cv2.imwrite(thumb_path, frame)
        return frame
    return _representative_frame(stream, t_ns, skip)


def preview_layout(
    date_dir: str,
    canvas: tuple[int, int],
    tiles: list[dict],
    cache_dir: str | None = None,
    csv_skip: dict[str, int] | None = None,
    background: tuple[int, int, int] = (0, 0, 0),
    at_fraction: float = 0.5,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """One composited frame of a resolved layout, at `at_fraction` through
    the cameras' overlap window, drawn with the same per-tile code as
    `render_layout`: `(frame, {tile_id: tile_pixels})`. The editor shows
    each tile's own pixels in its box, so moving one doesn't wait for a
    re-render.

    Camera frames come from one cached thumbnail per camera (`cache_dir`),
    so repeated previews skip video decode. A spectrogram tile carries
    `image`: a representative spectrogram PNG (or None), stretched to the
    tile under the playhead."""
    found = {s.name: s for s in discover_camera_streams(date_dir)}
    names: list[str] = []
    for t in tiles:
        if t["kind"] == "camera" and t["source"] in found and t["source"] not in names:
            names.append(t["source"])
    if not names:
        raise ValueError(f"No camera streams found under {date_dir}")

    _skip = csv_skip or {}
    bounds = [_stream_bounds(found[n], _skip.get(n, 0)) for n in names]
    t_start = max(b[0] for b in bounds)
    t_end = min(b[1] for b in bounds)
    t = t_start + int((t_end - t_start) * max(0.0, min(1.0, at_fraction)))
    thumbs = {n: _stream_thumb(found[n], t, cache_dir, _skip.get(n, 0)) for n in names}

    cw, ch = canvas
    frame = np.empty((ch, cw, 3), dtype=np.uint8)
    frame[:] = background
    panes: dict[str, np.ndarray] = {}
    for tile in _tile_order(tiles):
        x, y, w, h = tile["rect"]
        if tile["kind"] == "camera":
            thumb = thumbs.get(tile["source"])
            pane = (camera_pane(thumb, w, h, tile["options"], background)
                    if thumb is not None else _blank_pane(w, h, background))
        else:
            img = cv2.imread(tile["image"]) if tile.get("image") else None
            if img is None:
                pane = _blank_pane(w, h)
            else:
                pane = cv2.resize(img, (w, h))
                _draw_playhead(pane, tile["options"].get("playhead", 0.5))
        pane = _finish_pane(pane, tile)
        frame[y:y + h, x:x + w] = pane
        if "id" in tile:
            panes[tile["id"]] = pane
    return frame, panes


def encode_preview(frame: np.ndarray, panes: dict[str, np.ndarray],
                   jpeg_quality: int = 85) -> tuple[bytes, dict[str, bytes]]:
    """`preview_layout`'s output as bytes: the frame as PNG, each tile as
    JPEG (smaller over the socket; the editor shows them scaled anyway)."""
    ok, png = cv2.imencode(".png", frame)
    if not ok:
        raise RuntimeError("could not encode the preview frame")
    tiles = {}
    for tile_id, pane in panes.items():
        ok, jpg = cv2.imencode(".jpg", pane, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if ok:
            tiles[tile_id] = jpg.tobytes()
    return png.tobytes(), tiles


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
    # ffprobe; falls back to 0 (old behaviour) if it isn't on PATH. Capped at
    # a couple of frames -- a bigger CSV/`.ts` deficit is a sync-client discard
    # skew that _StreamCursor remaps proportionally (see its docstring).
    csv_skip = {}
    for s in discover_camera_streams(args.date_dir):
        try:
            with open(s.csv_path, newline="") as f:
                n_rows = sum(1 for _ in csv.DictReader(f))
            n_frames = _probe_frame_count(s.video_path)
            if n_frames:
                csv_skip[s.name] = min(2, max(0, n_rows - n_frames))
        except OSError:
            pass

    warnings: list[str] = []
    result = compose_session_video(
        args.date_dir, output, layout=args.layout, fps=args.fps,
        csv_skip=csv_skip, warnings=warnings,
    )
    for w in warnings:
        print(f"WARNING: {w}")
    print(f"Wrote {result}")


if __name__ == "__main__":
    main()
