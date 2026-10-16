"""
Session video composition jobs -- the backend for the frontend
"compose aggregated video" page.

A composite render is minutes of CPU + IO on a Pi 5 controller, so it
cannot run inside a socket handler. `ComposeWorker` is a single-slot
background worker: one job at a time, a small queue, and progress
callbacks the web layer forwards to the client. The artifact is written
into the session's own date directory so it shows up in the normal
file browser / download path with no new storage concept.

What a render looks like is a layout document (compose_layout.py): a
canvas and tiles, each a camera or an AudioMoth spectrogram placed in
output pixels, plus the AudioMoths muxed as audio tracks. The editor
sends one; an old-style request (a layout keyword -- `side | stack |
grid | auto` -- planned aspect-ratio-aware from each stream's real
dimensions, plus an audio mode) is translated into one
(`layout_from_legacy`), so both go through the same compositor
(video_compose.render_layout). Planning needs only ffprobe, so this
module imports neither OpenCV nor Flask at load time.
"""

from __future__ import annotations

import csv
import glob
import hashlib
import json
import math
import os
import queue
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from itertools import pairwise

from src.controller import audio_align, compose_layout
from src.controller.camera_segments import camera_segments

LAYOUTS = ("auto", "side", "stack", "grid", "loom")
# none  -> silent composite (default)
# track -> aligned audio muxed as an audio track
# strip -> scrolling spectrogram strip overlaid on the bottom of the video
# panel -> full scrolling spectrogram panel below the video (ethogram-style)
AUDIO_MODES = ("none", "track", "strip", "panel")
MAX_AUDIO_SOURCES = 8
DEFAULT_CANVAS_WIDTH = 1920
DEFAULT_FPS = 15
MAX_QUEUE = 4
# Spectrogram strip/panel height as a percentage of the composited video's
# height -- defaults per mode, and the accepted range.
# Panel defaults to the video's full height (strip overlays the video, so
# it stays small).
AUDIO_HEIGHT_PCT_DEFAULT = {"strip": 20.0, "panel": 100.0}
# Panel can exceed the video height: the height is shared between stacked
# panels (one per AudioMoth), so 200% gives two full-height panels.
AUDIO_HEIGHT_PCT_RANGE = {"strip": (5.0, 50.0), "panel": (10.0, 300.0)}


