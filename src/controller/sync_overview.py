"""Sync overview: one read-only snapshot of fleet timing for the System → Sync tab.

Pulls together data the controller already holds -- nothing here measures
anything new:

- per-module PTP history (``Health.module_health_history``, one sample per
  heartbeat), downsampled to a time window for sparklines;
- the controller's own PTP grandmaster / phc2sys state (``PTP.get_status`` and
  its rolling ``ptp_buffer``);
- camera FrameSync roles as reported (``true_config``), the role the
  controller wants each one in (mirrors ``ControllerFacade.reconcile_framesync``
  without its side effects), and whether a change is waiting on a recording;
- the most recent post-hoc framesync verdicts stored on sessions.

Pure functions over plain data so they test without a controller.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import Any

# Columns of each downsampled module PTP row, in order.
PTP_ROW_FIELDS = (
    "t",
    "ptp4l", "ptp4l_min", "ptp4l_max",
    "phc2sys", "phc2sys_min", "phc2sys_max",
)

DEFAULT_WINDOW_S = 3600
MAX_WINDOW_S = 24 * 3600
MAX_POINTS = 240
RECENT_VERDICTS = 5


def _abs_extreme(values: Iterable[float | None]) -> float | None:
    """The value with the largest magnitude (sign kept), or None."""
    best = None
    for v in values:
        if v is None:
            continue
        if best is None or abs(v) > abs(best):
            best = v
    return best


def _min_opt(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return min(vals) if vals else None


def _max_opt(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return max(vals) if vals else None


def downsample_ptp(history: Iterable[Mapping[str, Any]], since: float,
                   until: float, max_points: int = MAX_POINTS) -> list[list]:
    """Bucket per-heartbeat PTP samples into at most ``max_points`` rows.

    Each row is ``PTP_ROW_FIELDS``: the bucket's last timestamp, then for each
    of ptp4l / phc2sys the worst (largest-magnitude) instantaneous offset and
    the min/max of the reported ranges. A bucket never hides a spike: the band
    is the envelope of every sample's own min/max (falling back to the
    instantaneous value where a heartbeat carried no range).
    """
    samples = [s for s in history if since <= s.get("timestamp", 0) <= until]
    if not samples:
        return []
    span = max(until - since, 1e-9)
    bucket_w = span / max_points
    buckets: dict[int, list[Mapping[str, Any]]] = {}
    for s in samples:
        idx = min(int((s["timestamp"] - since) / bucket_w), max_points - 1)
        buckets.setdefault(idx, []).append(s)

    rows = []
    for idx in sorted(buckets):
        group = buckets[idx]
        row: list = [round(group[-1]["timestamp"], 1)]
        for key in ("ptp4l_offset_ns", "phc2sys_offset_ns"):
            inst = [g.get(key) for g in group]
            lows = [g.get(f"{key}_min", g.get(key)) for g in group]
            highs = [g.get(f"{key}_max", g.get(key)) for g in group]
            # A heartbeat whose range field is present but None still has an
            # instantaneous value worth folding into the band.
            lows += inst
            highs += inst
            row += [_abs_extreme(inst), _min_opt(lows), _max_opt(highs)]
        rows.append(row)
    return rows


def downsample_controller_buffer(buffer: Iterable[Mapping[str, Any]], since: float,
                                 until: float,
                                 max_points: int = MAX_POINTS) -> list[list]:
    """Controller phc2sys buffer (~1 Hz) → ``[[t, worst, min, max], …]``."""
    samples = [b for b in buffer
               if since <= b.get("timestamp", 0) <= until
               and b.get("phc2sys_offset_ns") is not None]
    if not samples:
        return []
    bucket_w = max(until - since, 1e-9) / max_points
    buckets: dict[int, list[float]] = {}
    last_t: dict[int, float] = {}
    for b in samples:
        idx = min(int((b["timestamp"] - since) / bucket_w), max_points - 1)
        buckets.setdefault(idx, []).append(b["phc2sys_offset_ns"])
        last_t[idx] = b["timestamp"]
    return [[round(last_t[i], 1), _abs_extreme(v), min(v), max(v)]
            for i, v in sorted(buckets.items())]


def framesync_view(cameras: Mapping[str, Mapping[str, Any]],
                   transmitter: str | None,
                   waiting: Iterable[str]) -> dict:
    """Role table for every known camera.

    ``cameras``: module_id → {online, recording, config_status,
    camera (true_config["camera"]), target_camera (target_config["camera"])}.

    expected_role follows reconcile_framesync's rule (transmitter → server,
    other online enabled cameras → client, the rest → none). ``deferred`` is
    set when the reported role differs from it and the camera is recording,
    which is exactly when reconcile holds the change back.
    """
    waiting = set(waiting)
    rows = {}
    for mid, info in cameras.items():
        cam = info.get("camera") or {}
        target = info.get("target_camera") or {}
        enabled = bool(cam.get("framesync_enabled"))
        online = bool(info.get("online"))
        reported = cam.get("sync_mode") or "none"
        if not online:
            expected = None
        elif mid == transmitter:
            expected = "server"
        elif enabled:
            expected = "client"
        else:
            expected = "none"
        pending = str(info.get("config_status") or "").upper() == "PENDING"
        rows[mid] = {
            "online": online,
            "recording": bool(info.get("recording")),
            "framesync_enabled": enabled,
            "sync_mode": reported,
            "expected_role": expected,
            "pending_sync_mode": target.get("sync_mode") if pending else None,
            "fps": cam.get("fps"),
            "sensor_mode_index": cam.get("sensor_mode_index"),
            "waiting_for_server": mid in waiting,
            "deferred": bool(expected and expected != reported
                             and info.get("recording")),
        }
    return {"transmitter": transmitter, "cameras": rows}


def recent_verdicts(sessions: Mapping[str, Any],
                    limit: int = RECENT_VERDICTS) -> list[dict]:
    """Newest-first slim framesync verdicts across sessions.

    Sorted on the verdict's own ``generated_at`` (ISO 8601 UTC, so it sorts
    lexically), not the session's start time: a long habitat session's latest
    day verdict is newer than a short session that started after it.
    """
    out = []
    for name, session in sessions.items():
        verdict = getattr(session, "framesync_verdict", None)
        if verdict is None and isinstance(session, Mapping):
            verdict = session.get("framesync_verdict")
        if not verdict or not verdict.get("status"):
            continue
        out.append({"session_name": name, **verdict})
    out.sort(key=lambda v: v.get("generated_at") or "", reverse=True)
    return out[:limit]


def clamp_window(window_s: Any) -> int:
    try:
        w = int(float(window_s))
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_S
    return max(300, min(w, MAX_WINDOW_S))


def build_overview(*, module_history: Mapping[str, Iterable[Mapping[str, Any]]],
                   module_health: Mapping[str, Mapping[str, Any]],
                   controller_ptp: Mapping[str, Any] | None,
                   controller_buffer: Iterable[Mapping[str, Any]],
                   cameras: Mapping[str, Mapping[str, Any]],
                   transmitter: str | None,
                   waiting: Iterable[str],
                   sessions: Mapping[str, Any],
                   thresholds: Mapping[str, float],
                   window_s: Any = DEFAULT_WINDOW_S,
                   now: float | None = None) -> dict:
    now = time.time() if now is None else now
    window = clamp_window(window_s)
    since = now - window

    modules = {}
    for mid, health in module_health.items():
        modules[mid] = {
            "ptp4l_offset_ns": health.get("ptp4l_offset_ns"),
            "ptp4l_offset_ns_min": health.get("ptp4l_offset_ns_min"),
            "ptp4l_offset_ns_max": health.get("ptp4l_offset_ns_max"),
            "phc2sys_offset_ns": health.get("phc2sys_offset_ns"),
            "phc2sys_offset_ns_min": health.get("phc2sys_offset_ns_min"),
            "phc2sys_offset_ns_max": health.get("phc2sys_offset_ns_max"),
            "ptp4l_freq": health.get("ptp4l_freq"),
            "phc2sys_freq": health.get("phc2sys_freq"),
            "ptp_restarts": health.get("ptp_restarts"),
            "last_ptp_restart": health.get("last_ptp_restart"),
            "last_heartbeat": health.get("last_heartbeat"),
            "history": downsample_ptp(module_history.get(mid, ()), since, now),
        }

    controller = None
    if controller_ptp is not None:
        controller = {
            k: controller_ptp.get(k)
            for k in ("role", "status", "ptp4l_service", "phc2sys_service",
                      "phc2sys_offset_ns", "phc2sys_freq", "last_sync",
                      "interface")
        }
        controller["history"] = downsample_controller_buffer(
            controller_buffer, since, now)

    return {
        "generated_at": now,
        "window_s": window,
        "ptp_row_fields": list(PTP_ROW_FIELDS),
        "thresholds": dict(thresholds),
        "controller": controller,
        "modules": modules,
        "framesync": framesync_view(cameras, transmitter, waiting),
        "recent_verdicts": recent_verdicts(sessions),
    }
