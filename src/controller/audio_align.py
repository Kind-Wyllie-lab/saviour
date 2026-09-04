"""
Align an AudioMoth recording to a session's video wall-clock timeline and
render a spectrogram for visualising vocalisations alongside behaviour.

Companion to src/controller/video_compose.py. Both the camera per-frame
CSV (`timestamp_ns`) and the microphone timestamp sidecar
(`<stem>_timestamps.txt`) are stamped on the same PTP-disciplined
CLOCK_REALTIME, so audio and video are already on one timeline to within
each module's phc2sys offset (<50 us at the recording-start gate). This
tool turns that shared clock into:

  1. `<session>_<label>_aligned.flac` -- the recording resampled so its
     playback rate matches wall-clock (correcting the AudioMoth's real
     vs nominal sample rate) and sample 0 pinned to the video window
     start. Full-band; feeds USV tools (DeepSqueak etc.) directly.
  2. `<session>_<label>_spectrogram.png` (--spectrogram) -- a static
     whole-session spectrogram per microphone, for scanning where calls
     occur.
  3. `<composite>_with_audio.mkv` (--overlay) -- video_compose.py's
     output with a scrolling spectrogram strip composited on and every
     aligned track muxed in.

ffmpeg (and ffprobe) must be on PATH -- the whole audio path shells out
to them for sample-rate correction, resampling and muxing.

Usage:
    python3 src/controller/audio_align.py /path/to/session/date_dir \
        --spectrogram --overlay /path/to/session/session_aggregated.mp4

The alignment fit itself has no dependency on the module's recording
config -- it is derived entirely from the sidecar block-start times and
the audio file's own sample count.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import logging
import math
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_SPECTROGRAM_SIZE = (1920, 480)
DEFAULT_STRIP_HEIGHT = 240
# Scrolling spectrogram strip: horizontal pixels per second of audio, and a
# hard cap on the pre-rendered strip width so a long session doesn't ask
# ffmpeg for a 200k-px PNG.
PLAYHEAD_PPS = 120
_MAX_STRIP_PX = 48000
# Blocks skipped from the sidecar timing fit: the first record() call(s)
# straddle the capture stream warm-up and their pre-call timestamps are
# unreliable. See parse_mic_sidecar.
_WARMUP_SKIP_BLOCKS = 2

# ffmpeg showspectrum(pic) enum values we let the caller pick from.
SPEC_COLORS = (
    "intensity", "rainbow", "moreland", "nebulae", "fire", "fiery", "fruit",
    "cool", "magma", "green", "viridis", "plasma", "cividis", "terrain",
    "channel",
)
SPEC_FSCALES = ("lin", "log", "rlog")
SPEC_ASCALES = ("lin", "sqrt", "cbrt", "log", "4thrt", "5thrt")


@dataclass
class SpectrogramOpts:
    """How the audio is drawn -- colour map, frequency band, and the
    amplitude/frequency scales. Shared by the static PNG, the overlay
    strip and the ethogram panel so they render identically."""

    color: str = "intensity"
    fmin_hz: int = 0
    fmax_hz: int | None = None
    fscale: str = "lin"      # frequency axis
    ascale: str = "log"      # amplitude -> colour
    gain: float = 1.0

    def __post_init__(self) -> None:
        if self.color not in SPEC_COLORS:
            raise ValueError(f"spectrogram color must be one of {SPEC_COLORS}")
        if self.fscale not in SPEC_FSCALES:
            raise ValueError(f"spectrogram fscale must be one of {SPEC_FSCALES}")
        if self.ascale not in SPEC_ASCALES:
            raise ValueError(f"spectrogram ascale must be one of {SPEC_ASCALES}")
        if not 0.1 <= self.gain <= 20:
            raise ValueError("spectrogram gain must be between 0.1 and 20")
        if self.fmax_hz is not None and self.fmax_hz <= self.fmin_hz:
            raise ValueError("spectrogram fmax must be above fmin")

    def _common(self, width: int, height: int) -> str:
        band = ""
        if self.fmin_hz:
            band += f":start={self.fmin_hz}"
        if self.fmax_hz:
            band += f":stop={self.fmax_hz}"
        return (
            f"s={width}x{height}:mode=combined:color={self.color}:"
            f"scale={self.ascale}:fscale={self.fscale}:gain={self.gain}{band}"
        )

    def pic_filter(self, width: int, height: int) -> str:
        return f"showspectrumpic={self._common(width, height)}:legend=1"

    def pic_filter_nolegend(self, width: int, height: int) -> str:
        """A bare spectrogram image with no axis/legend chrome -- for a
        strip that pans, where a baked-in axis would scroll with it."""
        return f"showspectrumpic={self._common(width, height)}:legend=0"

    def scroll_filter(self, width: int, height: int, fps: int) -> str:
        return (
            f"showspectrum={self._common(width, height)}:"
            f"slide=scroll:fps={fps}"
        )

# The microphone recorder loop reads a fixed number of frames per block
# (`recorder.record(numframes=frame_num)`), writing one sidecar line per
# block, so every block is exactly this many samples. It is the module's
# `microphone.frame_num` / `microphone.block_size` default and has been
# stable; overridable on the CLI. Backing it out of the decoded sample
# count instead is unreliable -- FLAC reports a padded count.
DEFAULT_FRAME_NUM = 1024 * 128


@dataclass
class AudioStream:
    """One AudioMoth's recording within a session date directory."""

    label: str
    audio_path: str
    sidecar_path: str