def panel_heights(total_px: int, n: int) -> list[int]:
    """Split the audio height across `n` stacked panels (even heights, at
    least 2 px each): the height setting sizes the whole audio area."""
    if n <= 0:
        return []
    each = max(2, total_px // n // 2 * 2)
    return [each] * n


def audio_height_px(video_height: int, height_pct: float) -> int:
    """Spectrogram strip/panel height in px: `height_pct` percent of the
    video's height, even (yuv420), at least 2. Shared by the preview and
    the real render so the preview shows what the render will produce."""
    return max(2, round(video_height * height_pct / 100) // 2 * 2)


class ComposeError(ValueError):
    """Bad request -- surfaced to the client verbatim."""


@dataclass
class AudioSpec:
    mode: str = "none"
    source: str | None = None                 # mic module folder; None -> first
    spectrogram: dict = field(default_factory=dict)  # -> audio_align.SpectrogramOpts
    height_pct: float | None = None           # strip/panel; None -> mode default
    # AudioMoths to use, as list_mic_devices() ids ("<folder>/<device>");
    # empty -> the first device of `source` (or of the first mic folder).
    # panel: one stacked spectrogram per device; track: one audio track per
    # device; strip: the first only (more would cover the picture).
    sources: list = field(default_factory=list)

    @property
    def effective_height_pct(self) -> float:
        return (self.height_pct if self.height_pct is not None
                else AUDIO_HEIGHT_PCT_DEFAULT.get(self.mode, 20.0))

    @classmethod
    def from_dict(cls, raw: dict | None) -> AudioSpec:
        if not raw:
            return cls()
        if not isinstance(raw, dict):
            raise ComposeError("audio must be an object")
        mode = str(raw.get("mode", "none")).lower()
        if mode not in AUDIO_MODES:
            raise ComposeError(f"audio.mode must be one of {', '.join(AUDIO_MODES)}")
        source = raw.get("source")
        if source is not None and not _safe_segment(str(source)):
            raise ComposeError("invalid audio.source")
        sources = raw.get("sources") or []
        if not isinstance(sources, list) or len(sources) > MAX_AUDIO_SOURCES:
            raise ComposeError(f"audio.sources must be a list of at most {MAX_AUDIO_SOURCES}")
        for sid in sources:
            folder, _, device = str(sid).partition("/")
            if not (_safe_segment(folder) and device and _safe_segment(device)):
                raise ComposeError(f"invalid audio source {sid!r}")
        spectrogram = raw.get("spectrogram") or {}
        if not isinstance(spectrogram, dict):
            raise ComposeError("audio.spectrogram must be an object")
        # Validate the spectrogram options now (SpectrogramOpts raises on bad
        # enum / range) so a bad request fails at submit, not mid-render.
        try:
            audio_align.SpectrogramOpts(**spectrogram)
        except TypeError as exc:
            raise ComposeError(f"unknown audio.spectrogram field: {exc}") from exc
        except ValueError as exc:
            raise ComposeError(str(exc)) from exc
        height_pct = raw.get("height_pct")
        if height_pct is not None and mode in AUDIO_HEIGHT_PCT_RANGE:
            try:
                height_pct = float(height_pct)
            except (TypeError, ValueError) as exc:
                raise ComposeError("audio.height_pct must be a number") from exc
            lo, hi = AUDIO_HEIGHT_PCT_RANGE[mode]
            if not lo <= height_pct <= hi:
                raise ComposeError(
                    f"audio.height_pct for {mode} must be between "
                    f"{lo:g} and {hi:g}")
        else:
            height_pct = None
        return cls(mode=mode, source=source, spectrogram=dict(spectrogram),
                   height_pct=height_pct, sources=[str(s) for s in sources])

    def spec_opts(self) -> audio_align.SpectrogramOpts:
        return audio_align.SpectrogramOpts(**self.spectrogram)


@dataclass
class ComposeSpec:
    session_name: str
    date_dir: str | None = None       # date-subdir name; None -> the latest one
    streams: list[str] | None = None  # module folder names; None -> every camera
    layout: str = "auto"
    fps: int = DEFAULT_FPS
    fmt: str = "mp4"
    audio: dict = field(default_factory=dict)   # -> AudioSpec
    # A layout document (compose_layout.py) from the editor. When set it
    # replaces `streams` / `layout` / `audio`, and `layout` reads "custom".
    layout_doc: dict | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> ComposeSpec:
        if not isinstance(raw, dict):
            raise ComposeError("compose spec must be an object")
        name = str(raw.get("session_name", "")).strip()
        if not _safe_name(name):
            raise ComposeError("invalid or missing session_name")
        layout_doc = raw.get("layout_doc")
        if layout_doc is not None:
            try:
                layout_doc = compose_layout.validate_layout(layout_doc)
            except compose_layout.LayoutError as exc:
                raise ComposeError(str(exc)) from exc
            layout = "custom"
        else:
            layout = str(raw.get("layout", "auto")).lower()
            if layout not in LAYOUTS:
                raise ComposeError(f"layout must be one of {', '.join(LAYOUTS)}")
        fmt = str(raw.get("fmt", "mp4")).lower()
        if fmt not in ("mp4", "mkv"):
            raise ComposeError("fmt must be mp4 or mkv")
        try:
            fps = int(raw.get("fps", DEFAULT_FPS))
        except (TypeError, ValueError) as exc:
            raise ComposeError("fps must be an integer") from exc
        if not 1 <= fps <= 60:
            raise ComposeError("fps must be between 1 and 60")
        streams = raw.get("streams")
        if streams is not None:
            if not isinstance(streams, list) or not all(
                isinstance(s, str) and _safe_segment(s) for s in streams
            ):
                raise ComposeError("streams must be a list of module folder names")
            streams = list(streams) or None
        date_dir = raw.get("date_dir")
        if date_dir is not None and not _safe_segment(str(date_dir)):
            raise ComposeError("invalid date_dir")
        audio = asdict(AudioSpec.from_dict(raw.get("audio")))
        if layout_doc is not None:
            streams, audio = None, asdict(AudioSpec())
        return cls(
            session_name=name, date_dir=date_dir, streams=streams,
            layout=layout, fps=fps, fmt=fmt, audio=audio, layout_doc=layout_doc,
        )


def _safe_name(text: str) -> bool:
    return bool(text) and all(c.isalnum() or c in "_-" for c in text)


def _safe_segment(text: str) -> bool:
    return bool(text) and "/" not in text and "\\" not in text and ".." not in text


# --------------------------------------------------------------------------- #
# Layout planning -- aspect-ratio aware, pure                                 #
# --------------------------------------------------------------------------- #

Region = tuple[int, int, int, int]  # x, y, w, h


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def plan_regions(
    dims: list[tuple[int, int]], layout: str,
    canvas_width: int = DEFAULT_CANVAS_WIDTH,
) -> tuple[list[Region], int, int]:
    """Given each stream's real `(w, h)`, return `(regions, canvas_w,
    canvas_h)` -- one `(x, y, w, h)` box per stream, every dimension even
    for codec safety. `side`/`stack` size each box to that stream's own
    aspect ratio; `grid`/`auto` use the median aspect for uniform cells
    (the compositor letterboxes each frame inside its box)."""
    n = len(dims)
    if n == 0:
        raise ComposeError("no streams to compose")
    aspects = [w / h if h else 16 / 9 for w, h in dims]
    median_aspect = sorted(aspects)[n // 2]

    if layout == "side" or (layout == "auto" and n == 2):
        box_h = _even(canvas_width / max(sum(aspects), 1e-6))
        box_h = min(box_h, 1080)
        regions, x = [], 0
        for a in aspects:
            bw = _even(box_h * a)
            regions.append((x, 0, bw, box_h))
            x += bw
        return regions, x, box_h

    if layout == "stack":
        box_w = _even(min(canvas_width, 1280))
        regions, y = [], 0
        for a in aspects:
            bh = _even(box_w / max(a, 1e-6))
            regions.append((0, y, box_w, bh))
            y += bh
        return regions, box_w, y

    # grid / auto (>2) / loom-fallthrough
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    cell_w = _even(canvas_width / cols)
    cell_h = _even(cell_w / max(median_aspect, 1e-6))
    regions = [
        ((i % cols) * cell_w, (i // cols) * cell_h, cell_w, cell_h)
        for i in range(n)
    ]
    return regions, cell_w * cols, cell_h * rows


# ffprobe results keyed by (path, size, mtime). A preview is requested on
# every debounced settings change, and `_video_frame_count` reads the whole
# .ts (-count_packets) -- uncached, every colour/gain tweak re-read every
# camera's video over the share. A finished recording doesn't change, so
# size+mtime is a sufficient key; bounded so a long-lived controller
# doesn't grow it forever.
_PROBE_CACHE: dict[tuple, object] = {}
_PROBE_CACHE_MAX = 256
_PROBE_CACHE_LOCK = threading.Lock()


def _cached_probe(kind: str, path: str, fn):
    try:
        st = os.stat(path)
        key = (kind, path, st.st_size, st.st_mtime_ns)
    except OSError:
        return fn()
    with _PROBE_CACHE_LOCK:
        if key in _PROBE_CACHE:
            return _PROBE_CACHE[key]
    value = fn()
    if value:  # never pin a failed/empty probe (e.g. a share hiccup)
        with _PROBE_CACHE_LOCK:
            if len(_PROBE_CACHE) >= _PROBE_CACHE_MAX:
                _PROBE_CACHE.pop(next(iter(_PROBE_CACHE)))
            _PROBE_CACHE[key] = value
    return value


def probe_dimensions(video_path: str) -> tuple[int, int]:
    """(width, height) of a video file's first stream, via ffprobe.
    Cached per file (see `_cached_probe`)."""
    return _cached_probe("dims", video_path,
                         lambda: _probe_dimensions_uncached(video_path))


def _probe_dimensions_uncached(video_path: str) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", video_path],
        capture_output=True, text=True, check=True,
    )
    s = json.loads(out.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


def _video_frame_count(video_path: str) -> int:
    """Exact frame count of a video's first stream (decoded packet count --
    the container's nb_frames is often absent/wrong for MPEG-TS). 0 if it
    can't be determined. Cached per file (see `_cached_probe`)."""
    return _cached_probe("frames", video_path,
                         lambda: _video_frame_count_uncached(video_path))


def _video_frame_count_uncached(video_path: str) -> int:
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
    except (subprocess.CalledProcessError, ValueError, OSError):
        return 0


# A genuine pre-stage skip is a couple of frames (the precallback logs rows
# for frames captured between CSV-open and start_encoder). A *larger*
# CSV/`.ts` deficit is a sync-client discard skew that is NOT all at the
# head (see plans/multicam-frame-alignment-and-sync-provenance.md): those
# drops are
# spread through the recording, so skipping them all as leading rows just
# moves the misalignment around. Cap the head skip here and let
# video_compose._StreamCursor remap the residual proportionally.
_MAX_PRESTAGE_SKIP = 2


def _prestage_skip(video_path: str, n_csv_rows: int) -> int:
    """Leading `*_timestamps.csv` rows to drop so `timestamp[i]` lines up
    with video frame `i`. Capped -- a bigger deficit is handled downstream
    (see `_MAX_PRESTAGE_SKIP`)."""
    n_frames = _video_frame_count(video_path)
    if not n_frames:
        return 0
    return min(_MAX_PRESTAGE_SKIP, max(0, n_csv_rows - n_frames))


# --------------------------------------------------------------------------- #
# Session layout on disk                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class SessionStream:
    name: str          # module folder name
    video_path: str    # first segment
    csv_path: str
    width: int
    height: int
    csv_skip: int = 0  # first segment's leading rows with no video frame
    # Every segment for the day, oldest first (camera_segments.py); empty
    # means just video_path/csv_path.
    segments: list = field(default_factory=list)


def resolve_date_dir(session_dir: str, date_dir: str | None) -> str:
    """Absolute path to the session's chosen date subdirectory (or the
    most recent one when unspecified)."""
    if date_dir:
        path = os.path.join(session_dir, date_dir)
        if not os.path.isdir(path):
            raise ComposeError(f"date_dir {date_dir!r} not found in session")
        return path
    subs = sorted(
        p for p in glob.glob(os.path.join(session_dir, "*")) if os.path.isdir(p)
    )
    if not subs:
        raise ComposeError("session has no date directory")
    return subs[-1]


def discover_streams(date_dir: str, wanted: list[str] | None) -> list[SessionStream]:
    """Every camera module folder (video + `*_timestamps.csv`) under the
    date dir, optionally filtered to `wanted`, with dimensions probed."""
    streams: list[SessionStream] = []
    for entry in sorted(os.listdir(date_dir)):
        if wanted is not None and entry not in wanted:
            continue
        module_dir = os.path.join(date_dir, entry)
        if not os.path.isdir(module_dir):
            continue
        segments = camera_segments(module_dir)
        if not segments:
            continue
        first = segments[0]
        w, h = probe_dimensions(first.video_path)
        try:
            with open(first.csv_path, newline="") as f:
                n_rows = sum(1 for _ in f) - 1  # minus header
        except OSError:
            n_rows = 0
        skip = _prestage_skip(first.video_path, n_rows) if n_rows > 0 else 0
        streams.append(SessionStream(
            entry, first.video_path, first.csv_path, w, h, skip, segments))
    if wanted:
        missing = [w for w in wanted if w not in {s.name for s in streams}]
        if missing:
            raise ComposeError(f"requested streams not found: {', '.join(missing)}")
    if not streams:
        raise ComposeError("no camera streams found for this session")
    return streams


def camera_window(streams: list[SessionStream]) -> tuple[int, int]:
    """`(t_start_ns, t_end_ns)` -- the wall-clock window every selected
    camera covers (overlap), from each stream's per-frame CSV. This is
    the window audio is aligned into, matching video_compose."""
    firsts, lasts = [], []
    for s in streams:
        rows = read_frame_timestamps(s.csv_path)[s.csv_skip:]  # drop pre-stage rows
        if rows:
            firsts.append(rows[0])
            # A multi-segment day runs to its last segment's last frame.
            lasts.append(s.segments[-1].last_ns if s.segments else rows[-1])
    if not firsts:
        raise ComposeError("camera CSVs have no timestamps")
    start, end = max(firsts), min(lasts)
    if end <= start:
        raise ComposeError("selected cameras have no overlapping time window")
    return start, end


def read_frame_timestamps(csv_path: str) -> list[int]:
    with open(csv_path, newline="") as f:
        return [int(row["timestamp_ns"]) for row in csv.DictReader(f)]


def stream_fps(csv_path: str) -> float | None:
    """A camera's real capture rate from the median gap between its
    per-frame timestamps (nominal fps drifts from real -- see CLAUDE.md
    Hardware gotchas). None if the CSV is too short to tell."""
    ts = read_frame_timestamps(csv_path)
    if len(ts) < 3:
        return None
    gaps = [b - a for a, b in pairwise(ts) if b > a]
    if not gaps:
        return None
    median_gap_ns = statistics.median(gaps)
    return 1e9 / median_gap_ns if median_gap_ns else None


def suggest_fps(streams: list[SessionStream], lo: int = 1, hi: int = 60) -> int:
    """Default output fps for a compose: the fastest real capture rate
    among the selected cameras (so no camera is temporally down-sampled),
    rounded and clamped. Falls back to DEFAULT_FPS if no CSV is usable."""
    rates = [r for s in streams if (r := stream_fps(s.csv_path)) is not None]
    if not rates:
        return DEFAULT_FPS
    return max(lo, min(hi, round(max(rates))))


def list_mic_folders(date_dir: str) -> list[str]:
    """Module folder names under `date_dir` that hold a mic recording
    (an audio file + a `*_timestamps.txt` sidecar)."""
    out = []
    for entry in sorted(os.listdir(date_dir)):
        mdir = os.path.join(date_dir, entry)
        if not os.path.isdir(mdir):
            continue
        has_audio = any(
            glob.glob(os.path.join(mdir, f"*{ext}")) for ext in (".flac", ".wav")
        )
        has_sidecar = bool(glob.glob(os.path.join(mdir, "*_timestamps.txt")))
        if has_audio and has_sidecar:
            out.append(entry)
    return out


def streams_info(share_path: str, session_name: str,
                 date_dir: str | None = None,
                 streams: list[str] | None = None) -> dict:
    """Lightweight inventory for the compose UI -- cameras (with real
    dimensions + capture rate), mic folders, available recording days, and
    the fps a render should default to. No video decode, no full render.
    `streams`, if given, scopes `suggested_fps` to just those cameras."""
    if not _safe_name(session_name):
        raise ComposeError("invalid session_name")
    session_dir = os.path.join(share_path, session_name)
    if not os.path.isdir(session_dir):
        raise ComposeError("session directory not found")
    dates = sorted(
        os.path.basename(p)
        for p in glob.glob(os.path.join(session_dir, "*"))
        if os.path.isdir(p) and not os.path.basename(p).startswith(".")
    )
    resolved = resolve_date_dir(session_dir, date_dir)
    cams = discover_streams(resolved, None)
    wanted = [s for s in cams if not streams or s.name in streams] or cams
    try:
        t0, t1 = camera_window(wanted)
        duration_s = round((t1 - t0) / 1e9, 3)
    except (ComposeError, OSError, ValueError, KeyError):
        t0 = t1 = duration_s = None
    return {
        "session_name": session_name,
        "date_dir": os.path.basename(resolved),
        "dates": dates,
        "cameras": [
            {
                "name": s.name, "width": s.width, "height": s.height,
                "fps": round(r, 2) if (r := stream_fps(s.csv_path)) else None,
                # Each segment's [first, last] frame time (ns), for the
                # timeline's coverage bars.
                "segments": [[g.first_ns, g.last_ns] for g in s.segments],
            }
            for s in cams
        ],
        "mics": list_mic_folders(resolved),
        "mic_devices": list_mic_devices(resolved),
        "suggested_fps": suggest_fps(wanted),
        # The selected cameras' overlap -- what a render covers at most.
        "duration_s": duration_s,
        "window_start_ns": t0,
        "window_end_ns": t1,
        # Lets the editor size an "auto" spectrogram window like a render.
        "playhead_pps": audio_align.PLAYHEAD_PPS,
        "max_strip_px": audio_align._MAX_STRIP_PX,
    }


def _preview_cache_dir(session_name: str, date_base: str) -> str:
    """Controller-local scratch dir for cached preview thumbnails -- kept
    off the (possibly remote) export share so a preview never writes there,
    and out of the session tree so it never shows in the file browser or a
    download zip."""
    path = os.path.join(
        tempfile.gettempdir(), "saviour_compose_cache", session_name, date_base
    )
    os.makedirs(path, exist_ok=True)
    return path


def clear_preview_cache(session_name: str, date_base: str | None = None) -> None:
    root = os.path.join(tempfile.gettempdir(), "saviour_compose_cache", session_name)
    target = os.path.join(root, date_base) if date_base else root
    shutil.rmtree(target, ignore_errors=True)


_SEGMENT_TAG = re.compile(r"_\(\d+_[0-9-]+\)")


def _mic_device_key(audio_path: str) -> str:
    """One AudioMoth's files share a name apart from the `(index_start)`
    segment tag (and a `_PARTIAL` salvage suffix); a mic folder holds every
    connected AudioMoth's files side by side."""
    stem = os.path.splitext(os.path.basename(audio_path))[0]
    return _SEGMENT_TAG.sub("", stem).removesuffix("_PARTIAL")


def list_mic_devices(date_dir: str) -> list[dict]:
    """Every AudioMoth recorded that day, across every mic folder:
    `{"id": "<folder>/<device>", "folder", "device", "label"}`. A mic module
    can carry several AudioMoths, each with its own files in the folder."""
    out = []
    for folder in list_mic_folders(date_dir):
        mdir = os.path.join(date_dir, folder)
        keys = {}
        for ext in (".flac", ".wav"):
            for path in sorted(glob.glob(os.path.join(mdir, f"*{ext}"))):
                if os.path.basename(path).startswith("PENDING_"):
                    continue
                if os.path.isfile(_mic_sidecar_for(path)):
                    keys.setdefault(_mic_device_key(path), None)
        for key in keys:
            # The key is "<session>_<module name>_<id>_<AudioMoth serial>";
            # the serial's tail is enough to tell a module's devices apart.
            serial = key.rsplit("_", 1)[-1]
            out.append({"id": f"{folder}/{key}", "folder": folder, "device": key,
                        "label": f"{folder} · {serial[-4:] if len(serial) > 4 else serial}"})
    return out


def resolve_mic_devices(date_dir: str, audio: AudioSpec) -> list[dict]:
    """The devices a render/preview uses: `audio.sources` in order, else the
    first device of `audio.source` (or of the first mic folder)."""
    devices = list_mic_devices(date_dir)
    if audio.sources:
        by_id = {d["id"]: d for d in devices}
        missing = [s for s in audio.sources if s not in by_id]
        if missing:
            raise ComposeError(f"microphone not found: {', '.join(missing)}")
        return [by_id[s] for s in audio.sources]
    pool = [d for d in devices if audio.source in (None, d["folder"])]
    if not pool:
        raise ComposeError(
            f"no microphone recording found{f' for {audio.source}' if audio.source else ''}")
    return pool[:1]


def find_microphone_segments(date_dir: str, source: str | None,
                             device: str | None = None) -> list[tuple[str, str]]:
    """Every (audio_path, sidecar_path) segment of one AudioMoth: `device`
    (a list_mic_devices() device key) in the `source` folder, else that
    folder's (or the first mic folder's) first device by name, the same
    device `find_microphone` picks. Skips empty and mid-export files."""
    audio, _sidecar = find_microphone(date_dir, source)
    mdir = os.path.dirname(audio)
    key = device or _mic_device_key(audio)
    out = []
    for ext in (".flac", ".wav"):
        for path in sorted(glob.glob(os.path.join(mdir, f"*{ext}"))):
            name = os.path.basename(path)
            if name.startswith("PENDING_") or _mic_device_key(path) != key:
                continue
            sidecar = _mic_sidecar_for(path)
            try:
                if os.path.getsize(path) > 0 and os.path.isfile(sidecar):
                    out.append((path, sidecar))
            except OSError:
                continue
    return out


def _mic_sidecar_for(audio_path: str) -> str:
    stem = os.path.splitext(audio_path)[0]
    if stem.endswith("_PARTIAL"):
        return stem.removesuffix("_PARTIAL") + "_timestamps_PARTIAL.txt"
    return stem + "_timestamps.txt"


def find_microphone(date_dir: str, source: str | None):
    """The chosen mic's (audio_path, sidecar_path), or the first mic
    folder with an audio file + a `*_timestamps.txt` sidecar."""
    for entry in sorted(os.listdir(date_dir)):
        if source is not None and entry != source:
            continue
        mdir = os.path.join(date_dir, entry)
        if not os.path.isdir(mdir):
            continue
        for ext in (".flac", ".wav"):
            for audio in sorted(glob.glob(os.path.join(mdir, f"*{ext}"))):
                if os.path.basename(audio).startswith("PENDING_"):
                    continue
                sidecar = _mic_sidecar_for(audio)
                if os.path.isfile(sidecar):
                    return audio, sidecar
    raise ComposeError(
        f"no microphone recording found{f' for {source}' if source else ''}"
    )


# Bounds the preview's ffmpeg spectrogram call: a stuck ffmpeg used to leave
# the preview spinning forever with no error.
PREVIEW_FFMPEG_TIMEOUT_S = 30
# A spectrogram tile's preview shows this much audio when its window is
# "auto" and the session length can't be read.
PREVIEW_SPECTROGRAM_S = 20.0


# --------------------------------------------------------------------------- #
# Layouts: legacy translation, templates, resolving against a session         #
# --------------------------------------------------------------------------- #


def _spectrogram_options(audio: AudioSpec) -> dict:
    opts = asdict(audio.spec_opts())
    return {**opts, "window_s": None, "playhead": 0.5}


def layout_from_legacy(spec: ComposeSpec, streams: list[SessionStream],
                       devices: list[dict],
                       canvas_width: int = DEFAULT_CANVAS_WIDTH) -> dict:
    """The layout document an old-style request (a `layout` keyword plus an
    `audio` mode) describes, so old callers and saved jobs render through
    the same compositor as the editor. `devices` are the AudioMoths the
    request resolves to (resolve_mic_devices); `strip` uses the first.

    Reproduces the old placement exactly: cameras where plan_regions puts
    them; a `strip` over the bottom of the video; `panel`s stacked under
    it, labelled when there's more than one; one spectrogram window per
    tile pixel (`window_s` None)."""
    regions, cw, ch = plan_regions(
        [(s.width, s.height) for s in streams], spec.layout, canvas_width)
    tiles = [
        {"id": f"c{i + 1}", "kind": "camera", "source": s.name, "rect": list(r),
         "z": 0, "options": {"fit": "contain", "label": True}}
        for i, (s, r) in enumerate(zip(streams, regions, strict=True))
    ]
    audio = AudioSpec(**spec.audio)
    canvas_h = ch
    tracks: list[str] = []
    if audio.mode in ("strip", "panel") and devices:
        devs = devices[:1] if audio.mode == "strip" else devices
        heights = panel_heights(audio_height_px(ch, audio.effective_height_pct),
                                len(devs))
        opts = _spectrogram_options(audio)
        if audio.mode == "strip":
            h = min(heights[0], ch)
            tiles.append({"id": "a1", "kind": "spectrogram", "source": devs[0]["id"],
                          "rect": [0, ch - h, cw, h], "z": 1,
                          "options": {**opts, "label": False}})
        else:
            y = ch
            for i, (dev, h) in enumerate(zip(devs, heights, strict=True)):
                tiles.append({"id": f"a{i + 1}", "kind": "spectrogram",
                              "source": dev["id"], "rect": [0, y, cw, h], "z": 0,
                              "options": {**opts, "label": len(devs) > 1}})
                y += h
            canvas_h = y
        tracks = [d["id"] for d in devs]
    elif audio.mode == "track":
        tracks = [d["id"] for d in devices]
    try:
        return compose_layout.validate_layout({
            "canvas": {"width": cw, "height": canvas_h, "background": "#000000"},
            "tiles": tiles, "audio_tracks": tracks, "range": None,
        })
    except compose_layout.LayoutError as exc:
        raise ComposeError(str(exc)) from exc


@dataclass
class ResolvedLayout:
    """A layout bound to one session day: its document, the camera streams
    it shows and the AudioMoths it uses (list_mic_devices entries by id)."""
    date_dir: str
    doc: dict
    streams: list[SessionStream]
    devices: dict[str, dict]

    def stream(self, name: str) -> SessionStream:
        return next(s for s in self.streams if s.name == name)

    def window(self) -> tuple[int, int]:
        """The stretch a render covers: the cameras' overlap, narrowed to
        the layout's range."""
        try:
            return compose_layout.clip_range(
                self.doc.get("range") or {}, camera_window(self.streams))
        except compose_layout.LayoutError as exc:
            raise ComposeError(str(exc)) from exc

    @property
    def speed(self) -> float:
        return float((self.doc.get("range") or {}).get("speed") or 1.0)


def resolve_layout(date_dir: str, spec: ComposeSpec, strict_audio: bool = True,
                   logger=None) -> ResolvedLayout:
    """`spec`'s layout document (translated from the legacy fields if it
    has none) with every source checked against what `date_dir` holds.
    `strict_audio=False` (the preview) drops a missing microphone with a
    warning instead of failing."""
    if spec.layout_doc:
        doc = spec.layout_doc
        streams = discover_streams(date_dir, compose_layout.camera_sources(doc))
        by_id = {d["id"]: d for d in list_mic_devices(date_dir)}
        missing = [m for m in compose_layout.mic_sources(doc) if m not in by_id]
        if missing:
            raise ComposeError(f"microphone not found: {', '.join(missing)}")
        devices = {m: by_id[m] for m in compose_layout.mic_sources(doc)}
        return ResolvedLayout(date_dir, doc, streams, devices)

    streams = discover_streams(date_dir, spec.streams)
    audio = AudioSpec(**spec.audio)
    devices: list[dict] = []
    if audio.mode != "none":
        try:
            devices = resolve_mic_devices(date_dir, audio)
        except ComposeError as exc:
            if strict_audio:
                raise
            if logger:
                logger.warning("compose preview: skipping audio (%s)", exc)
    doc = layout_from_legacy(spec, streams, devices)
    return ResolvedLayout(date_dir, doc, streams, {d["id"]: d for d in devices})


def layout_template(share_path: str, session_name: str, date_dir: str | None,
                    template: str = "auto", audio_mode: str = "none",
                    streams: list[str] | None = None, mics: list[str] | None = None,
                    spectrogram: dict | None = None,
                    canvas: dict | None = None) -> dict:
    """A built-in starting layout for the editor: one of the old layout
    keywords (`auto | side | stack | grid`) with an old audio arrangement
    (`none | track | strip | panel`) over the chosen cameras and AudioMoths,
    as a layout document. With `canvas` ({width, height}) the arrangement
    is scaled to fit that canvas and centred on it; without, the canvas is
    the arrangement's natural size."""
    raw = {"session_name": session_name, "date_dir": date_dir, "layout": template,
           "streams": streams or None,
           "audio": {"mode": audio_mode, "sources": mics or [],
                     "spectrogram": spectrogram or {}}}
    spec = ComposeSpec.from_dict(raw)
    session_dir = os.path.join(share_path, spec.session_name)
    if not os.path.isdir(session_dir):
        raise ComposeError("session directory not found")
    resolved = resolve_layout(resolve_date_dir(session_dir, spec.date_dir), spec,
                              strict_audio=False)
    doc = resolved.doc
    if canvas:
        try:
            target = compose_layout.validate_canvas(
                {"background": doc["canvas"]["background"], **canvas})
        except compose_layout.LayoutError as exc:
            raise ComposeError(str(exc)) from exc
        doc = compose_layout.fit_to_canvas(doc, target["width"], target["height"])
        doc["canvas"]["background"] = target["background"]
    return doc


def _tile_label(resolved: ResolvedLayout, tile: dict) -> str:
    if tile["kind"] == "camera":
        return tile["source"]
    dev = resolved.devices.get(tile["source"])
    return dev["label"] if dev else tile["source"]


def _preview_tile_spectrogram(cache_dir: str, date_dir: str, device: dict,
                              tile: dict, width: int, height: int, dur_s: float,
                              logger=None) -> str | None:
    """A representative (unaligned) spectrogram PNG for one spectrogram
    tile's preview, `dur_s` of the device's first recording, cached by file
    + options + size so it's rendered once per settings combination.
    Returns None (the tile previews blank) if ffmpeg or the recording isn't
    usable -- a preview must not fail over audio."""
    try:
        segs = find_microphone_segments(date_dir, device["folder"], device["device"])
        if not segs:
            raise ComposeError(f"no recording for {device['label']}")
        audio_file = segs[0][0]
        try:
            mtime = int(os.path.getmtime(audio_file))
        except OSError:
            mtime = 0
        opts = compose_layout.spectrogram_opts(tile)
        key = hashlib.md5(
            f"{audio_file}|{mtime}|{width}x{height}|{dur_s:.2f}|nolegend|"
            f"{json.dumps(asdict(opts), sort_keys=True)}".encode()
        ).hexdigest()[:12]
        out = os.path.join(cache_dir, f"spec_{key}.png")
        if not os.path.isfile(out):
            audio_align.render_source_spectrogram_png(
                audio_file, out, size=(max(2, width), max(2, height)), spec=opts,
                dur_s=dur_s, timeout=PREVIEW_FFMPEG_TIMEOUT_S, legend=False,
            )
        return out
    except Exception as exc:  # noqa: BLE001 -- preview must not fail over audio
        if logger:
            logger.warning("compose preview: skipping spectrogram (%s)", exc)
        return None


_FIT_CACHE: dict[tuple, object] = {}
_FIT_CACHE_LOCK = threading.Lock()


def _mic_fit(audio_file: str, sidecar: str):
    """parse_mic_sidecar, cached per (sidecar, size, mtime): every scrubbed
    preview needs the fits of the segments near the previewed time."""
    try:
        st = os.stat(sidecar)
        key = (sidecar, st.st_size, st.st_mtime_ns)
    except OSError:
        return audio_align.parse_mic_sidecar(sidecar, audio_file)
    with _FIT_CACHE_LOCK:
        if key in _FIT_CACHE:
            return _FIT_CACHE[key]
    fit = audio_align.parse_mic_sidecar(sidecar, audio_file)
    with _FIT_CACHE_LOCK:
        if len(_FIT_CACHE) >= 256:
            _FIT_CACHE.pop(next(iter(_FIT_CACHE)))
        _FIT_CACHE[key] = fit
    return fit


def _preview_window_spectrogram(cache_dir: str, date_dir: str, device: dict,
                                tile: dict, width: int, height: int,
                                t0_ns: int, dur_s: float, logger=None) -> str | None:
    """The spectrogram a render's tile shows around the previewed time:
    `dur_s` of aligned audio from wall-clock `t0_ns`, `width` x `height`.
    None if this device has no recording there or it can't be read (the
    tile previews blank); never fails the preview."""
    try:
        parts = []
        t1_ns = t0_ns + int(dur_s * 1e9)
        for audio_file, sidecar in find_microphone_segments(
                date_dir, device["folder"], device["device"]):
            fit = _mic_fit(audio_file, sidecar)
            seg_end = fit.sample0_wall_ns + int(fit.duration_s * 1e9)
            if seg_end > t0_ns and fit.sample0_wall_ns < t1_ns:
                parts.append((audio_align.AudioStream("mic", audio_file, sidecar), fit))
        if not parts:
            return None
        opts = compose_layout.spectrogram_opts(tile)
        key = hashlib.md5(
            f"{[p[0].audio_path for p in parts]}|{t0_ns // 1_000_000}|{dur_s:.3f}|"
            f"{width}x{height}|{json.dumps(asdict(opts), sort_keys=True)}".encode()
        ).hexdigest()[:12]
        out = os.path.join(cache_dir, f"specw_{key}.png")
        if not os.path.isfile(out):
            audio_align.render_window_spectrogram_png(
                parts, t0_ns, dur_s, out, (max(2, width), max(2, height)), opts,
                timeout=PREVIEW_FFMPEG_TIMEOUT_S)
        return out
    except Exception as exc:  # noqa: BLE001 -- preview must not fail over audio
        if logger:
            logger.warning("compose preview: spectrogram at the previewed time "
                           "unavailable (%s)", exc)
        return None


@dataclass
class PreviewResult:
    png: bytes                       # the whole composited frame
    tiles: dict[str, bytes]          # tile id -> that tile's pixels (JPEG)
    scale: float                     # preview px per output px
    doc: dict                        # the layout previewed (full size)
    t_ns: int | None = None          # the wall-clock time previewed


def render_preview(share_path: str, spec: ComposeSpec, max_width: int = 960,
                   rebuild: bool = False, logger=None,
                   at_ns: int | None = None) -> PreviewResult:
    """One composited frame of `spec`'s layout at wall-clock `at_ns`
    (clamped to the recording; default the middle of the range), drawn by the
    same per-tile code as a render, at most `max_width` wide: the camera
    frames a render shows at that time, and each spectrogram tile's window
    of aligned audio around it (falling back to a stretch from the start of
    the recording if that can't be read). Frames are cached per camera and
    time (cleared on `rebuild=True`). Also returns each tile's own pixels,
    for the editor."""
    from src.controller import video_compose

    session_dir = os.path.join(share_path, spec.session_name)
    if not os.path.isdir(session_dir):
        raise ComposeError("session directory not found")
    date_dir = resolve_date_dir(session_dir, spec.date_dir)
    date_base = os.path.basename(date_dir)
    if rebuild:
        clear_preview_cache(spec.session_name, date_base)
    cache_dir = _preview_cache_dir(spec.session_name, date_base)

    resolved = resolve_layout(date_dir, spec, strict_audio=False, logger=logger)
    small, scale = compose_layout.scaled(resolved.doc, max_width)
    try:
        t0, t1 = resolved.window()
        total_s = (t1 - t0) / 1e9
        # Scrubbing can show any moment of the recording (that's how the
        # range is chosen), so clamp to the cameras' overlap, not the range.
        w0, w1 = camera_window(resolved.streams)
        t = t0 + (t1 - t0) // 2 if at_ns is None else max(w0, min(w1, int(at_ns)))
    except ComposeError:
        total_s, t = 0.0, None

    tiles = []
    for full, tile in zip(resolved.doc["tiles"], small["tiles"], strict=True):
        tile = {**tile, "label_text": _tile_label(resolved, tile), "label_scale": scale}
        if tile["kind"] == "spectrogram":
            device = resolved.devices.get(tile["source"])
            window_s = tile["options"].get("window_s")
            if window_s is None:
                _w, pps = audio_align._strip_pixels_per_second(total_s or 1.0)
                window_s = full["rect"][2] / pps if total_s else PREVIEW_SPECTROGRAM_S
            window_s = max(0.5, min(window_s, 120.0))
            w, h = tile["rect"][2], tile["rect"][3]
            image = None
            if device and t is not None:
                playhead = tile["options"].get("playhead", 0.5)
                image = _preview_window_spectrogram(
                    cache_dir, date_dir, device, tile, w, h,
                    t - int(playhead * window_s * 1e9), window_s, logger)
            if device and image is None:
                image = _preview_tile_spectrogram(
                    cache_dir, date_dir, device, tile, w, h, window_s, logger)
            tile["image"] = image
        tiles.append(tile)

    frame, panes = video_compose.preview_layout(
        date_dir, (small["canvas"]["width"], small["canvas"]["height"]), tiles,
        cache_dir=cache_dir, csv_skip={s.name: s.csv_skip for s in resolved.streams},
        background=compose_layout.hex_to_bgr(small["canvas"]["background"]),
        at_ns=t,
    )
    png, tile_jpegs = video_compose.encode_preview(frame, panes)
    return PreviewResult(png, tile_jpegs, scale, resolved.doc, t)


class PreviewQueue:
    """One background thread rendering previews, keeping only each client's
    *latest* request.

    The panel asks for a preview on every debounced settings change. One
    thread per request (the old handler) let a burst of colour/gain tweaks
    run several first-time decodes at once, all competing for the same CPU
    and share, so none finished. Here a client's newer request replaces its
    queued one (a running render isn't interrupted, but at most one
    follow-up runs), and clients are served in arrival order so one busy
    client can't starve another.

    `render(spec_dict)` returns the reply payload; `deliver(client, payload)`
    sends it. Both are injected so this has no Flask/OpenCV dependency."""

    def __init__(self, render, deliver, logger=None, idle_timeout_s: float = 60):
        self._render = render
        self._deliver = deliver
        self._log = logger
        self._idle_timeout_s = idle_timeout_s
        self._pending: dict = {}          # client -> spec_dict (insertion-ordered)
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None

    def submit(self, client, spec_dict: dict) -> None:
        with self._cv:
            self._pending.pop(client, None)   # re-queue at the back
            self._pending[client] = spec_dict
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, daemon=True, name="compose-preview")
                self._thread.start()
            self._cv.notify()

    def _run(self) -> None:
        _lower_thread_priority()
        while True:
            with self._cv:
                if not self._cv.wait_for(lambda: self._pending,
                                         timeout=self._idle_timeout_s):
                    self._thread = None       # idle: let the thread exit
                    return
                client = next(iter(self._pending))
                spec_dict = self._pending.pop(client)
            try:
                payload = self._render(spec_dict)
            except Exception as exc:  # noqa: BLE001 -- report, never kill the loop
                if self._log:
                    self._log.exception("compose preview failed")
                payload = {"error": f"internal error: {exc}"}
            request_id = (spec_dict or {}).get("request_id")
            if request_id is not None:
                payload = {**payload, "request_id": request_id}
            try:
                self._deliver(client, payload)
            except Exception:  # noqa: BLE001
                if self._log:
                    self._log.exception("compose preview delivery failed")


# --------------------------------------------------------------------------- #
# Jobs + worker                                                               #
# --------------------------------------------------------------------------- #


@dataclass
class ComposeJob:
    id: str
    spec: dict
    state: str = "queued"          # queued | running | done | error | cancelled
    progress: float = 0.0          # 0..1
    stage: str = "queued"
    output_rel: str | None = None  # path under the session dir, for download
    error: str | None = None
    # Non-fatal alignment caveats (e.g. a sync-client camera's .ts/CSV
    # frame-count mismatch) surfaced to the operator alongside the result.
    warnings: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    output_bytes: int | None = None

    def summary(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> ComposeJob:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


# Render history (ComposeWorker): kept across restarts like the session list,
# pruned to the newest JOBS_KEEP, and anything finished more than
# JOBS_MAX_AGE_S ago. Pruning drops records only; the videos stay on the share.
JOBS_FILE = "/var/lib/saviour/controller/compose_jobs.json"
JOBS_KEEP = 100
JOBS_MAX_AGE_S = 90 * 24 * 3600
_ACTIVE_STATES = ("queued", "running")


def _aligned_mic_parts(segments: list[tuple[str, str]], t_start: int, t_end: int,
                       warnings: list | None = None, logger=None) -> list:
    """(AudioStream, SidecarFit) for each mic segment that overlaps the
    video window, oldest first. A segment whose sidecar can't be fitted (a
    salvaged fragment, say) is skipped with a warning rather than failing
    the render."""
    parts = []
    for audio_file, sidecar in segments:
        try:
            fit = audio_align.parse_mic_sidecar(sidecar, audio_file)
        except Exception as exc:  # noqa: BLE001 -- one bad segment mustn't sink the render
            msg = f"audio segment {os.path.basename(audio_file)} skipped: {exc}"
            if logger:
                logger.warning(msg)
            if warnings is not None:
                warnings.append(msg)
            continue
        seg_end = fit.sample0_wall_ns + int(fit.duration_s * 1e9)
        if seg_end <= t_start or fit.sample0_wall_ns >= t_end:
            continue
        parts.append((audio_align.AudioStream("mic", audio_file, sidecar), fit))
    parts.sort(key=lambda p: p[1].sample0_wall_ns)
    return parts


class ComposeWorker:
    """One render at a time, small FIFO queue, progress via `on_update`."""

    def __init__(
        self, share_path: str,
        busy_check=None,        # () -> str|None : reason a render is disallowed now
        on_update=None,         # (job_summary: dict) -> None
        logger=None,
        jobs_file: str | None = None,  # history file; None = in memory only
    ):
        self.share_path = share_path
        self._busy_check = busy_check or (lambda: None)
        self._on_update = on_update or (lambda _summary: None)
        self._log = logger
        self._jobs: dict[str, ComposeJob] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._jobs_file = jobs_file
        self._last_save = 0.0
        self._load()
        self._thread = threading.Thread(
            target=self._run_loop, name="compose-worker", daemon=True
        )
        self._thread.start()

    # -- public ----------------------------------------------------------- #

    def submit(self, spec: ComposeSpec) -> ComposeJob:
        reason = self._busy_check()
        if reason:
            raise ComposeError(reason)
        with self._lock:
            pending = [j for j in self._jobs.values()
                       if j.state in ("queued", "running")]
            if len(pending) >= MAX_QUEUE:
                raise ComposeError("compose queue is full, try again shortly")
            job = ComposeJob(id=uuid.uuid4().hex[:12], spec=asdict(spec))
            self._jobs[job.id] = job
        self._queue.put(job.id)
        self._emit(job)
        return job

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state not in ("queued", "running"):
                return False
            # A running render isn't force-killed; it's marked so the
            # progress callback stops emitting and the result is dropped.
            job.state = "cancelled"
            job.stage = "cancelled"
            job.finished_at = time.time()
        self._emit(job)
        return True

    def get(self, job_id: str) -> ComposeJob | None:
        return self._jobs.get(job_id)

    def delete(self, job_id: str, delete_file: bool = False) -> None:
        """Remove a finished job from the history, and its video from the
        share if `delete_file`. Refuses a queued or running job (cancel it
        first)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise ComposeError("unknown render job")
            if job.state in _ACTIVE_STATES:
                raise ComposeError("cancel the render before deleting it")
            out = self._output_path(job) if delete_file else None
            del self._jobs[job_id]
        if out:
            _safe_unlink(out)
        self._save(force=True)

    def rerun(self, job_id: str) -> ComposeJob:
        """A new job with the same settings (renders start from scratch,
        so this is how an interrupted or failed one is resumed)."""
        old = self._jobs.get(job_id)
        if old is None:
            raise ComposeError("unknown render job")
        return self.submit(ComposeSpec(**old.spec))

    def _output_path(self, job: ComposeJob) -> str | None:
        """The job's video on the share, only if it really is inside it."""
        if not job.output_rel:
            return None
        share = os.path.realpath(self.share_path)
        path = os.path.realpath(os.path.join(share, job.output_rel))
        return path if path.startswith(share + os.sep) else None

    # -- history file ----------------------------------------------------- #

    def _load(self) -> None:
        if not self._jobs_file or not os.path.isfile(self._jobs_file):
            return
        try:
            with open(self._jobs_file) as f:
                raw = json.load(f)
        except (OSError, ValueError) as exc:
            if self._log:
                self._log.warning("compose job history unreadable (%s); starting empty", exc)
            return
        for d in raw.get("jobs", []):
            try:
                job = ComposeJob.from_dict(d)
            except TypeError:
                continue
            if job.state in _ACTIVE_STATES:
                # The controller stopped mid-render (an update, a reboot).
                job.state = "interrupted"
                job.stage = "interrupted"
                job.error = "Stopped by a controller restart; use Re-run to render it again"
                job.finished_at = job.finished_at or time.time()
            self._jobs[job.id] = job
        self._prune()

    def _prune(self) -> None:
        now = time.time()
        done = sorted(
            (j for j in self._jobs.values() if j.state not in _ACTIVE_STATES),
            key=lambda j: j.created_at, reverse=True)
        for i, job in enumerate(done):
            age = now - (job.finished_at or job.created_at)
            if i >= JOBS_KEEP or age > JOBS_MAX_AGE_S:
                del self._jobs[job.id]

    def _save(self, force: bool = False) -> None:
        """Write the history atomically; progress-only changes at most every
        10 s (a render reports ~100 times)."""
        if not self._jobs_file:
            return
        now = time.time()
        if not force and now - self._last_save < 10:
            return
        self._last_save = now
        with self._lock:
            data = {"jobs": [j.summary() for j in self._jobs.values()]}
        try:
            os.makedirs(os.path.dirname(self._jobs_file), exist_ok=True)
            tmp = self._jobs_file + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self._jobs_file)
        except OSError as exc:
            if self._log:
                self._log.warning("could not save compose job history: %s", exc)

    def list(self) -> list[dict]:
        with self._lock:
            return [j.summary() for j in sorted(
                self._jobs.values(), key=lambda j: j.created_at, reverse=True
            )]

    # -- internals ------------------------------------------------------- #

    def _emit(self, job: ComposeJob) -> None:
        self._save(force=job.state not in ("running",) or job.stage in ("starting", "done"))
        try:
            self._on_update(job.summary())
        except Exception:  # noqa: BLE001 -- a broken listener must not kill the worker
            if self._log:
                self._log.exception("compose on_update callback failed")

    def _run_loop(self) -> None:
        _lower_thread_priority()
        while True:
            job_id = self._queue.get()
            job = self._jobs.get(job_id)
            if job is None or job.state == "cancelled":
                continue
            with self._lock:
                job.state = "running"
                job.started_at = time.time()
                job.stage = "starting"
            self._emit(job)
            try:
                out_rel = self._render(job)
                if job.state == "cancelled":
                    _safe_unlink(os.path.join(self.share_path, out_rel or ""))
                else:
                    job.state = "done"
                    job.stage = "done"
                    job.progress = 1.0
                    job.output_rel = out_rel
                    out = self._output_path(job)
                    try:
                        job.output_bytes = os.path.getsize(out) if out else None
                    except OSError:
                        job.output_bytes = None
            except Exception as exc:  # noqa: BLE001 -- report, never crash the loop
                if job.state == "cancelled":
                    # Operator pressed Cancel: the render unwinds via
                    # ComposeError("cancelled"), which is not a failure.
                    if self._log:
                        self._log.info("compose job %s cancelled", job.id)
                    continue
                job.state = "error"
                job.stage = "error"
                job.error = str(exc)
                if self._log:
                    self._log.exception("compose job %s failed", job.id)
            finally:
                job.finished_at = time.time()
                with self._lock:
                    self._prune()
                self._emit(job)

    def _render(self, job: ComposeJob) -> str:
        # video_compose pulls in OpenCV; import here so this module stays
        # importable (and unit-testable) without it.
        from src.controller import video_compose

        spec = ComposeSpec(**job.spec)
        session_dir = os.path.join(self.share_path, spec.session_name)
        if not os.path.isdir(session_dir):
            raise ComposeError("session directory not found")
        date_dir = resolve_date_dir(session_dir, spec.date_dir)
        resolved = resolve_layout(date_dir, spec)
        doc = resolved.doc
        t_start, t_end = resolved.window()
        speed = resolved.speed
        total_s = (t_end - t_start) / 1e9          # recording time covered
        out_s = total_s / speed                    # length of the video

        spectro = [t for t in doc["tiles"] if t["kind"] == "spectrogram"]
        mics = compose_layout.mic_sources(doc)
        # Spectrogram tiles: the composite is re-encoded to H.264 by ffmpeg
        # with the audio muxed in (Matroska, FLAC), so the compositor writes
        # a temp file. Audio tracks only: the video is stream-copied with the
        # audio muxed in. Neither: the compositor writes the final file.
        encode = bool(spectro)
        fmt = "mkv" if encode else spec.fmt
        out_name = f"{spec.session_name}_composed_{job.id}.{fmt}"
        out_path = os.path.join(date_dir, out_name)
        base_path = out_path + ".base.mp4" if mics else out_path

        def set_progress(frac: float, stage: str) -> None:
            if job.state == "cancelled":
                raise _CancelledError()
            job.progress = round(max(0.0, min(1.0, frac)), 4)
            job.stage = stage
            self._emit(job)

        def phase(lo: float, hi: float, stage: str):
            """A 0..1 sub-callback that maps into the [lo, hi] band -- so the
            video pass and each audio pass each own a slice of the bar
            instead of the audio work all landing at 99%."""
            def cb(done, total=1, _stage=None):
                frac = (done / total) if total else 0.0
                set_progress(lo + (hi - lo) * frac, _stage or stage)
            return cb

        if encode:
            video_lo, video_hi = 0.12, 0.75
        elif mics:
            video_lo, video_hi = 0.1, 0.99
        else:
            video_lo, video_hi = 0.0, 1.0
        aligned: dict[str, str] = {}
        temp_pngs: list[str] = []
        try:
            if mics:
                aligned = self._align_mics(
                    date_dir, [resolved.devices[m] for m in mics], t_start, t_end, out_path,
                    phase(0.0, 0.1, "aligning audio"), job.warnings)
            tiles = []
            strip_cache: dict[tuple, str] = {}
            for tile in doc["tiles"]:
                tile = {**tile, "label_text": _tile_label(resolved, tile)}
                if tile["kind"] == "spectrogram":
                    tile.update(strip=None, pps=1.0, win=2)
                    src = aligned.get(tile["source"])
                    if src:
                        w, h = tile["rect"][2], tile["rect"][3]
                        window_s = tile["options"]["window_s"]
                        strip_w, pps = compose_layout.spectrogram_strip(w, window_s, total_s)
                        opts = compose_layout.spectrogram_opts(tile)
                        key = (tile["source"], strip_w, h,
                               json.dumps(asdict(opts), sort_keys=True))
                        if key not in strip_cache:
                            png = (os.path.splitext(out_path)[0]
                                   + f".panel{len(strip_cache)}.png")
                            temp_pngs.append(png)
                            audio_align.render_strip_png(src, png, strip_w, h, opts)
                            strip_cache[key] = png
                        tile.update(strip=strip_cache[key], pps=pps,
                                    win=compose_layout.window_px(w, window_s, pps))
                tiles.append(tile)
            set_progress(video_lo, "compositing video")
            video_compose.render_layout(
                date_dir, base_path,
                (doc["canvas"]["width"], doc["canvas"]["height"]), tiles,
                fps=spec.fps,
                background=compose_layout.hex_to_bgr(doc["canvas"]["background"]),
                csv_skip={s.name: s.csv_skip for s in resolved.streams},
                progress=phase(video_lo, video_hi, "compositing video"),
                warnings=job.warnings,
                t_range=(t_start, t_end), speed=speed,
            )
            track_ids = [m for m in doc["audio_tracks"] if m in aligned]
            if track_ids and speed != 1.0:
                lo, hi = compose_layout.AUDIO_SPEED_RANGE
                if lo <= speed <= hi:
                    for m in track_ids:
                        stretched = os.path.splitext(aligned[m])[0] + "_tempo.flac"
                        temp_pngs.append(stretched)
                        audio_align.render_tempo(aligned[m], stretched, speed)
                        aligned[m] = stretched
                else:
                    job.warnings.append(
                        f"audio tracks left out: {speed:g}x is outside the "
                        f"{lo:g}-{hi:g}x that audio can be played at")
                    track_ids = []
            tracks = [aligned[m] for m in track_ids]
            labels = [resolved.devices[m]["label"] for m in track_ids]
            if encode:
                audio_align.render_with_audio(
                    base_path, tracks, out_path, labels,
                    progress=phase(0.75, 1.0, "encoding with audio"),
                    total_s=out_s)
                _safe_unlink(base_path)
            elif mics and tracks:
                audio_align.render_muxed_track(base_path, tracks, out_path, labels)
                _safe_unlink(base_path)
            elif mics:
                os.replace(base_path, out_path)
        except _CancelledError:
            _safe_unlink(base_path)
            _safe_unlink(out_path)
            raise ComposeError("cancelled") from None
        finally:
            for path in list(aligned.values()) + temp_pngs:
                _safe_unlink(path)

        return os.path.relpath(out_path, self.share_path)

    def _align_mics(self, date_dir: str, devices: list[dict], t_start: int, t_end: int,
                    out_path: str, progress,
                    warnings: list | None = None) -> dict[str, str]:
        """Each AudioMoth aligned to the camera window as a FLAC next to
        `out_path`: {device id: path}. A device with no recording in the
        window is left out with a warning; none at all fails the render."""
        window_s = (t_end - t_start) / 1e9
        aligned: dict[str, str] = {}
        n = len(devices)
        try:
            for i, dev in enumerate(devices):
                parts = _aligned_mic_parts(
                    find_microphone_segments(date_dir, dev["folder"], dev["device"]),
                    t_start, t_end, warnings, self._log)
                if not parts:
                    if warnings is not None:
                        warnings.append(f"{dev['label']}: no recording overlaps the video")
                    continue
                path = os.path.splitext(out_path)[0] + f"_aligned{i}.flac"
                audio_align.render_aligned_audio_segments(
                    parts, t_start, window_s, path, parts[0][1].nominal_rate_hz,
                    progress=lambda f, _i=i: progress((_i + f) / n),
                )
                aligned[dev["id"]] = path
        except BaseException:
            for path in aligned.values():
                _safe_unlink(path)
            raise
        if not aligned:
            raise ComposeError("no microphone recording overlaps the video")
        return aligned


def _lower_thread_priority() -> None:
    """Nice the calling thread (Linux schedules threads individually), so
    the in-process OpenCV compositing of a render or preview yields to the
    controller's real-time work, as its ffmpeg children do."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except (AttributeError, OSError):
        pass


class _CancelledError(Exception):
    pass


def _safe_unlink(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def any_session_busy_reason(sessions: dict,
                            export_queue: dict | None = None) -> str | None:
    """A reason string if any session is still recording / about to start /
    still exporting -- composing then would load the controller or race the
    share -- else None. `sessions` is facade.get_recording_sessions()'s
    value (RecordingSession objects, or their dicts).

    A `stopped` session is *finished* -- it's exactly what compose targets
    -- so it only counts as busy while it still has exports in flight. With
    `export_queue` (ExportQueue.snapshot()) that means a module actually
    exporting or queued for it: a session's `pending_exports` counter can
    stay above zero after every file has landed (an export lost with a
    module reboot), and used to block every render indefinitely. Without a
    snapshot, the counter is the fallback."""
    def field(s, key, default=None):
        return s.get(key, default) if isinstance(s, dict) else getattr(s, key, default)

    exporting = None
    if export_queue is not None:
        exporting = {
            e.get("session") for e in
            (export_queue.get("active") or []) + (export_queue.get("queued") or [])
        }

    running = {"active", "paused", "scheduled", "pending"}
    for name, s in sessions.items():
        state = str(field(s, "state"))
        if state in running:
            return f"a session ({name}) is still {state}; compose when it has ended"
        if state != "stopped":
            continue
        busy = (name in exporting if exporting is not None
                else (field(s, "pending_exports", 0) or 0) > 0)
        if busy:
            return (
                f"a session ({name}) still has exports in flight; "
                "compose once they finish"
            )
    return None
