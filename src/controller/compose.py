"""
Session video composition jobs -- the backend for the frontend
"compose aggregated video" page.

A composite render is minutes of CPU + IO on a Pi 5 controller, so it
cannot run inside a socket handler. `ComposeWorker` is a single-slot
background worker: one job at a time, a small queue, and progress
callbacks the web layer forwards to the client. The artifact is written
into the session's own date directory so it shows up in the normal
file browser / download path with no new storage concept.

Phase 1 is video layout only -- presets (`side | stack | grid | loom |
auto`), planned aspect-ratio-aware from each stream's real dimensions
(probed with ffprobe, so this module needs neither OpenCV nor Flask).
Audio modes (muxed track / spectrogram strip / ethogram panel) plug in
at `_render` in phase 2 via audio_align.py.
"""

from __future__ import annotations

import csv
import glob
import hashlib
import json
import math
import os
import queue
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from itertools import pairwise

from src.controller import audio_align

LAYOUTS = ("auto", "side", "stack", "grid", "loom")
# none  -> silent composite (default)
# track -> aligned audio muxed as an audio track
# strip -> scrolling spectrogram strip overlaid on the bottom of the video
# panel -> full scrolling spectrogram panel below the video (ethogram-style)
AUDIO_MODES = ("none", "track", "strip", "panel")
DEFAULT_CANVAS_WIDTH = 1920
DEFAULT_FPS = 15
MAX_QUEUE = 4


class ComposeError(ValueError):
    """Bad request -- surfaced to the client verbatim."""


@dataclass
class AudioSpec:
    mode: str = "none"
    source: str | None = None                 # mic module folder; None -> first
    spectrogram: dict = field(default_factory=dict)  # -> audio_align.SpectrogramOpts

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
        return cls(mode=mode, source=source, spectrogram=dict(spectrogram))

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

    @classmethod
    def from_dict(cls, raw: dict) -> ComposeSpec:
        if not isinstance(raw, dict):
            raise ComposeError("compose spec must be an object")
        name = str(raw.get("session_name", "")).strip()
        if not _safe_name(name):
            raise ComposeError("invalid or missing session_name")
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
        return cls(
            session_name=name, date_dir=date_dir, streams=streams,
            layout=layout, fps=fps, fmt=fmt, audio=audio,
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


def probe_dimensions(video_path: str) -> tuple[int, int]:
    """(width, height) of a video file's first stream, via ffprobe."""
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
    can't be determined."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-count_packets", "-show_entries", "stream=nb_read_packets",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, check=True,
        )
        return int(out.stdout.strip() or 0)
    except (subprocess.CalledProcessError, ValueError, OSError):
        return 0


def _prestage_skip(video_path: str, n_csv_rows: int) -> int:
    """How many leading `*_timestamps.csv` rows have no matching video frame.

    The camera's frame precallback can log rows for a few frames captured
    before `start_encoder()` actually opened the stream (and, on older
    firmware, from CSV-open rather than encoder-start -- up to ~1 s). Those
    head rows shift every downstream `frame i <-> timestamp i` mapping, so
    they must be dropped. Mirrors tools/make_aligned_video.load_timestamps.
    """
    n_frames = _video_frame_count(video_path)
    return max(0, n_csv_rows - n_frames) if n_frames else 0