@dataclass
class SidecarFit:
    """Least-squares fit of block-start wall time against block index.

    `sample0_wall_ns` is the PTP wall-clock instant of the first audio
    sample; `measured_rate_hz` is the AudioMoth's true sample rate (its
    nominal rate is never exact), from the fit slope and the fixed
    `frame_num` block size. `residual_p50_ms` / `residual_p95_ms` are
    the per-block timestamp scatter about the fitted line after outlier
    rejection -- p95 is the honest alignment-accuracy figure; a handful
    of `n_outliers` (scheduler stalls on a loaded Pi) are dropped from
    the fit. `n_samples` is `n_blocks * frame_num`; `probe_samples` is
    what ffprobe reported (FLAC can pad it).
    """

    sample0_wall_ns: int
    measured_rate_hz: float
    nominal_rate_hz: int
    frame_num: int
    n_blocks: int
    probe_samples: int
    residual_p50_ms: float
    residual_p95_ms: float
    n_outliers: int
    started_wall_ns: int

    @property
    def n_samples(self) -> int:
        return self.n_blocks * self.frame_num

    @property
    def duration_s(self) -> float:
        return self.n_samples / self.measured_rate_hz

    def as_report(self) -> dict:
        return {
            "sample0_wall_ns": self.sample0_wall_ns,
            "measured_rate_hz": round(self.measured_rate_hz, 4),
            "nominal_rate_hz": self.nominal_rate_hz,
            "rate_error_ppm": round(
                1e6 * (self.measured_rate_hz - self.nominal_rate_hz)
                / self.nominal_rate_hz,
                2,
            ),
            "frame_num": self.frame_num,
            "n_blocks": self.n_blocks,
            "n_samples": self.n_samples,
            "probe_samples": self.probe_samples,
            "residual_p50_ms": _round_or_none(self.residual_p50_ms, 3),
            "residual_p95_ms": _round_or_none(self.residual_p95_ms, 3),
            "n_outliers": self.n_outliers,
            "recording_duration_s": round(self.duration_s, 3),
        }


def _round_or_none(value: float, digits: int) -> float | None:
    return None if value is None or np.isnan(value) else round(value, digits)


@dataclass
class AlignOptions:
    out_dir: str
    out_rate: int | None = None
    spectrogram: bool = False
    overlay_path: str | None = None
    strip_height: int = DEFAULT_STRIP_HEIGHT
    frame_num: int = DEFAULT_FRAME_NUM
    ptp_history: str | None = None
    ethogram: bool = False
    ethogram_fps: int = 15
    frame_index_offset: int = 0
    spec: SpectrogramOpts = field(default_factory=SpectrogramOpts)


# --------------------------------------------------------------------------- #
# Discovery / parsing                                                         #
# --------------------------------------------------------------------------- #

_AUDIO_EXTS = (".flac", ".wav")


def discover_audio_streams(date_dir: str) -> list[AudioStream]:
    """Find every module subfolder holding an audio file plus a matching
    `<stem>_timestamps.txt` sidecar. Camera / TTL folders are skipped."""
    streams: list[AudioStream] = []
    for entry in sorted(os.listdir(date_dir)):
        module_dir = os.path.join(date_dir, entry)
        if not os.path.isdir(module_dir):
            continue
        for ext in _AUDIO_EXTS:
            for audio_path in sorted(glob.glob(os.path.join(module_dir, f"*{ext}"))):
                sidecar = f"{os.path.splitext(audio_path)[0]}_timestamps.txt"
                if os.path.isfile(sidecar):
                    label = _label_from_filename(os.path.basename(audio_path), entry)
                    streams.append(AudioStream(label, audio_path, sidecar))
    return streams


def _camera_timestamp_csvs(date_dir: str) -> list[str]:
    """Every module subfolder's per-frame CSV (a `*_timestamps.csv` with a
    `timestamp_ns` column) -- the same set video_compose.py composites,
    found here without importing it so this tool has no OpenCV dependency."""
    found: list[str] = []
    for entry in sorted(os.listdir(date_dir)):
        module_dir = os.path.join(date_dir, entry)
        if not os.path.isdir(module_dir):
            continue
        for csv_path in sorted(glob.glob(os.path.join(module_dir, "*_timestamps.csv"))):
            with open(csv_path, newline="") as f:
                header = next(csv.reader(f), [])
            if "timestamp_ns" in header:
                found.append(csv_path)
    return found


