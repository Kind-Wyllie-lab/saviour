"""
Post-hoc sync-quality validation for a recorded session.

Once every file a session produced is on the controller share, the
controller runs this against the session's per-camera ``*_timestamps.csv``
and per-module ``*_health_metadata_*.csv`` files and produces a compact
green / amber / red verdict:

* **framesync** (cameras with ``camera.sync_mode`` server/client): inter-camera
  offset, clock drift, detrended p95 jitter, dropped frames -- the analysis
  ``tools/analyse_framesync.py`` does, ported to stdlib + numpy so it runs on
  the controller (which, unlike ``tools/``, has no pandas).
* **PTP** (every session): ptp4l / phc2sys offset percentiles from the health
  metadata, the check ``tools/make_aligned_video.check_ptp`` does.
* **capture-rate stability** (every camera): real vs nominal fps, gap CV.

For a Habitat Session (one session, many ``YYYYMMDD`` day dirs) the unit of
validation is one day dir -- see :func:`check_session_day`. A plain session
is validated as a whole via :func:`check_session`.

This module has the same dependency discipline as ``compose.py``: stdlib
plus numpy, nothing heavier. :class:`SyncCheckWorker` is a single-slot
background runner in the mould of ``compose.ComposeWorker``.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

import numpy as np

# --------------------------------------------------------------------------- #
# Constants / thresholds                                                      #
# --------------------------------------------------------------------------- #

REPORT_FILENAME = "framesync_report.json"
SCHEMA = 1
TOOL_VERSION = "framesync_check/1"
MAX_CAMERAS = 40
RATE_HEAD_ROWS = 50_000          # contiguous, un-strided, for fps / gap CV
MAX_QUEUE = 8

# Every value is overridable via config.recording.<key>; web._framesync_thresholds
# overlays the config on top of this dict.
DEFAULT_THRESHOLDS: dict = {
    # PTP offset (all sessions). The green line reuses recording.ptp_start_gate_us.
    "ptp_gate_us": 50.0,
    "ptp_amber_us": 250.0,
    # Inter-camera framesync jitter, after phase + drift removal (server/client only).
    "framesync_detrended_p95_green_us": 20.0,
    "framesync_detrended_p95_amber_us": 200.0,
    # Clock drift, as the fraction of one half-frame it accumulates over the run.
    "framesync_drift_accum_halfframe_frac_amber": 0.5,
    "framesync_drift_accum_halfframe_frac_red": 1.0,
    # Dropped frames, as a fraction of that camera's frames.
    "framesync_dropped_frac_amber": 0.001,
    "framesync_dropped_frac_red": 0.02,
    # Per-camera capture-rate stability.
    "framesync_rate_cv_amber": 0.05,
    "framesync_fps_dev_amber": 0.02,
    # Fraction of frames whose match landed a whole frame period away.
    "framesync_artefact_frac_amber": 0.05,
    # Memory bounds -- adaptive-decimation sample caps per stream.
    "framesync_cap_rows": 500_000,
    "framesync_habitat_cap_rows": 200_000,
    "framesync_health_cap_rows": 200_000,
    # Habitat day-completeness: hours past UTC midnight before a day dir counts
    # as settled (a segment straddling midnight can still land in it).
    "framesync_day_settle_hours": 1,
}

_STATUS_ORDER = {"green": 0, "amber": 1, "red": 2}
_DAY_RE = re.compile(r"^\d{8}$")


class SyncCheckError(ValueError):
    """Bad request -- surfaced to the caller verbatim (mirrors ComposeError)."""


# --------------------------------------------------------------------------- #
# Result dataclasses                                                          #
# --------------------------------------------------------------------------- #


@dataclass
class PtpSummary:
    module: str
    n: int
    ptp4l_p50_ns: float | None = None
    ptp4l_p95_ns: float | None = None
    ptp4l_max_ns: float | None = None
    phc2sys_p50_ns: float | None = None
    phc2sys_p95_ns: float | None = None
    phc2sys_max_ns: float | None = None
    column_used: str = "none"     # phc2sys_offset_ns | phc2sys_offset | none | mixed


@dataclass
class CameraSync:
    name: str
    sync_mode: str
    nominal_fps: float | None
    real_fps: float | None
    n_frames: int
    n_sampled: int
    stride: int
    dropped_frames: int
    rate_cv: float | None
    fps_dev: float | None
    first_ns: int | None
    last_ns: int | None
    ts_ns: np.ndarray            # sampled, sorted int64 (not serialised)
    ptp: PtpSummary | None
    notes: list = field(default_factory=list)


@dataclass
class PairOffset:
    ref: str
    client: str
    n_frames: int
    n_artefacts: int
    mean_offset_us: float | None = None
    std_offset_us: float | None = None
    p50_offset_us: float | None = None
    p95_offset_us: float | None = None
    max_abs_offset_us: float | None = None
    pct_within_half_frame: float | None = None
    drift_us_per_sec: float | None = None
    detrended_p95_us: float | None = None


# --------------------------------------------------------------------------- #
# Small helpers                                                               #
# --------------------------------------------------------------------------- #


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_name(text: str) -> bool:
    return bool(text) and all(c.isalnum() or c in "_-" for c in text)


def _safe_segment(text: str) -> bool:
    return bool(text) and "/" not in text and "\\" not in text and ".." not in text


def _parse_float(row: list, idx: int | None) -> float | None:
    if idx is None or idx >= len(row):
        return None
    try:
        return float(row[idx])
    except (ValueError, TypeError):
        return None


def _max_opt(values) -> float | None:
    vals = [v for v in values if v is not None]
    return max(vals) if vals else None


def _rollup(statuses) -> str:
    real = [s for s in statuses if s in _STATUS_ORDER]
    if not real:
        return "error" if "error" in statuses else "skipped"
    return max(real, key=lambda s: _STATUS_ORDER[s])


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serialisable: {type(o)}")


class _Reservoir:
    """Bounded, single-pass sampler. Appends every value until it holds
    ``2 * cap``; then keeps every 2nd in place, doubles the stride, and
    samples 1-in-stride from there on. Ends with ``cap..2*cap`` samples with
    no preallocation -- so a multi-million-row CSV costs a fixed amount of
    memory and one read."""

    __slots__ = ("_since", "buf", "cap", "stride")

    def __init__(self, cap: int):
        self.cap = max(1, int(cap))
        self.buf: list = []
        self.stride = 1
        self._since = 0

    def add(self, value) -> None:
        self._since += 1
        if self._since < self.stride:
            return
        self._since = 0
        self.buf.append(value)
        if len(self.buf) >= 2 * self.cap:
            self.buf = self.buf[::2]
            self.stride *= 2

    def array(self, dtype) -> np.ndarray:
        return np.asarray(self.buf, dtype=dtype)


# --------------------------------------------------------------------------- #
# CSV loaders (bounded memory)                                                #
# --------------------------------------------------------------------------- #


def _segment_key(path: str) -> tuple:
    m = re.search(r"_\((\d+)_", os.path.basename(path))
    return (int(m.group(1)) if m else 1 << 30, os.path.basename(path))


def _timestamp_csvs(module_dir: str) -> list[str]:
    files = sorted(
        glob.glob(os.path.join(module_dir, "*_timestamps.csv")), key=_segment_key
    )
    recovered = os.path.join(
        os.path.dirname(module_dir), "_recovered", os.path.basename(module_dir)
    )
    if os.path.isdir(recovered):
        files += sorted(
            glob.glob(os.path.join(recovered, "*_timestamps.csv")), key=_segment_key
        )
    return files


def _health_csvs(module_dir: str) -> list[str]:
    files = sorted(glob.glob(os.path.join(module_dir, "*_health_metadata_*.csv")))
    recovered = os.path.join(
        os.path.dirname(module_dir), "_recovered", os.path.basename(module_dir)
    )
    if os.path.isdir(recovered):
        files += sorted(glob.glob(os.path.join(recovered, "*_health_metadata_*.csv")))
    return files


def _load_camera_timestamps(module_dir: str, cap_rows: int):
    """One streaming pass over every ``*_timestamps.csv`` segment for a camera.

    Returns ``(ts_ns sorted ndarray, n_total, dropped_sum, stride, head_ts,
    head_delta_ms, bad_rows)``. ``head_*`` are the first RATE_HEAD_ROWS rows,
    un-strided, for fps / gap-CV (striding corrupts inter-frame gaps)."""
    res = _Reservoir(cap_rows)
    n_total = dropped_sum = bad = 0
    head_ts: list[int] = []
    head_delta_ms: list[float] = []

    for path in _timestamp_csvs(module_dir):
        try:
            f = open(path, newline="")
        except OSError:
            continue
        with f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header or "timestamp_ns" not in header:
                continue
            i_ts = header.index("timestamp_ns")
            i_drop = (
                header.index("dropped_before") if "dropped_before" in header else None
            )
            i_delta = header.index("delta_ms") if "delta_ms" in header else None
            for row in reader:
                if not row:
                    continue
                v = _parse_float(row, i_ts)
                if v is None or v <= 0:
                    bad += 1
                    continue
                ts = int(v)
                n_total += 1
                if i_drop is not None:
                    d = _parse_float(row, i_drop)
                    if d and d > 0:
                        dropped_sum += int(d)
                if len(head_ts) < RATE_HEAD_ROWS:
                    head_ts.append(ts)
                    dm = _parse_float(row, i_delta)
                    if dm is not None:
                        head_delta_ms.append(dm)
                res.add(ts)

    ts_arr = res.array(np.int64)
    if ts_arr.size:
        # A segment that straddles UTC midnight is filed under the *next*
        # day's dir, so segment order != chronological order -- sort.
        ts_arr.sort()
    return ts_arr, n_total, dropped_sum, res.stride, head_ts, head_delta_ms, bad


def _load_health_series(module_dir: str, cap_rows: int) -> PtpSummary | None:
    csvs = _health_csvs(module_dir)
    if not csvs:
        return None
    p4 = _Reservoir(cap_rows)
    ph = _Reservoir(cap_rows)
    n = 0
    col_used = "none"

    for path in csvs:
        try:
            f = open(path, newline="")
        except OSError:
            continue
        with f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                continue
            i_p4 = (
                header.index("ptp4l_offset_ns") if "ptp4l_offset_ns" in header else None
            )
            if "phc2sys_offset_ns" in header:
                i_ph, this_col = header.index("phc2sys_offset_ns"), "phc2sys_offset_ns"
            elif "phc2sys_offset" in header:          # pre-2026 health schema
                i_ph, this_col = header.index("phc2sys_offset"), "phc2sys_offset"
            else:
                i_ph, this_col = None, "none"
            if this_col != "none":
                col_used = this_col if col_used in ("none", this_col) else "mixed"
            for row in reader:
                if not row:
                    continue
                n += 1
                v = _parse_float(row, i_p4)
                if v is not None:
                    p4.add(abs(v))
                v = _parse_float(row, i_ph)
                if v is not None:
                    ph.add(abs(v))

    def pcts(res: _Reservoir):
        if not res.buf:
            return None, None, None
        a = res.array(np.float64)
        return (
            float(np.percentile(a, 50)),
            float(np.percentile(a, 95)),
            float(a.max()),
        )

    p4_50, p4_95, p4_max = pcts(p4)
    ph_50, ph_95, ph_max = pcts(ph)
    return PtpSummary(
        module=os.path.basename(module_dir.rstrip("/\\")),
        n=n,
        ptp4l_p50_ns=p4_50, ptp4l_p95_ns=p4_95, ptp4l_max_ns=p4_max,
        phc2sys_p50_ns=ph_50, phc2sys_p95_ns=ph_95, phc2sys_max_ns=ph_max,
        column_used=col_used,
    )


def _read_camera_config(module_dir: str) -> dict:
    """``{sync_mode, fps}`` from the module's exported ``config.json``."""
    out: dict = {}
    path = os.path.join(module_dir, "config.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return out
    cam = cfg.get("camera") if isinstance(cfg, dict) else None
    if isinstance(cam, dict):
        if isinstance(cam.get("sync_mode"), str):
            out["sync_mode"] = cam["sync_mode"]
        for key in ("fps", "framerate"):
            try:
                out["fps"] = float(cam[key])
                break
            except (KeyError, TypeError, ValueError):
                pass
    return out


def _rate_stats(head_ts, head_delta_ms, nominal_fps):
    real_fps = rate_cv = fps_dev = None
    if len(head_ts) >= 3:
        gaps = np.diff(np.asarray(head_ts, dtype=np.int64))
        gaps = gaps[gaps > 0]
        if gaps.size:
            med = float(np.median(gaps))
            if med > 0:
                real_fps = 1e9 / med
                rate_cv = float(np.std(gaps) / np.mean(gaps))
    if real_fps is None and len(head_delta_ms) >= 3:
        dm = np.asarray(head_delta_ms, dtype=np.float64)
        dm = dm[dm > 0]
        if dm.size:
            med = float(np.median(dm))
            if med > 0:
                real_fps = 1000.0 / med
                rate_cv = float(np.std(dm) / np.mean(dm))
    if real_fps and nominal_fps:
        fps_dev = abs(real_fps - nominal_fps) / nominal_fps
    return real_fps, rate_cv, fps_dev


# --------------------------------------------------------------------------- #
# Inter-camera alignment                                                      #
# --------------------------------------------------------------------------- #


def _align_offsets(cameras: list[CameraSync], fps: float) -> list[PairOffset]:
    """Nearest-neighbour match every synced camera against the one with the
    most frames, on ``timestamp_ns``. Port of ``analyse_framesync.align_frames``
    + ``report_offsets``, with the drift fit against **elapsed seconds** (not
    frame index) so it is invariant to the reservoir stride and to fps."""
    usable = [c for c in cameras if c.ts_ns is not None and c.ts_ns.size >= 2]
    if len(usable) < 2:
        return []
    ref = max(usable, key=lambda c: c.ts_ns.size)
    ref_ts = ref.ts_ns.astype(np.float64)
    outlier_thr_us = (1e6 / fps * 0.9) if fps else 1e6
    half_frame_us = (1e6 / fps / 2) if fps else None

    pairs: list[PairOffset] = []
    for c in usable:
        if c is ref:
            continue
        cam_ts = c.ts_ns
        idx = np.clip(np.searchsorted(cam_ts, ref.ts_ns), 0, len(cam_ts) - 1)
        lo = np.clip(idx - 1, 0, len(cam_ts) - 1)
        pick = np.where(
            np.abs(cam_ts[idx] - ref.ts_ns) <= np.abs(cam_ts[lo] - ref.ts_ns), idx, lo
        )
        delta_us = (cam_ts[pick].astype(np.float64) - ref_ts) / 1e3
        real_mask = np.abs(delta_us) < outlier_thr_us
        real = delta_us[real_mask]
        n_real = int(real.size)
        n_art = int(delta_us.size - n_real)

        drift = detr_p95 = None
        if n_real >= 10:
            x = (ref_ts[real_mask] - float(ref_ts[real_mask][0])) / 1e9
            if x[-1] > x[0]:
                slope, intercept = np.polyfit(x, real, 1)
                drift = float(slope)                       # µs per second
                detr_p95 = float(
                    np.percentile(np.abs(real - (slope * x + intercept)), 95)
                )
        within = (
            float(np.mean(np.abs(real) <= half_frame_us) * 100)
            if (half_frame_us and n_real)
            else None
        )
        pairs.append(PairOffset(
            ref=ref.name, client=c.name, n_frames=n_real, n_artefacts=n_art,
            mean_offset_us=float(np.mean(real)) if n_real else None,
            std_offset_us=float(np.std(real)) if n_real else None,
            p50_offset_us=float(np.percentile(np.abs(real), 50)) if n_real else None,
            p95_offset_us=float(np.percentile(np.abs(real), 95)) if n_real else None,
            max_abs_offset_us=float(np.max(np.abs(real))) if n_real else None,
            pct_within_half_frame=within,
            drift_us_per_sec=drift, detrended_p95_us=detr_p95,
        ))
    return pairs


# --------------------------------------------------------------------------- #
# Classification                                                              #
# --------------------------------------------------------------------------- #


def classify(cameras: list[CameraSync], pairs: list[PairOffset], thr: dict,
             phase_lock_evaluated: bool, span_s: float, fps: float):
    """-> (status, reasons). status = red if any red, else amber if any amber,
    else green. Reasons are ordered reds-then-ambers."""
    reds: list[str] = []
    ambers: list[str] = []
    half_frame_us = (1e6 / fps / 2) if fps else None

    # ---- PTP, per camera ------------------------------------------------ #
    for c in cameras:
        p = c.ptp
        if p is None:
            ambers.append(f"{c.name}: no health metadata for the PTP check")
            continue
        for label, p95_ns in (("ptp4l", p.ptp4l_p95_ns), ("phc2sys", p.phc2sys_p95_ns)):
            if p95_ns is None:
                ambers.append(f"{c.name}: no {label} offset data")
                continue
            p95_us = p95_ns / 1e3
            if p95_us >= thr["ptp_amber_us"]:
                reds.append(f"{c.name}: {label} offset p95 {p95_us:.0f} µs "
                            f"(≥ {thr['ptp_amber_us']:.0f} µs)")
            elif p95_us >= thr["ptp_gate_us"]:
                ambers.append(f"{c.name}: {label} offset p95 {p95_us:.0f} µs "
                              f"(≥ {thr['ptp_gate_us']:.0f} µs gate)")
        if p.column_used in ("phc2sys_offset", "mixed"):
            ambers.append(
                f"{c.name}: health CSV uses the pre-2026 phc2sys_offset column"
            )

    # ---- capture-rate stability, per camera --------------------------- #
    for c in cameras:
        if c.n_frames == 0:
            ambers.append(f"{c.name}: video present but no frame timestamps")
            continue
        dropped_frac = c.dropped_frames / max(c.n_frames, 1)
        if dropped_frac > thr["framesync_dropped_frac_red"]:
            reds.append(f"{c.name}: {dropped_frac * 100:.1f}% dropped frames")
        elif dropped_frac > thr["framesync_dropped_frac_amber"]:
            ambers.append(f"{c.name}: {dropped_frac * 100:.2f}% dropped frames")
        if c.rate_cv is not None and c.rate_cv > thr["framesync_rate_cv_amber"]:
            ambers.append(f"{c.name}: unstable capture rate (gap CV {c.rate_cv:.3f})")
        if c.fps_dev is not None and c.fps_dev > thr["framesync_fps_dev_amber"]:
            ambers.append(
                f"{c.name}: real {c.real_fps:.2f} fps is {c.fps_dev * 100:.1f}% "
                f"off the configured {c.nominal_fps:.2f}"
            )
        for note in c.notes:
            ambers.append(f"{c.name}: {note}")

    # ---- inter-camera framesync (synced cameras only) ---------------- #
    if phase_lock_evaluated and pairs and half_frame_us:
        for pr in pairs:
            if pr.detrended_p95_us is not None:
                if pr.detrended_p95_us > thr["framesync_detrended_p95_amber_us"]:
                    reds.append(f"{pr.client} vs {pr.ref}: detrended p95 "
                                f"{pr.detrended_p95_us:.0f} µs")
                elif pr.detrended_p95_us > thr["framesync_detrended_p95_green_us"]:
                    ambers.append(f"{pr.client} vs {pr.ref}: detrended p95 "
                                  f"{pr.detrended_p95_us:.0f} µs")
            if pr.drift_us_per_sec is not None and span_s:
                accum = abs(pr.drift_us_per_sec) * span_s
                red_lim = half_frame_us * thr[
                    "framesync_drift_accum_halfframe_frac_red"]
                amb_lim = half_frame_us * thr[
                    "framesync_drift_accum_halfframe_frac_amber"]
                if accum > red_lim:
                    reds.append(f"{pr.client} vs {pr.ref}: clock drift accumulates "
                                f"{accum / 1000:.1f} ms over the run (> half a frame)")
                elif accum > amb_lim:
                    ambers.append(f"{pr.client} vs {pr.ref}: clock drift accumulates "
                                  f"{accum / 1000:.1f} ms over the run")
            total = pr.n_frames + pr.n_artefacts
            if total and pr.n_artefacts / total > thr["framesync_artefact_frac_amber"]:
                ambers.append(f"{pr.client} vs {pr.ref}: "
                              f"{pr.n_artefacts / total * 100:.0f}% of frames matched "
                              "more than a frame period away")

    status = "red" if reds else "amber" if ambers else "green"
    return status, reds + ambers


# --------------------------------------------------------------------------- #
# Verdict assembly                                                            #
# --------------------------------------------------------------------------- #


def _skeleton(status: str, scope: str, date_dir: str | None,
              session_name: str | None, generated_at: str) -> dict:
    return {
        "schema": SCHEMA,
        "status": status,
        "scope": scope,
        "session_name": session_name,
        "date_dir": date_dir,
        "generated_at": generated_at,
        "generated_by": TOOL_VERSION,
        "sync_mode": "none",
        "phase_lock_evaluated": False,
        "reasons": [],
        "worst": {},
        "counts": {},
        "report_rel": None,
    }


def _camera_dict(c: CameraSync) -> dict:
    d = asdict(c)
    d.pop("ts_ns", None)
    if c.ptp is not None:
        d["ptp"] = asdict(c.ptp)
    return d


def check_session_day(date_dir: str, *, thresholds: dict | None = None,
                      cap_rows: int | None = None, health_cap_rows: int | None = None,
                      progress=None, logger=None,
                      session_name: str | None = None) -> dict:
    """Validate one ``YYYYMMDD`` directory. Pure: reads CSVs, returns the
    verdict dict; never writes and never raises for thin data
    (``status="skipped"`` + a reason)."""
    thr = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    cap = cap_rows or thr["framesync_cap_rows"]
    hcap = health_cap_rows or thr["framesync_health_cap_rows"]
    date_base = os.path.basename(os.path.normpath(date_dir))
    report = progress or (lambda *a, **k: None)
    generated_at = _utcnow()

    try:
        entries = sorted(
            e for e in os.listdir(date_dir)
            if not e.startswith(".") and os.path.isdir(os.path.join(date_dir, e))
        )
    except OSError as exc:
        v = _skeleton("error", "day", date_base, session_name, generated_at)
        v["reasons"] = [f"cannot read {date_base}: {exc}"]
        return v

    cam_dirs: list[tuple[str, str]] = []
    health_only: list[tuple[str, str]] = []
    recovered_present = False
    for e in entries:
        if e == "_recovered":
            recovered_present = True
            continue
        md = os.path.join(date_dir, e)
        if glob.glob(os.path.join(md, "*_timestamps.csv")):
            cam_dirs.append((e, md))
        elif glob.glob(os.path.join(md, "*_health_metadata_*.csv")):
            health_only.append((e, md))

    truncated = len(cam_dirs) > MAX_CAMERAS
    cameras: list[CameraSync] = []
    for i, (name, md) in enumerate(cam_dirs[:MAX_CAMERAS]):
        report(i / max(len(cam_dirs), 1), f"reading {name}")
        (ts_arr, n_total, dropped, stride,
         head_ts, head_dm, bad) = _load_camera_timestamps(md, cap)
        cfg = _read_camera_config(md)
        real_fps, rate_cv, fps_dev = _rate_stats(head_ts, head_dm, cfg.get("fps"))
        ptp = _load_health_series(md, hcap)
        notes: list[str] = []
        denom = n_total + bad
        if denom and bad / denom > 0.05:
            notes.append(f"{bad / denom * 100:.0f}% of timestamp rows were unparseable")
        if n_total == 0:
            notes.append("frame timestamps CSV present but empty")
        cameras.append(CameraSync(
            name=name, sync_mode=cfg.get("sync_mode", "none"),
            nominal_fps=cfg.get("fps"), real_fps=real_fps,
            n_frames=n_total, n_sampled=int(ts_arr.size), stride=stride,
            dropped_frames=dropped, rate_cv=rate_cv, fps_dev=fps_dev,
            first_ns=int(ts_arr[0]) if ts_arr.size else None,
            last_ns=int(ts_arr[-1]) if ts_arr.size else None,
            ts_ns=ts_arr, ptp=ptp, notes=notes,
        ))

    health_mods = [p for _n, md in health_only if (p := _load_health_series(md, hcap))]

    if not cameras:
        v = _skeleton("skipped", "day", date_base, session_name, generated_at)
        v["reasons"] = ["no camera streams with frame timestamps in this day"]
        v["counts"] = {
            "cameras": 0, "cameras_synced": 0,
            "health_modules": len(health_mods),
            "recovered_present": recovered_present,
        }
        v["health_modules"] = [asdict(p) for p in health_mods]
        return v

    synced = [c for c in cameras if c.sync_mode in ("server", "client")]
    phase_lock_evaluated = len(synced) >= 2
    fps_pool = [
        c.real_fps or c.nominal_fps
        for c in cameras
        if (c.real_fps or c.nominal_fps)
    ]
    fps = float(np.median(fps_pool)) if fps_pool else 30.0

    report(0.9, "aligning cameras")
    pairs = _align_offsets(synced, fps) if phase_lock_evaluated else []
    spans = [
        (c.last_ns - c.first_ns) / 1e9
        for c in cameras
        if c.first_ns and c.last_ns and c.last_ns > c.first_ns
    ]
    span_s = max(spans) if spans else 0.0

    if not synced:
        sync_mode = "none"
    elif len(synced) != len(cameras):
        sync_mode = "mixed"
    else:
        sync_mode = synced[0].sync_mode

    status, reasons = classify(cameras, pairs, thr, phase_lock_evaluated, span_s, fps)

    worst = {
        "ptp4l_p95_ns": _max_opt(c.ptp.ptp4l_p95_ns for c in cameras if c.ptp),
        "phc2sys_p95_ns": _max_opt(c.ptp.phc2sys_p95_ns for c in cameras if c.ptp),
        "inter_camera_p95_us": _max_opt(p.p95_offset_us for p in pairs),
        "detrended_p95_us": _max_opt(p.detrended_p95_us for p in pairs),
        "drift_us_per_sec": _max_opt(
            abs(p.drift_us_per_sec) for p in pairs if p.drift_us_per_sec is not None
        ),
        "max_dropped_frac": _max_opt(
            c.dropped_frames / max(c.n_frames, 1) for c in cameras
        ),
        "span_s": round(span_s, 1),
    }
    counts = {
        "cameras": len(cameras),
        "cameras_synced": len(synced),
        "health_modules": len([c for c in cameras if c.ptp]) + len(health_mods),
        "recovered_present": recovered_present,
        "cameras_truncated": truncated,
    }

    v = _skeleton(status, "day", date_base, session_name, generated_at)
    v.update({
        "sync_mode": sync_mode,
        "phase_lock_evaluated": phase_lock_evaluated,
        "reasons": reasons,
        "worst": worst,
        "counts": counts,
        "cameras": [_camera_dict(c) for c in cameras],
        "health_modules": [asdict(p) for p in health_mods],
        "pairs": [asdict(p) for p in pairs],
        "thresholds_used": thr,
    })
    return v


def check_session(session_dir: str, *, thresholds: dict | None = None,
                  cap_rows: int | None = None, health_cap_rows: int | None = None,
                  progress=None, logger=None,
                  session_name: str | None = None) -> dict:
    """Validate a plain (non-Habitat) session: one :func:`check_session_day`
    per date dir, then a worst-of roll-up. Pure -- returns
    ``{...rollup..., "days": {day: full_day_verdict}}``; the caller writes the
    report files."""
    thr = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    report = progress or (lambda *a, **k: None)
    generated_at = _utcnow()
    days = enumerate_day_dirs(session_dir)

    if not days:
        v = _skeleton("skipped", "session", None, session_name, generated_at)
        v["reasons"] = ["session has no date directory"]
        v["days"] = {}
        return v

    day_verdicts: dict[str, dict] = {}
    for i, day in enumerate(days):
        report(i / len(days), f"day {day}")
        day_verdicts[day] = check_session_day(
            os.path.join(session_dir, day), thresholds=thr,
            cap_rows=cap_rows, health_cap_rows=health_cap_rows,
            session_name=session_name,
        )

    roll = _rollup([dv["status"] for dv in day_verdicts.values()])
    v = _skeleton(roll, "session", None, session_name, generated_at)
    v["days"] = day_verdicts
    v["reasons"] = [
        f"{day}: {r}"
        for day, dv in day_verdicts.items()
        for r in (dv.get("reasons") or [])
    ][:20]
    v["worst"] = {
        k: _max_opt(dv.get("worst", {}).get(k) for dv in day_verdicts.values())
        for k in ("ptp4l_p95_ns", "phc2sys_p95_ns", "inter_camera_p95_us",
                  "detrended_p95_us", "drift_us_per_sec", "max_dropped_frac")
    }
    v["counts"] = {
        "days": len(days),
        "green_days": sum(1 for dv in day_verdicts.values() if dv["status"] == "green"),
    }
    return v


def slim(verdict: dict) -> dict:
    """The subset stored on the session / in ``day_verdicts`` -- drops the
    per-camera and per-pair arrays so ``sessions.json`` and every
    ``sessions_update`` payload stay small (~1 KB)."""
    out = {
        "schema": verdict.get("schema", SCHEMA),
        "status": verdict["status"],
        "scope": verdict.get("scope"),
        "date_dir": verdict.get("date_dir"),
        "generated_at": verdict.get("generated_at"),
        "sync_mode": verdict.get("sync_mode"),
        "phase_lock_evaluated": verdict.get("phase_lock_evaluated", False),
        "reasons": (verdict.get("reasons") or [])[:5],
        "worst": verdict.get("worst") or {},
        "counts": verdict.get("counts") or {},
        "report_rel": verdict.get("report_rel"),
    }
    if "days" in verdict:
        out["days"] = {d: dv.get("status") for d, dv in verdict["days"].items()}
    return out


# --------------------------------------------------------------------------- #
# Day enumeration / completeness                                              #
# --------------------------------------------------------------------------- #


def enumerate_day_dirs(session_dir: str) -> list[str]:
    """Sorted ``YYYYMMDD`` sub-directory names (excludes ``_recovered``,
    dot-dirs, files, ``session_events.log``, compose outputs …)."""
    try:
        return sorted(
            e for e in os.listdir(session_dir)
            if _DAY_RE.match(e) and os.path.isdir(os.path.join(session_dir, e))
        )
    except OSError:
        return []


def day_has_cameras(session_dir: str, day: str) -> bool:
    dd = os.path.join(session_dir, day)
    try:
        for e in os.listdir(dd):
            md = os.path.join(dd, e)
            if os.path.isdir(md) and glob.glob(os.path.join(md, "*_timestamps.csv")):
                return True
    except OSError:
        pass
    return False


def day_is_complete(session_dir: str, day: str, pending_exports: int,
                    now_utc: datetime, settle_hours: float = 1) -> bool:
    """A day dir is safe to validate once it is in the past, settled past
    UTC midnight, and either all exports are confirmed or the day is old
    enough that a never-stopping Habitat Session should be checked anyway."""
    today = now_utc.strftime("%Y%m%d")
    if day >= today:
        return False
    midnight = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    if (now_utc - midnight).total_seconds() < settle_hours * 3600:
        return False
    if pending_exports == 0:
        return True
    try:
        d = datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError:
        return False
    return (now_utc - d).days >= 2


# --------------------------------------------------------------------------- #
# Report file                                                                 #
# --------------------------------------------------------------------------- #


def write_report(dir_path: str, verdict: dict) -> str | None:
    """Atomically write ``framesync_report.json`` into ``dir_path``. Best
    effort: a read-only share must not block validation -- returns ``None``
    on any OSError and the verdict is still stored (with ``report_rel=None``)."""
    try:
        os.makedirs(dir_path, exist_ok=True)
        try:
            os.chmod(dir_path, 0o777)
        except OSError:
            pass
        final = os.path.join(dir_path, REPORT_FILENAME)
        tmp = f"{final}.{uuid.uuid4().hex[:8]}.tmp"
        with open(tmp, "w") as f:
            json.dump(verdict, f, indent=2, default=_json_default)
        os.replace(tmp, final)
        try:
            os.chmod(final, 0o666)
        except OSError:
            pass
        return final
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Background worker                                                           #
# --------------------------------------------------------------------------- #


@dataclass
class SyncCheckJob:
    id: str
    spec: dict
    state: str = "queued"          # queued | running | done | error
    progress: float = 0.0
    stage: str = "queued"
    verdict_status: str | None = None
    report_rel: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def summary(self) -> dict:
        return asdict(self)


class SyncCheckWorker:
    """One check at a time, small FIFO queue, progress via ``on_update``.

    Auto-triggered from ``Recording._monitor_sessions`` (via the facade) and
    also by the manual ``recheck_framesync`` socket event. In-memory only --
    a controller restart just re-triggers from the monitor loop. ``on_result``
    is ``(session_name, scope, date_dir, slim_verdict, report_rel)`` and is
    where the verdict gets stored on the session."""

    def __init__(self, share_path: str, thresholds_provider=None,
                 on_update=None, on_result=None, logger=None):
        self.share_path = share_path
        self._thresholds = thresholds_provider or (lambda: dict(DEFAULT_THRESHOLDS))
        self._on_update = on_update or (lambda _s: None)
        self._on_result = on_result or (lambda *_a: None)
        self._log = logger
        self._jobs: dict[str, SyncCheckJob] = {}
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run_loop, name="framesync-worker", daemon=True
        )
        self._thread.start()

    # -- public --------------------------------------------------------- #

    def submit(self, spec: dict) -> SyncCheckJob:
        spec = dict(spec or {})
        name = str(spec.get("session_name", ""))
        if not _safe_name(name):
            raise SyncCheckError("invalid or missing session_name")
        scope = spec.get("scope") or ("day" if spec.get("date_dir") else "session")
        if scope not in ("session", "day"):
            raise SyncCheckError("scope must be 'session' or 'day'")
        date_dir = spec.get("date_dir") or None
        if scope == "day":
            if not date_dir or not _safe_segment(str(date_dir)):
                raise SyncCheckError("a day check needs a valid date_dir")
        spec = {"session_name": name, "scope": scope, "date_dir": date_dir,
                "reason": spec.get("reason", "manual")}

        with self._lock:
            for j in self._jobs.values():
                if j.state in ("queued", "running") and (
                    j.spec["session_name"] == name
                    and j.spec["scope"] == scope
                    and j.spec["date_dir"] == date_dir
                ):
                    return j            # already in flight -- dedupe
            pending = sum(1 for j in self._jobs.values()
                          if j.state in ("queued", "running"))
            if pending >= MAX_QUEUE:
                raise SyncCheckError("sync-check queue is full, try again shortly")
            job = SyncCheckJob(id=uuid.uuid4().hex[:12], spec=spec)
            self._jobs[job.id] = job
        self._queue.put(job.id)
        self._emit(job)
        return job

    def list(self) -> list[dict]:
        with self._lock:
            return [
                j.summary()
                for j in sorted(self._jobs.values(),
                                key=lambda j: j.created_at, reverse=True)
            ]

    # -- internals ---------------------------------------------------- #

    def _emit(self, job: SyncCheckJob) -> None:
        try:
            self._on_update(job.summary())
        except Exception:  # noqa: BLE001 -- a broken listener must not kill the worker
            if self._log:
                self._log.exception("framesync on_update callback failed")

    def _run_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            with self._lock:
                job = self._jobs.get(job_id)
            if job is None:
                continue
            job.state = "running"
            job.started_at = time.time()
            job.stage = "starting"
            self._emit(job)
            try:
                self._run(job)
                job.state = "done"
                job.stage = "done"
                job.progress = 1.0
            except Exception as exc:  # noqa: BLE001 -- report, never crash the loop
                job.state = "error"
                job.stage = "error"
                job.error = str(exc)
                if self._log:
                    self._log.exception("framesync job %s failed", job.id)
                self._report_error(job, exc)
            finally:
                job.finished_at = time.time()
                self._emit(job)
                self._prune()

    def _run(self, job: SyncCheckJob) -> None:
        spec = job.spec
        name = spec["session_name"]
        session_dir = os.path.join(self.share_path, name)
        if not os.path.isdir(session_dir):
            raise SyncCheckError("session directory not found")
        thr = self._thresholds()

        def progress(frac, stage):
            job.progress = round(float(frac), 3)
            job.stage = stage
            self._emit(job)

        if spec["scope"] == "day":
            day = spec["date_dir"]
            dv = check_session_day(
                os.path.join(session_dir, day), thresholds=thr,
                progress=progress, logger=self._log, session_name=name,
            )
            rr = self._write(os.path.join(session_dir, day), dv)
            dv["report_rel"] = rr
            job.verdict_status = dv["status"]
            job.report_rel = rr
            self._on_result(name, "day", day, slim(dv), rr)
            return

        full = check_session(
            session_dir, thresholds=thr,
            progress=progress, logger=self._log, session_name=name,
        )
        for day, dv in full.get("days", {}).items():
            dv["report_rel"] = self._write(os.path.join(session_dir, day), dv)
        rr = self._write(session_dir, full)
        full["report_rel"] = rr
        job.verdict_status = full["status"]
        job.report_rel = rr
        self._on_result(name, "session", None, slim(full), rr)

    def _write(self, dir_path: str, verdict: dict) -> str | None:
        path = write_report(dir_path, verdict)
        if path is None:
            return None
        try:
            return os.path.relpath(path, self.share_path)
        except ValueError:
            return None

    def _report_error(self, job: SyncCheckJob, exc: Exception) -> None:
        """Tell the monitor this target was handled even on failure, so it
        stops re-enqueuing it every cycle. A manual Re-check clears it."""
        spec = job.spec
        err = {
            "schema": SCHEMA, "status": "error", "scope": spec.get("scope"),
            "date_dir": spec.get("date_dir"), "generated_at": _utcnow(),
            "reasons": [str(exc)], "worst": {}, "counts": {}, "report_rel": None,
        }
        try:
            self._on_result(spec["session_name"], spec.get("scope"),
                            spec.get("date_dir"), err, None)
        except Exception:  # noqa: BLE001
            if self._log:
                self._log.exception("framesync error on_result failed")

    def _prune(self, keep: int = 40) -> None:
        with self._lock:
            done = sorted(
                (j for j in self._jobs.values()
                 if j.state in ("done", "error")),
                key=lambda j: j.finished_at or 0,
            )
            for j in done[:-keep]:
                self._jobs.pop(j.id, None)