# --------------------------------------------------------------------------- #
# Session layout on disk                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class SessionStream:
    name: str          # module folder name
    video_path: str
    csv_path: str
    width: int
    height: int
    csv_skip: int = 0  # leading timestamp rows with no video frame (pre-stage)


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
        videos = sorted(
            glob.glob(os.path.join(module_dir, "*.ts"))
            + glob.glob(os.path.join(module_dir, "*.mp4"))
        )
        csvs = glob.glob(os.path.join(module_dir, "*_timestamps.csv"))
        if not videos or not csvs:
            continue
        w, h = probe_dimensions(videos[0])
        try:
            n_rows = sum(1 for _ in open(csvs[0], newline="")) - 1  # minus header
        except OSError:
            n_rows = 0
        skip = _prestage_skip(videos[0], n_rows) if n_rows > 0 else 0
        streams.append(SessionStream(entry, videos[0], csvs[0], w, h, skip))
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
            lasts.append(rows[-1])
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
    return {
        "session_name": session_name,
        "date_dir": os.path.basename(resolved),
        "dates": dates,
        "cameras": [
            {
                "name": s.name, "width": s.width, "height": s.height,
                "fps": round(r, 2) if (r := stream_fps(s.csv_path)) else None,
            }
            for s in cams
        ],
        "mics": list_mic_folders(resolved),
        "suggested_fps": suggest_fps(wanted),
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
                sidecar = f"{os.path.splitext(audio)[0]}_timestamps.txt"
                if os.path.isfile(sidecar):
                    return audio, sidecar
    raise ComposeError(
        f"no microphone recording found{f' for {source}' if source else ''}"
    )


def _preview_spectrogram_png(cache_dir: str, date_dir: str, audio: AudioSpec,
                             width: int, logger=None) -> str | None:
    """A representative (unaligned) spectrogram PNG for the preview, cached
    by mic file + spectrogram options so it's rendered once per settings
    combination. Returns None (preview just omits the audio panel) if
    ffmpeg or the mic recording isn't usable -- a preview must not fail
    over audio."""
    try:
        audio_file, _sidecar = find_microphone(date_dir, audio.source)
        try:
            mtime = int(os.path.getmtime(audio_file))
        except OSError:
            mtime = 0
        # `strip` and `panel` render the same source spectrogram (only the
        # placement differs), so the mode is deliberately not in the key.
        key = hashlib.md5(
            f"{audio_file}|{mtime}|{width}|"
            f"{json.dumps(audio.spectrogram, sort_keys=True)}".encode()
        ).hexdigest()[:12]
        out = os.path.join(cache_dir, f"spec_{key}.png")
        if not os.path.isfile(out):
            height = max(2, round(width * 0.28) // 2 * 2)
            audio_align.render_source_spectrogram_png(
                audio_file, out, size=(width, height), spec=audio.spec_opts(),
            )
        return out
    except Exception as exc:  # noqa: BLE001 -- preview must not fail over audio
        if logger:
            logger.warning("compose preview: skipping audio panel (%s)", exc)
        return None


def render_preview(share_path: str, spec: ComposeSpec, max_width: int = 960,
                   rebuild: bool = False, logger=None) -> bytes:
    """One composited frame (mid-window) as PNG bytes -- a fast layout
    preview before committing to a full render. Cameras are drawn from
    cached per-module thumbnails (rebuilt on `rebuild=True`); a `strip`/
    `panel` audio mode is composited in the same place a real render puts
    it."""
    from src.controller import video_compose

    session_dir = os.path.join(share_path, spec.session_name)
    if not os.path.isdir(session_dir):
        raise ComposeError("session directory not found")
    date_dir = resolve_date_dir(session_dir, spec.date_dir)
    date_base = os.path.basename(date_dir)
    if rebuild:
        clear_preview_cache(spec.session_name, date_base)
    cache_dir = _preview_cache_dir(spec.session_name, date_base)

    streams = discover_streams(date_dir, spec.streams)
    regions, cw, ch = plan_regions(
        [(s.width, s.height) for s in streams], spec.layout, canvas_width=max_width,
    )

    audio = AudioSpec(**spec.audio)
    audio_png = None
    if audio.mode in ("strip", "panel"):
        audio_png = _preview_spectrogram_png(cache_dir, date_dir, audio, cw, logger)

    tmp = os.path.join(cache_dir, f".preview_{uuid.uuid4().hex[:8]}.png")
    try:
        video_compose.compose_preview_frame(
            date_dir, tmp, streams=[s.name for s in streams],
            regions=regions, canvas=(cw, ch), cache_dir=cache_dir,
            audio_png=audio_png,
            audio_mode=audio.mode if audio_png else None,
            csv_skip={s.name: s.csv_skip for s in streams},
        )
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        _safe_unlink(tmp)


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
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def summary(self) -> dict:
        return asdict(self)


class ComposeWorker:
    """One render at a time, small FIFO queue, progress via `on_update`."""

    def __init__(
        self, share_path: str,
        busy_check=None,        # () -> str|None : reason a render is disallowed now
        on_update=None,         # (job_summary: dict) -> None
        logger=None,
    ):
        self.share_path = share_path
        self._busy_check = busy_check or (lambda: None)
        self._on_update = on_update or (lambda _summary: None)
        self._log = logger
        self._jobs: dict[str, ComposeJob] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
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

    def list(self) -> list[dict]:
        with self._lock:
            return [j.summary() for j in sorted(
                self._jobs.values(), key=lambda j: j.created_at, reverse=True
            )]

    # -- internals ------------------------------------------------------- #

    def _emit(self, job: ComposeJob) -> None:
        try:
            self._on_update(job.summary())
        except Exception:  # noqa: BLE001 -- a broken listener must not kill the worker
            if self._log:
                self._log.exception("compose on_update callback failed")

    def _run_loop(self) -> None:
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
            except Exception as exc:  # noqa: BLE001 -- report, never crash the loop
                job.state = "error"
                job.stage = "error"
                job.error = str(exc)
                if self._log:
                    self._log.exception("compose job %s failed", job.id)
            finally:
                job.finished_at = time.time()
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
        streams = discover_streams(date_dir, spec.streams)
        regions, canvas_w, canvas_h = plan_regions(
            [(s.width, s.height) for s in streams], spec.layout,
        )

        audio = AudioSpec(**spec.audio)
        # `strip`/`panel` composite a spectrogram video with ffmpeg, so the
        # base pass writes a temp mp4; `track`/`none` write the final file.
        needs_ffmpeg_pass = audio.mode in ("strip", "panel")
        fmt = "mkv" if needs_ffmpeg_pass else spec.fmt
        out_name = f"{spec.session_name}_composed_{job.id}.{fmt}"
        out_path = os.path.join(date_dir, out_name)
        base_path = out_path + ".base.mp4" if audio.mode != "none" else out_path

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

        # `none`/`track` audio is cheap (stream copy); `strip`/`panel` re-encode
        # the whole video, so give it a real slice of the bar.
        video_hi = 0.65 if audio.mode in ("strip", "panel") else (
            0.97 if audio.mode == "track" else 1.0
        )
        try:
            video_compose.compose_session_video(
                date_dir, base_path,
                streams=[s.name for s in streams],
                regions=regions, canvas=(canvas_w, canvas_h),
                fps=spec.fps, progress=phase(0.0, video_hi, "compositing video"),
                csv_skip={s.name: s.csv_skip for s in streams},
            )
            if audio.mode != "none":
                self._apply_audio(date_dir, streams, audio, base_path, out_path,
                                  spec.fps, phase)
                _safe_unlink(base_path)
        except _CancelledError:
            _safe_unlink(base_path)
            _safe_unlink(out_path)
            raise ComposeError("cancelled") from None

        return os.path.relpath(out_path, self.share_path)

    def _apply_audio(self, date_dir, streams, audio: AudioSpec,
                     base_path: str, out_path: str, fps: int, phase) -> None:
        audio_file, sidecar = find_microphone(date_dir, audio.source)
        t_start, t_end = camera_window(streams)
        window_s = (t_end - t_start) / 1e9
        fit = audio_align.parse_mic_sidecar(sidecar, audio_file)
        aligned = os.path.splitext(out_path)[0] + "_aligned.flac"
        audio_align.render_aligned_audio(
            audio_align.AudioStream("mic", audio_file, sidecar), fit,
            t_start, window_s, aligned, fit.nominal_rate_hz,
            progress=phase(0.65, 0.75, "aligning audio"),
        )
        try:
            if audio.mode == "track":
                audio_align.render_muxed_track(base_path, aligned, out_path)
            else:  # strip | panel
                audio_align.render_overlay(
                    base_path, [aligned], out_path,
                    audio_align.DEFAULT_STRIP_HEIGHT, fps,
                    spec=audio.spec_opts(), stacked=(audio.mode == "panel"),
                    progress=phase(0.75, 1.0, "rendering audio panel"),
                    total_s=window_s,
                )
        finally:
            _safe_unlink(aligned)


class _CancelledError(Exception):
    pass


def _safe_unlink(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def any_session_busy_reason(sessions: dict) -> str | None:
    """A reason string if any session is still recording / about to start /
    still exporting -- composing then would load the controller or race the
    share -- else None. `sessions` is facade.get_recording_sessions()'s
    value (RecordingSession objects, or their dicts).

    A `stopped` session is *finished* -- it's exactly what compose targets
    -- so it only counts as busy while it still has exports in flight."""
    def field(s, key, default=None):
        return s.get(key, default) if isinstance(s, dict) else getattr(s, key, default)

    running = {"active", "paused", "scheduled", "pending"}
    for name, s in sessions.items():
        state = str(field(s, "state"))
        if state in running:
            return f"a session ({name}) is still {state}; compose when it has ended"
        if state == "stopped" and (field(s, "pending_exports", 0) or 0) > 0:
            return (
                f"a session ({name}) still has exports in flight; "
                "compose once they finish"
            )
    return None