def _label_from_filename(filename: str, fallback: str) -> str:
    """`<prefix>_<label>_(<segment>_<utc>).flac` -> `<label>`."""
    stem = os.path.splitext(filename)[0]
    head = stem.split("_(")[0]
    parts = head.split("_")
    return parts[-1] if len(parts) >= 2 else fallback


def _probe_audio(audio_path: str) -> tuple[int, int]:
    """Return (probe_samples, sample_rate) via ffprobe. `probe_samples`
    is only a cross-check -- FLAC reports it rounded up by up to a frame."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=sample_rate,duration_ts,nb_samples",
            "-of", "json", audio_path,
        ],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(out.stdout)["streams"][0]
    sample_rate = int(stream["sample_rate"])
    probe_samples = int(stream.get("nb_samples") or stream.get("duration_ts") or 0)
    return probe_samples, sample_rate


def parse_mic_sidecar(
    sidecar_path: str, audio_path: str, frame_num: int = DEFAULT_FRAME_NUM,
) -> SidecarFit:
    """Fit block-start wall times against block index.

    Each sidecar line is one `recorder.record(numframes=frame_num)`
    block, so wall time advances `frame_num / true_rate` per line: the
    fit slope gives the true sample rate directly, with no dependency on
    the (FLAC-padded) decoded sample count.
    """
    block_times: list[float] = []
    started_wall_ns = 0
    with open(sidecar_path) as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("STARTED "):
                started_wall_ns = int(float(line.split(maxsplit=1)[1]) * 1e9)
                continue
            if " " in line or not _is_float(line):
                continue  # START_AT / STARTUP_LATENCY_MS / SEGMENT_* trailer
            block_times.append(float(line))

    probe_samples, nominal_rate = _probe_audio(audio_path)
    n_blocks = len(block_times)
    if n_blocks < 2:
        # Degenerate segment -- fall back to STARTED + nominal rate.
        anchor = started_wall_ns or (int(block_times[0] * 1e9) if block_times else 0)
        return SidecarFit(
            sample0_wall_ns=anchor,
            measured_rate_hz=float(nominal_rate),
            nominal_rate_hz=nominal_rate,
            frame_num=frame_num,
            n_blocks=max(n_blocks, 1),
            probe_samples=probe_samples,
            residual_p50_ms=float("nan"),
            residual_p95_ms=float("nan"),
            n_outliers=0,
            started_wall_ns=started_wall_ns or anchor,
        )

    k = np.arange(n_blocks, dtype=np.float64)
    t = np.asarray(block_times, dtype=np.float64)
    # The first record() call blocks while the capture stream warms up -- on a
    # 192 kHz / 131072-frame config that's up to a full block (~0.68 s), and on
    # an AudioMoth sharing the device with the monitoring stream it can be
    # more. Its pre-call time.time() stamp therefore lands well before any
    # audio was actually delivered and drags the sample-0 intercept early
    # (audio ends up leading the video). Blocks after the warm-up are evenly
    # spaced and clean, so fit the line from there and let it extrapolate
    # back to block 0.
    fit_from = _WARMUP_SKIP_BLOCKS if n_blocks >= _WARMUP_SKIP_BLOCKS + 4 else 0
    slope, intercept, keep_fit = _robust_linfit(k[fit_from:], t[fit_from:])
    keep = np.ones(n_blocks, dtype=bool)
    keep[fit_from:] = keep_fit
    keep[:fit_from] = False
    resid_ms = np.abs(t - (slope * k + intercept)) * 1e3

    drift_blocks = abs(probe_samples - n_blocks * frame_num) / frame_num
    if probe_samples and drift_blocks > 2:
        logger.warning(
            "%s: %d blocks x %d != %d decoded samples (%.1f blocks off) -- "
            "truncated recording or wrong --frame-num?",
            os.path.basename(sidecar_path), n_blocks, frame_num,
            probe_samples, drift_blocks,
        )

    return SidecarFit(
        sample0_wall_ns=int(round(intercept * 1e9)),
        measured_rate_hz=frame_num / slope,
        nominal_rate_hz=nominal_rate,
        frame_num=frame_num,
        n_blocks=n_blocks,
        probe_samples=probe_samples,
        residual_p50_ms=float(np.percentile(resid_ms[keep], 50)),
        residual_p95_ms=float(np.percentile(resid_ms[keep], 95)),
        n_outliers=int(np.count_nonzero(~keep)),
        started_wall_ns=started_wall_ns or int(round(intercept * 1e9)),
    )


def _robust_linfit(
    x: np.ndarray, y: np.ndarray, sigma: float = 3.0, iterations: int = 5,
) -> tuple[float, float, np.ndarray]:
    """Ordinary least squares with iterative n-sigma outlier rejection.
    The microphone's per-block `time.time()` is occasionally stalled a
    whole block or more by scheduler pressure on a loaded Pi; those few
    points would otherwise drag the intercept (sample-0 wall time).
    Returns (slope, intercept, keep_mask)."""
    keep = np.ones(x.size, dtype=bool)
    slope, intercept = np.polyfit(x, y, 1)
    for _ in range(iterations):
        resid = y - (slope * x + intercept)
        std = np.std(resid[keep])
        new_keep = np.abs(resid) < sigma * std if std > 0 else keep
        if new_keep.sum() < 2 or np.array_equal(new_keep, keep):
            keep = new_keep
            break
        keep = new_keep
        slope, intercept = np.polyfit(x[keep], y[keep], 1)
    return slope, intercept, keep


def _is_float(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Window resolution                                                           #
# --------------------------------------------------------------------------- #


def resolve_window(
    date_dir: str, fits: list[SidecarFit],
    t_start_ns: int | None, duration_s: float | None,
) -> tuple[int, float]:
    """Wall-clock window (start ns, duration s) the aligned audio must
    span. Explicit args win; otherwise take it from the camera streams
    exactly as video_compose.py does (overlap of every camera); failing
    that, the union of the audio streams themselves."""
    if t_start_ns is not None and duration_s is not None:
        return t_start_ns, duration_s

    cam_first: list[int] = []
    cam_last: list[int] = []
    for csv_path in _camera_timestamp_csvs(date_dir):
        with open(csv_path, newline="") as f:
            rows = [int(r["timestamp_ns"]) for r in csv.DictReader(f)]
        if rows:
            cam_first.append(rows[0])
            cam_last.append(rows[-1])

    if cam_first:
        start = max(cam_first)
        end = min(cam_last)
    else:
        start = min(f.sample0_wall_ns for f in fits)
        end = max(f.sample0_wall_ns + int(f.duration_s * 1e9) for f in fits)

    start = t_start_ns if t_start_ns is not None else start
    dur = duration_s if duration_s is not None else max(0.0, (end - start) / 1e9)
    return start, dur


# --------------------------------------------------------------------------- #
# PTP quality during the recording                                            #
# --------------------------------------------------------------------------- #


def discover_ptp_history(date_dir: str) -> str | None:
    """Look for a controller PTP-history CSV (`/api/ptp_history.csv` /
    the diagnostics bundle's `ptp_history_*.csv`) next to the session."""
    for root in (date_dir, os.path.dirname(os.path.normpath(date_dir)), os.getcwd()):
        hits = sorted(glob.glob(os.path.join(root, "*ptp*history*.csv")))
        if hits:
            return hits[0]
    return None


def summarise_ptp_window(
    ptp_csv_path: str, t_start_ns: int, t_end_ns: int,
) -> dict | None:
    """Summarise `ptp4l_offset_ns` / `phc2sys_offset_ns` for every sample
    inside [t_start_ns, t_end_ns] -- so the alignment report can be read
    with the PTP quality that actually held during the recording, rather
    than trusting the 50 us record-start gate blanket. Returns None if
    the CSV has no samples in the window."""
    t0, t1 = t_start_ns / 1e9, t_end_ns / 1e9
    ptp4l: list[float] = []
    phc2sys: list[float] = []
    modules: set[str] = set()
    with open(ptp_csv_path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                epoch = float(row["timestamp_epoch"])
            except (KeyError, ValueError, TypeError):
                continue
            if not t0 <= epoch <= t1:
                continue
            modules.add(row.get("module_id", ""))
            for key, sink in (("ptp4l_offset_ns", ptp4l),
                              ("phc2sys_offset_ns", phc2sys)):
                val = row.get(key)
                if val not in (None, ""):
                    try:
                        sink.append(abs(float(val)))
                    except ValueError:
                        pass
    if not ptp4l and not phc2sys:
        return None

    def stats(values: list[float]) -> dict | None:
        if not values:
            return None
        arr = np.asarray(values)
        return {
            "abs_p50_ns": round(float(np.percentile(arr, 50)), 1),
            "abs_p95_ns": round(float(np.percentile(arr, 95)), 1),
            "abs_max_ns": round(float(arr.max()), 1),
        }

    return {
        "source": os.path.basename(ptp_csv_path),
        "samples": len(ptp4l) or len(phc2sys),
        "modules": sorted(m for m in modules if m),
        "ptp4l_offset": stats(ptp4l),
        "phc2sys_offset": stats(phc2sys),
    }


# --------------------------------------------------------------------------- #
# Frame timing -- position video frames by their real capture timestamp      #
# --------------------------------------------------------------------------- #


def map_grid_to_frames(
    frame_ts_ns: list[int], grid_start_ns: int, grid_step_ns: int, n_out: int,
    index_offset: int = 0,
) -> list[int]:
    """For each of `n_out` evenly spaced output times, the index of the
    video frame whose real capture timestamp is closest -- the fix for
    positioning frames by `timestamp_ns` instead of assuming a constant
    framerate (which drifts: a camera nominally at 25 fps was measured
    ~129 ms fast over an hour).

    `index_offset` is added to every returned index to absorb a known
    fixed skew between decoded-frame index and CSV-row index (the
    recorder can emit a few frames the sidecar does not cover; the
    `_encoder_active` gate in camera_base.py removes this for new
    recordings). Forward-only, so it can drive a sequential decoder.
    """
    if not frame_ts_ns:
        return []
    out: list[int] = []
    i = 0
    last = len(frame_ts_ns) - 1
    for j in range(n_out):
        target = grid_start_ns + j * grid_step_ns
        while i < last and abs(frame_ts_ns[i + 1] - target) <= abs(
            frame_ts_ns[i] - target
        ):
            i += 1
        out.append(min(max(i + index_offset, 0), last))
    return out


def read_frame_timestamps(csv_path: str) -> list[int]:
    with open(csv_path, newline="") as f:
        return [int(row["timestamp_ns"]) for row in csv.DictReader(f)]


# --------------------------------------------------------------------------- #
# ffmpeg filter construction                                                  #
# --------------------------------------------------------------------------- #


def build_align_filter(fit: SidecarFit, t_start_ns: int, out_rate: int) -> str:
    """Filtergraph for one track: reinterpret at the measured rate,
    resample to `out_rate`, then shift so sample 0 lands at
    `t_start_ns`. Positive offset -> delay; negative -> trim the head."""
    offset_ms = (fit.sample0_wall_ns - t_start_ns) / 1e6
    stages = [
        f"asetrate={fit.measured_rate_hz:.6f}",
        f"aresample={out_rate}",
        "aformat=sample_fmts=s16",
    ]
    if offset_ms >= 0.5:
        stages.append(f"adelay={round(offset_ms)}:all=1")
    elif offset_ms <= -0.5:
        stages.append(f"atrim=start={-offset_ms / 1e3:.6f}")
        stages.append("asetpts=PTS-STARTPTS")
    return ",".join(stages)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise RuntimeError(f"{cmd[0]} failed ({proc.returncode}):\n{tail}")


def _run_progress(cmd: list[str], total_s: float | None, on_frac) -> None:
    """`_run` but streams ffmpeg's own `-progress` output so a long re-encode
    reports a real 0..1 fraction via `on_frac`. Falls back to `_run` when
    there's no duration or callback to drive."""
    if not total_s or total_s <= 0 or on_frac is None:
        _run(cmd)
        return
    full = [cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]]
    proc = subprocess.Popen(
        full, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        for raw in proc.stdout:
            key, _, val = raw.strip().partition("=")
            # ffmpeg emits BOTH `out_time_us` and `out_time_ms` in every
            # progress block, and -- a long-standing ffmpeg quirk kept for
            # compat -- `out_time_ms` is actually microseconds too, not
            # milliseconds. Treating it as ms made the fraction jump to a
            # clamped 1.0 on alternate lines, so the bar flickered between
            # ~90-something % and 100 %. Both are microseconds.
            if key not in ("out_time_us", "out_time_ms"):
                continue
            try:
                on_frac(max(0.0, min(1.0, int(val) / 1e6 / total_s)))
            except (ValueError, TypeError):
                pass
    finally:
        proc.wait()
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr.read() or "").strip().splitlines()[-8:])
        raise RuntimeError(f"{cmd[0]} failed ({proc.returncode}):\n{tail}")


