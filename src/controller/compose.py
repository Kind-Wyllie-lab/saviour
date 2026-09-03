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

import glob
import json
import math
import os
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

LAYOUTS = ("auto", "side", "stack", "grid", "loom")
DEFAULT_CANVAS_WIDTH = 1920
DEFAULT_FPS = 15
MAX_QUEUE = 4


class ComposeError(ValueError):
    """Bad request -- surfaced to the client verbatim."""


@dataclass
class ComposeSpec:
    session_name: str
    date_dir: str | None = None       # date-subdir name; None -> the latest one
    streams: list[str] | None = None  # module folder names; None -> every camera
    layout: str = "auto"
    fps: int = DEFAULT_FPS
    fmt: str = "mp4"

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
        return cls(
            session_name=name, date_dir=date_dir, streams=streams,
            layout=layout, fps=fps, fmt=fmt,
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
        streams.append(SessionStream(entry, videos[0], csvs[0], w, h))
    if wanted:
        missing = [w for w in wanted if w not in {s.name for s in streams}]
        if missing:
            raise ComposeError(f"requested streams not found: {', '.join(missing)}")
    if not streams:
        raise ComposeError("no camera streams found for this session")
    return streams


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

        out_name = f"{spec.session_name}_composed_{job.id}.{spec.fmt}"
        out_path = os.path.join(date_dir, out_name)

        def progress(done: int, total: int, stage: str = "rendering") -> None:
            if job.state == "cancelled":
                raise _CancelledError()
            job.progress = round(done / total, 4) if total else 0.0
            job.stage = stage
            self._emit(job)

        try:
            video_compose.compose_session_video(
                date_dir, out_path,
                streams=[s.name for s in streams],
                regions=regions, canvas=(canvas_w, canvas_h),
                fps=spec.fps, progress=progress,
            )
        except _CancelledError:
            _safe_unlink(out_path)
            raise ComposeError("cancelled") from None

        return os.path.relpath(out_path, self.share_path)


class _CancelledError(Exception):
    pass


def _safe_unlink(path: str) -> None:
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def any_session_busy_reason(sessions: dict) -> str | None:
    """A reason string if any session is still recording / about to /
    still exporting -- composing then would race the share -- else None.
    `sessions` is facade.get_recording_sessions()'s value."""
    busy = {"active", "scheduled", "stopped", "pending"}
    for name, s in sessions.items():
        if isinstance(s, dict):
            state = s.get("state")
        else:
            state = getattr(s, "state", None)
        if str(state) in busy:
            return f"a session ({name}) is still {state}; compose when it has ended"
    return None