def _dashed_line_filters(width_expr: str, height: int, thickness: int = 3,
                         dash: int = 14, gap: int = 9,
                         colour: str = "yellow@0.85") -> str:
    """A vertical dashed line as a chain of `drawbox` segments, for the
    centred 'now' marker. `width_expr` is an ffmpeg x-position expression
    (e.g. '(W-3)/2')."""
    segs = []
    y = 0
    while y < height:
        h = min(dash, height - y)
        segs.append(
            f"drawbox=x={width_expr}:y={y}:w={thickness}:h={h}:"
            f"color={colour}:t=fill"
        )
        y += dash + gap
    return ",".join(segs)


# --------------------------------------------------------------------------- #
# Rendering                                                                   #
# --------------------------------------------------------------------------- #


def render_aligned_audio(
    stream: AudioStream, fit: SidecarFit,
    t_start_ns: int, duration_s: float, out_path: str, out_rate: int,
    progress=None,
) -> str:
    filt = build_align_filter(fit, t_start_ns, out_rate) + ",apad"
    _run_progress([
        "ffmpeg", "-y", "-i", stream.audio_path,
        "-af", filt, "-t", f"{duration_s:.6f}",
        "-c:a", "flac", out_path,
    ], duration_s, progress)
    return out_path


def render_spectrogram_png(
    aligned_path: str, out_path: str,
    size: tuple[int, int] = DEFAULT_SPECTROGRAM_SIZE,
    spec: SpectrogramOpts | None = None,
) -> str:
    spec = spec or SpectrogramOpts()
    _run([
        "ffmpeg", "-y", "-i", aligned_path,
        "-lavfi", spec.pic_filter(size[0], size[1]), out_path,
    ])
    return out_path


def render_source_spectrogram_png(
    audio_path: str, out_path: str,
    size: tuple[int, int] = DEFAULT_SPECTROGRAM_SIZE,
    spec: SpectrogramOpts | None = None,
    start_s: float = 0.0, dur_s: float = 20.0,
) -> str:
    """A static spectrogram PNG straight from a source recording, with no
    PTP alignment -- for the compose preview, where only the look (colour
    map, band, scales) matters, not sample-accurate timing. `start_s` /
    `dur_s` bound how much audio is decoded so this stays fast on a Pi."""
    spec = spec or SpectrogramOpts()
    _run([
        "ffmpeg", "-y",
        "-ss", f"{max(0.0, start_s):.3f}", "-t", f"{max(0.1, dur_s):.3f}",
        "-i", audio_path,
        "-lavfi", spec.pic_filter(size[0], size[1]), out_path,
    ])
    return out_path


def _video_width(path: str) -> int:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )
    return int(probe.stdout.strip())


def _media_duration_s(path: str) -> float:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )
    try:
        return float(probe.stdout.strip())
    except ValueError:
        return 0.0


def _strip_pixels_per_second(total_s: float) -> tuple[int, float]:
    """(strip_width_px, pixels_per_second) for a `total_s`-long recording,
    at PLAYHEAD_PPS but capped at _MAX_STRIP_PX so a long session doesn't
    ask ffmpeg for an enormous PNG."""
    total_s = max(total_s, 0.1)
    width = min(math.ceil(total_s * PLAYHEAD_PPS), _MAX_STRIP_PX)
    width = max(2, width // 2 * 2)
    return width, width / total_s


def render_overlay(
    composite_path: str, aligned_paths: list[str], out_path: str,
    strip_height: int, fps: int, spec: SpectrogramOpts | None = None,
    stacked: bool = False, progress=None, total_s: float | None = None,
) -> str:
    """A `video_compose.py` composite + a spectrogram of the first aligned
    track that scrolls with a **centred** 'now' marker (a dashed vertical
    line at mid-width; past audio to its left, upcoming audio to its
    right), plus every aligned track muxed as audio, into a `.mkv`.
    `stacked=False` overlays the strip on the bottom of the video;
    `stacked=True` puts a full spectrogram panel below it. `progress(0..1)`
    + `total_s` drive a real progress read off ffmpeg's own `-progress`."""
    spec = spec or SpectrogramOpts()
    width = _video_width(composite_path)
    if not total_s or total_s <= 0:
        total_s = _media_duration_s(composite_path) or _media_duration_s(
            aligned_paths[0] if aligned_paths else composite_path
        )
    strip_w, pps = _strip_pixels_per_second(total_s or 1.0)

    tmp_dir = os.path.dirname(out_path) or "."
    wide_png = os.path.join(tmp_dir, f".strip_{uuid.uuid4().hex[:8]}.png")
    try:
        # 1. the whole recording's spectrogram as one wide, chrome-free PNG.
        _run([
            "ffmpeg", "-y", "-i", aligned_paths[0],
            "-lavfi", spec.pic_filter_nolegend(strip_w, strip_height), wide_png,
        ])

        # 2. pan that PNG so the column for output time `t` sits at mid-width,
        #    over a dark backing, with a dashed centre line.
        dashes = _dashed_line_filters(f"({width}-3)/2", strip_height)
        strip_chain = (
            f"color=c=0x101014:s={width}x{strip_height}:r={fps}[bg];"
            f"[bg][1:v]overlay=x='{width}/2-{pps:.4f}*t':y=0:"
            f"eof_action=pass:shortest=1[scan];"
            f"[scan]{dashes}[strip]"
        )
        if stacked:
            graph = f"{strip_chain};[0:v][strip]vstack=inputs=2:shortest=1[v]"
        else:
            graph = f"{strip_chain};[0:v][strip]overlay=0:H-h:shortest=1[v]"

        cmd = ["ffmpeg", "-y", "-i", composite_path, "-loop", "1", "-i", wide_png]
        for path in aligned_paths:
            cmd += ["-i", path]
        cmd += ["-filter_complex", graph, "-map", "[v]"]
        for i in range(len(aligned_paths)):
            cmd += ["-map", f"{i + 2}:a"]     # 0=composite, 1=wide PNG
        cmd += ["-shortest", "-c:v", "libx264", "-crf", "20",
                "-c:a", "flac", out_path]
        _run_progress(cmd, total_s, progress)
    finally:
        try:
            os.remove(wide_png)
        except OSError:
            pass
    return out_path


def render_muxed_track(
    composite_path: str, aligned_path: str, out_path: str,
) -> str:
    """The composite, unchanged, with one aligned audio track muxed in."""
    _run([
        "ffmpeg", "-y", "-i", composite_path, "-i", aligned_path,
        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
        "-b:a", "192k", "-movflags", "+faststart", out_path,
    ])
    return out_path


def render_ethogram(
    date_dir: str, aligned_flac: str, window_start_ns: int, window_dur_s: float,
    out_path: str, fps: int = 15, strip_height: int = DEFAULT_STRIP_HEIGHT,
    spec: SpectrogramOpts | None = None, panel_width: int = 800,
    index_offset: int = 0,
) -> str:
    """Camera footage on top, a scrolling spectrogram of the aligned audio
    below with a "now" line, for scrubbing a session to check that a sound
    and the behaviour that made it line up.

    The top panel is built frame-by-`timestamp_ns` (via `map_grid_to_frames`)
    rather than at a constant framerate, so it stays locked to the
    rate-corrected audio across the whole window instead of drifting.
    OpenCV is imported lazily so the rest of this module has no cv2 dep.
    """
    import cv2  # noqa: PLC0415  -- optional heavy dep, only this path needs it

    spec = spec or SpectrogramOpts(gain=2.5)
    cam_csvs = _camera_timestamp_csvs(date_dir)
    if not cam_csvs:
        raise ValueError(f"No camera timestamp CSV under {date_dir} for the ethogram")
    csv_path = cam_csvs[0]
    module_dir = os.path.dirname(csv_path)
    videos = sorted(
        glob.glob(os.path.join(module_dir, "*.ts"))
        + glob.glob(os.path.join(module_dir, "*.mp4"))
    )
    if not videos:
        raise ValueError(f"No video file next to {csv_path}")

    frame_ts = read_frame_timestamps(csv_path)
    step_ns = int(1e9 / fps)
    n_out = int(window_dur_s * fps)
    want = map_grid_to_frames(frame_ts, window_start_ns, step_ns, n_out, index_offset)

    cap = cv2.VideoCapture(videos[0])
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or panel_width
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or panel_width
    pane_h = round(panel_width * src_h / src_w / 2) * 2

    tmp_top = os.path.splitext(out_path)[0] + "_top.mp4"
    writer = cv2.VideoWriter(
        tmp_top, cv2.VideoWriter_fourcc(*"mp4v"), fps, (panel_width, pane_h)
    )
    try:
        decoded_idx, frame = -1, None
        for target in want:
            while decoded_idx < target:
                ok, frame = cap.read()
                if not ok:
                    break
                decoded_idx += 1
            if frame is None:
                break
            writer.write(cv2.resize(frame, (panel_width, pane_h)))
    finally:
        writer.release()
        cap.release()

    graph = (
        f"[0:v]scale={panel_width}:-2,setsar=1[top];"
        f"[1:a]{spec.scroll_filter(panel_width, strip_height, fps)}[sp];"
        f"[sp]drawbox=x={panel_width - 3}:y=0:w=3:h={strip_height}:"
        f"color=yellow@0.9:t=fill[spec];"
        # shortest=1 (+ -shortest): the scrolling spectrum outlives the
        # frame-exact top panel by a fraction of a second, which trips
        # `Assertion best_input >= 0` in ffmpeg 6.x -- stop with the video.
        f"[top][spec]vstack=inputs=2:shortest=1[v]"
    )
    _run([
        "ffmpeg", "-y", "-i", tmp_top, "-i", aligned_flac,
        "-filter_complex", graph, "-map", "[v]", "-map", "1:a",
        "-r", str(fps), "-shortest",
        "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-g", str(fps * 3), "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", out_path,
    ])
    os.remove(tmp_top)
    return out_path


def _composite_fps(composite_path: str) -> int:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", composite_path],
        capture_output=True, text=True, check=True,
    )
    num, _, den = probe.stdout.strip().partition("/")
    return max(1, round(float(num) / float(den or 1)))


# --------------------------------------------------------------------------- #
# Orchestration                                                              #
# --------------------------------------------------------------------------- #


def align_session_audio(
    date_dir: str, opts: AlignOptions,
    t_start_ns: int | None = None, duration_s: float | None = None,
) -> list[dict]:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe must be on PATH for the audio path")

    streams = discover_audio_streams(date_dir)
    if not streams:
        raise ValueError(
            f"No audio streams (audio file + *_timestamps.txt) found under {date_dir}"
        )

    fits = [
        parse_mic_sidecar(s.sidecar_path, s.audio_path, opts.frame_num)
        for s in streams
    ]
    window_start, window_dur = resolve_window(date_dir, fits, t_start_ns, duration_s)
    if window_dur <= 0:
        raise ValueError("Resolved a non-positive alignment window")

    ptp_summary = None
    ptp_path = opts.ptp_history or discover_ptp_history(date_dir)
    if ptp_path and os.path.isfile(ptp_path):
        ptp_summary = summarise_ptp_window(
            ptp_path, window_start, window_start + int(window_dur * 1e9),
        )

    os.makedirs(opts.out_dir, exist_ok=True)
    session_name = os.path.basename(os.path.dirname(os.path.normpath(date_dir)))

    results: list[dict] = []
    aligned_paths: list[str] = []
    for stream, fit in zip(streams, fits, strict=True):
        out_rate = opts.out_rate or fit.nominal_rate_hz
        base = os.path.join(opts.out_dir, f"{session_name}_{stream.label}")
        aligned = render_aligned_audio(
            stream, fit, window_start, window_dur, f"{base}_aligned.flac", out_rate,
        )
        aligned_paths.append(aligned)

        report = fit.as_report()
        report["label"] = stream.label
        report["window_start_ns"] = window_start
        report["window_duration_s"] = round(window_dur, 3)
        report["offset_from_window_ms"] = round(
            (fit.sample0_wall_ns - window_start) / 1e6, 2)
        report["ptp_during_recording"] = ptp_summary
        report["aligned_path"] = aligned
        with open(f"{base}_align.json", "w") as f:
            json.dump(report, f, indent=2)

        if opts.spectrogram:
            report["spectrogram_path"] = render_spectrogram_png(
                aligned, f"{base}_spectrogram.png", spec=opts.spec,
            )
        if opts.ethogram:
            report["ethogram_path"] = render_ethogram(
                date_dir, aligned, window_start, window_dur,
                f"{base}_ethogram.mp4", fps=opts.ethogram_fps,
                strip_height=opts.strip_height, spec=opts.spec,
                index_offset=opts.frame_index_offset,
            )
        results.append(report)

    if opts.overlay_path:
        overlay_out = os.path.splitext(opts.overlay_path)[0] + "_with_audio.mkv"
        render_overlay(
            opts.overlay_path, aligned_paths, overlay_out,
            opts.strip_height, _composite_fps(opts.overlay_path), spec=opts.spec,
        )
        for report in results:
            report["overlay_path"] = overlay_out

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("date_dir", help="Session date dir, e.g. .../session/20260703")
    parser.add_argument("--out-dir", default=None,
                        help="Output directory (default: the session directory)")
    parser.add_argument("--t-start-ns", type=int, default=None,
                        help="Force the window start (default: from camera CSVs)")
    parser.add_argument("--duration-s", type=float, default=None,
                        help="Force the window duration in seconds")
    parser.add_argument("--out-rate", type=int, default=None,
                        help="Output sample rate (default: the recording's own rate)")
    parser.add_argument("--fmax", type=int, default=None, dest="spec_fmax",
                        help="Upper frequency limit for spectrograms, Hz")
    parser.add_argument("--fmin", type=int, default=0, dest="spec_fmin",
                        help="Lower frequency limit for spectrograms, Hz")
    parser.add_argument("--spec-color", default="intensity", choices=SPEC_COLORS,
                        help="Spectrogram colour map")
    parser.add_argument("--spec-fscale", default="lin", choices=SPEC_FSCALES,
                        help="Spectrogram frequency-axis scale")
    parser.add_argument("--spec-ascale", default="log", choices=SPEC_ASCALES,
                        help="Spectrogram amplitude scale")
    parser.add_argument("--spec-gain", type=float, default=1.0,
                        help="Spectrogram gain (0.1-20)")
    parser.add_argument("--spectrogram", action="store_true",
                        help="Also write a whole-session spectrogram PNG per mic")
    parser.add_argument("--overlay", default=None, dest="overlay_path",
                        help="video_compose.py output to add a spectrogram strip to")
    parser.add_argument("--strip-height", type=int, default=DEFAULT_STRIP_HEIGHT,
                        help="Overlay spectrogram strip height in px")
    parser.add_argument("--frame-num", type=int, default=DEFAULT_FRAME_NUM,
                        help="Samples per sidecar block (microphone.frame_num)")
    parser.add_argument("--ptp-history", default=None,
                        help="Controller ptp_history.csv to fold PTP quality "
                             "for the recording window into the report")
    parser.add_argument("--ethogram", action="store_true",
                        help="Also render camera-over-spectrogram scrub video "
                             "(needs OpenCV); frames placed by real timestamp")
    parser.add_argument("--ethogram-fps", type=int, default=15,
                        help="Output framerate for --ethogram")
    parser.add_argument("--frame-index-offset", type=int, default=0,
                        help="Fixed decoded-frame vs CSV-row skew for --ethogram")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    session_dir = os.path.dirname(os.path.normpath(args.date_dir))
    opts = AlignOptions(
        out_dir=args.out_dir or session_dir,
        out_rate=args.out_rate,
        spectrogram=args.spectrogram,
        overlay_path=args.overlay_path,
        strip_height=args.strip_height,
        frame_num=args.frame_num,
        ptp_history=args.ptp_history,
        ethogram=args.ethogram,
        ethogram_fps=args.ethogram_fps,
        frame_index_offset=args.frame_index_offset,
        spec=SpectrogramOpts(
            color=args.spec_color, fmin_hz=args.spec_fmin, fmax_hz=args.spec_fmax,
            fscale=args.spec_fscale, ascale=args.spec_ascale, gain=args.spec_gain,
        ),
    )
    results = align_session_audio(
        args.date_dir, opts, t_start_ns=args.t_start_ns, duration_s=args.duration_s,
    )
    for report in results:
        print(f"{report['label']}: {report['aligned_path']}  "
              f"(offset {report['offset_from_window_ms']:+.1f} ms, "
              f"rate {report['measured_rate_hz']} Hz / "
              f"{report['rate_error_ppm']:+.1f} ppm, "
              f"residual p95 {report['residual_p95_ms']} ms, "
              f"{report['n_outliers']} outliers)")


if __name__ == "__main__":
    main()
