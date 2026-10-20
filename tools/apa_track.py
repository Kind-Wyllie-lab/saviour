#!/usr/bin/env python3
"""
apa_track.py -- offline rat tracking for APA sessions: a per-frame centroid
track, time in the shock zone, a trajectory map and a shock timeline.

The apa_camera module only tracks live when `object_detection.enabled` is on
(and then writes det_cx/det_cy/in_zone into the timestamps CSV). This works
from the recorded video instead, so it covers sessions recorded without it.

Tracking: the arena floor rotates, so a per-pixel temporal median of frames
sampled across the session is an empty-arena background (the rat moves in
the room frame even when it freezes, because the floor carries it). Each
frame is blurred (which also washes out the grid bars), differenced against
that, thresholded, and closed so a hooded rat's dark hood and white body
merge into one blob; the blob nearest the last position wins.

The shock zone is rebuilt from the session's exported apa_camera config.json
with the same geometry as APACameraModule._apply_shock_zone (the overlay the
operator shocks from), in recorded-frame pixels. Everything is in the room
frame, which is the frame the zone is defined in.

    python3 tools/apa_track.py /home/pi/controller_share/<session>/<DATE> [...]
    python3 tools/apa_track.py /home/pi/controller_share/Nlgn3FlexNew_Test-85*/2026*

Writes <out>/<session>/track.csv, summary.json, trajectory.png, timeline.png
(default out: ./apa_track) and prints a one-line summary per session. Needs
only numpy + OpenCV (the main env). Check a few frames of a new rig, lighting
or coat colour with --debug-frames before trusting the numbers.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "controller"))
from camera_segments import camera_segments  # noqa: E402 -- path set just above

PROC_SIZE = 540          # tracking resolution (long side), px
BG_SAMPLES = 80          # frames sampled per segment for the background median
BLUR_SIGMA = 3.0         # at PROC_SIZE; wider than the grid bar spacing
MAX_GAP_S = 1.0          # interpolate missed detections up to this long
ENTRY_MERGE_S = 0.3      # zone exits shorter than this don't end an entry
PULSE_ZONE_SLACK_S = 0.5 # a pulse this close to an in-zone frame counts as in-zone


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def find_module_dir(date_dir: str, prefix: str) -> str | None:
    dirs = sorted(glob.glob(os.path.join(date_dir, f"{prefix}_*")))
    return dirs[0] if dirs else None


def load_config(module_dir: str) -> dict:
    with open(os.path.join(module_dir, "config.json")) as f:
        return json.load(f)


def load_shock_events(date_dir: str) -> list[tuple[int, str]]:
    ard = find_module_dir(date_dir, "apa_arduino")
    if not ard:
        return []
    files = glob.glob(os.path.join(ard, "*_shock_events.csv"))
    events = []
    for path in files:
        with open(path) as f:
            for line in f:
                if line[:1].isdigit():
                    t, ev = line.split(",")[:2]
                    events.append((int(t), ev.strip()))
    return sorted(events)


def read_timestamps(csv_path: str) -> list[int]:
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        return [int(row["timestamp_ns"]) for row in reader if row.get("timestamp_ns")]


# ---------------------------------------------------------------------------
# Shock zone geometry (mirrors apa_camera_module.py)
# ---------------------------------------------------------------------------

def zone_geometry(cfg: dict, width: int, height: int) -> dict:
    mask = cfg.get("mask", {})
    sz = cfg.get("shock_zone", {})
    cx = width // 2 + int(mask.get("mask_center_x_offset") or 0)
    cy = height // 2 + int(mask.get("mask_center_y_offset") or 0)
    outer_r = int(0.5 * float(mask["mask_radius"]) * min(height, width))
    inner_r = max(0, int(float(sz.get("shock_zone_inner_offset", 0)) * outer_r))
    span = float(sz.get("shock_zone_angle_span_deg", 0))
    # The module subtracts 90 so the config's 0 deg is the top of the frame.
    centre = float(sz.get("shock_zone_start_angle_deg", 0)) - 90
    start = (centre - span / 2) % 360
    return {"cx": cx, "cy": cy, "outer_r": outer_r, "inner_r": inner_r,
            "start_deg": start, "span_deg": span}


def in_zone(x: np.ndarray, y: np.ndarray, g: dict) -> np.ndarray:
    """Vectorised point-in-sector test; NaN positions are not in the zone.

    Angles wrap mod 360 here. The module's own _is_in_shock_zone compares raw
    start/end angles against [0, 360), so it misses zones whose arc goes below
    0 or above 360 deg (config start angle under 120 deg at a 60 deg span)."""
    dx, dy = x - g["cx"], y - g["cy"]
    r = np.hypot(dx, dy)
    theta = np.degrees(np.arctan2(dy, dx)) % 360
    with np.errstate(invalid="ignore"):
        inside_r = (r >= g["inner_r"]) & (r <= g["outer_r"])
        inside_a = ((theta - g["start_deg"]) % 360) <= g["span_deg"]
    return inside_r & inside_a


def draw_zone(img: np.ndarray, g: dict, scale: float, color, fill_alpha: float = 0.25):
    c = (int(g["cx"] * scale), int(g["cy"] * scale))
    ro, ri = int(g["outer_r"] * scale), int(g["inner_r"] * scale)
    a0, a1 = g["start_deg"], g["start_deg"] + g["span_deg"]
    outer = cv2.ellipse2Poly(c, (ro, ro), 0, int(a0), int(math.ceil(a1)), 2)
    inner = cv2.ellipse2Poly(c, (ri, ri), 0, int(a0), int(math.ceil(a1)), 2)[::-1]
    poly = np.concatenate([outer, inner]) if ri > 0 else np.concatenate([outer, [c]])
    overlay = img.copy()
    cv2.fillPoly(overlay, [poly], color)
    cv2.addWeighted(overlay, fill_alpha, img, 1 - fill_alpha, 0, img)
    cv2.polylines(img, [poly], True, color, 2, cv2.LINE_AA)
    cv2.circle(img, c, ro, (90, 90, 90), 1, cv2.LINE_AA)


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------

def _prep(frame: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    g = cv2.resize(g, size, interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(g, (0, 0), BLUR_SIGMA)


def background(video: str, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """(blurred grey background for differencing, colour background for plots)."""
    cap = cv2.VideoCapture(video)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    grey, colour = [], []
    for idx in np.linspace(0, max(0, n - 1), BG_SAMPLES).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, f = cap.read()
        if ok:
            grey.append(_prep(f, size))
            colour.append(cv2.resize(f, size, interpolation=cv2.INTER_AREA))
    cap.release()
    if not grey:
        raise RuntimeError(f"could not read frames from {video}")
    return (np.median(np.stack(grey), axis=0).astype(np.uint8),
            np.median(np.stack(colour), axis=0).astype(np.uint8))


class RatTracker:
    def __init__(self, bg: np.ndarray, arena_mask: np.ndarray, threshold: int,
                 min_area: int, max_jump: float):
        self.bg = bg
        self.arena = arena_mask
        self.threshold = threshold
        self.min_area = min_area
        self.max_jump = max_jump
        self.last: tuple[float, float] | None = None
        self.areas: list[int] = []
        self.k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        self.k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    def detect(self, grey: np.ndarray) -> tuple[float, float, int] | None:
        """(x, y, area) at tracking resolution, or None."""
        d = cv2.absdiff(grey, self.bg)
        m = ((d >= self.threshold) & self.arena).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, self.k_open)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, self.k_close)
        n, _, stats, cents = cv2.connectedComponentsWithStats(m, connectivity=8)
        # Candidate areas, largest first, for callers judging ambiguity.
        self.areas = sorted((int(a) for a in stats[1:, cv2.CC_STAT_AREA] if a >= self.min_area),
                            reverse=True)
        best, best_score = None, 0.0
        for k in range(1, n):
            area = int(stats[k, cv2.CC_STAT_AREA])
            if area < self.min_area:
                continue
            x, y = cents[k]
            score = float(area)
            if self.last is not None:
                dist = math.hypot(x - self.last[0], y - self.last[1])
                score *= math.exp(-dist / self.max_jump)
            if score > best_score:
                best, best_score = (float(x), float(y), area), score
        if best:
            self.last = best[:2]
        return best


def track_session(date_dir: str, step: int, threshold: int, min_area: int,
                  debug_frames: int, out_dir: str) -> dict:
    cam_dir = find_module_dir(date_dir, "apa_camera")
    if not cam_dir:
        raise RuntimeError("no apa_camera_* folder")
    cfg = load_config(cam_dir)
    segments = camera_segments(cam_dir)
    if not segments:
        raise RuntimeError(f"no video/timestamps pairs in {cam_dir}")

    rows = []  # (frame_id, timestamp_ns, x, y, area) in full-res px
    geom = bg_colour = None
    debug = []
    for seg in segments:
        cap = cv2.VideoCapture(seg.video_path)
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if geom is None:
            geom = zone_geometry(cfg, w, h)
        scale = PROC_SIZE / max(w, h)
        size = (round(w * scale), round(h * scale))
        bg, colour = background(seg.video_path, size)
        if bg_colour is None:
            bg_colour = colour
        yy, xx = np.mgrid[:size[1], :size[0]]
        arena = np.hypot(xx - geom["cx"] * scale, yy - geom["cy"] * scale) <= geom["outer_r"] * scale
        tracker = RatTracker(bg, arena, threshold, min_area, max_jump=60.0)

        stamps = read_timestamps(seg.csv_path)
        want_debug = set(np.linspace(0, len(stamps) - 1, debug_frames + 2).astype(int)[1:-1]) \
            if debug_frames else set()
        for i, ts in enumerate(stamps):
            if i % step and i not in want_debug:
                if not cap.grab():
                    break
                continue
            ok, frame = cap.read()
            if not ok:
                # OpenCV's frame-count estimate can be wrong (May 2026 files
                # advertise 30 fps for 25), so only frames actually read count.
                print(f"  warning: {os.path.basename(seg.video_path)} ended at frame {i} "
                      f"of {len(stamps)} CSV rows", file=sys.stderr)
                break
            det = tracker.detect(_prep(frame, size))
            if det:
                rows.append((i, ts, det[0] / scale, det[1] / scale, det[2] / scale ** 2))
            else:
                rows.append((i, ts, math.nan, math.nan, 0))
            if i in want_debug:
                debug.append((frame, rows[-1]))
        cap.release()

    if debug:
        _write_debug(debug, geom, os.path.join(out_dir, "debug_frames.jpg"))
    return {"cfg": cfg, "geom": geom, "rows": rows, "bg": bg_colour, "frame_w": w}


def _write_debug(debug, geom, path):
    tiles = []
    for frame, (_, _ts, x, y, area) in debug:
        img = frame.copy()
        draw_zone(img, geom, 1.0, (0, 0, 255))
        if not math.isnan(x):
            cv2.circle(img, (int(x), int(y)), 14, (0, 255, 255), 3)
        cv2.putText(img, f"area {area:.0f}" if area else "no detection", (20, 140),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 255), 3)
        tiles.append(cv2.resize(img, (400, 400)))
    while len(tiles) % 4:
        tiles.append(np.zeros_like(tiles[0]))
    grid = np.vstack([np.hstack(tiles[r:r + 4]) for r in range(0, len(tiles), 4)])
    cv2.imwrite(path, grid)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index runs where mask is True."""
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def analyse(result: dict, shocks: list[tuple[int, str]], arena_cm: float | None,
            skip_s: float = 0.0) -> dict:
    rows = result["rows"]
    if skip_s > 0:
        start = rows[0][1] + int(skip_s * 1e9)
        rows = result["rows"] = [r for r in rows if r[1] >= start]
    g = result["geom"]
    ts = np.array([r[1] for r in rows], dtype=np.int64)
    x = np.array([r[2] for r in rows])
    y = np.array([r[3] for r in rows])
    t = (ts - ts[0]) / 1e9
    detected = ~np.isnan(x)

    # Fill short gaps by linear interpolation; leave long ones (rat absent,
    # e.g. before placement) as NaN.
    filled = detected.copy()
    for a, b in _runs(~detected):
        if a == 0 or b == len(x) or t[b] - t[a - 1] > MAX_GAP_S:
            continue
        for arr in (x, y):
            arr[a:b] = np.interp(t[a:b], [t[a - 1], t[b]], [arr[a - 1], arr[b]])
        filled[a:b] = True

    zone = in_zone(x, y, g)
    # Merge brief exits (jitter on the zone edge) into the surrounding entry.
    for a, b in _runs(~zone):
        if a > 0 and b < len(zone) and t[b] - t[a] < ENTRY_MERGE_S and filled[a:b].all():
            zone[a:b] = True
    entries = _runs(zone)

    present = np.flatnonzero(filled)
    t0 = t[present[0]] if len(present) else 0.0
    t_end = t[present[-1]] if len(present) else 0.0
    dt = np.diff(t, append=t[-1] + (t[-1] - t[-2] if len(t) > 1 else 0))
    time_in = float(dt[zone].sum())
    tracked = float(dt[filled].sum())

    # Longest stretch out of the zone, from the rat appearing to the end.
    bounds = [t0] + [v for a, b in entries for v in (t[a], t[b - 1])] + [t_end]
    max_avoid = max((bounds[i + 1] - bounds[i] for i in range(0, len(bounds) - 1, 2)), default=0.0)

    step = np.hypot(np.diff(x), np.diff(y))
    path_r = float(np.nansum(step[filled[1:] & filled[:-1]])) / g["outer_r"]

    pulses = np.array([s for s, e in shocks if e == "SENDING_SHOCK"], dtype=np.int64)
    delivered = np.array([s for s, e in shocks if e == "SHOCK_DELIVERY"], dtype=np.int64)
    in_window = (pulses >= ts[0]) & (pulses <= ts[-1])
    pulses = pulses[in_window]
    pulse_idx = np.clip(np.searchsorted(ts, pulses), 0, len(ts) - 1)
    slack_ns = int(PULSE_ZONE_SLACK_S * 1e9)
    zone_ts = ts[zone]
    pulse_in_zone = np.array([bool(np.any(np.abs(zone_ts - p) <= slack_ns)) for p in pulses], bool)
    shocked_entries = [bool(np.any((pulses >= ts[a] - slack_ns) & (pulses <= ts[b - 1] + slack_ns)))
                       for a, b in entries]
    lat = []
    for a, b in entries:
        after = pulses[(pulses >= ts[a]) & (pulses <= ts[b - 1] + slack_ns)]
        if len(after):
            lat.append((after[0] - ts[a]) / 1e9)

    summary = {
        "session_s": round(float(t[-1]), 1),
        "rat_first_seen_s": round(float(t0), 1),
        "tracked_fraction": round(tracked / max(1e-9, t_end - t0), 3),
        "time_in_zone_s": round(time_in, 1),
        "time_in_zone_pct": round(100 * time_in / max(1e-9, tracked), 1),
        "entries": len(entries),
        "latency_first_entry_s": round(float(t[entries[0][0]] - t0), 1) if entries else None,
        "max_time_avoided_s": round(float(max_avoid), 1),
        "mean_entry_s": round(float(np.mean([t[b - 1] - t[a] for a, b in entries])), 2) if entries else None,
        "path_arena_radii": round(path_r, 1),
        "shock_pulses": int(len(pulses)),
        "shock_deliveries": int(((delivered >= ts[0]) & (delivered <= ts[-1])).sum()),
        "pulses_in_zone": int(pulse_in_zone.sum()),
        "pulses_outside_zone": int((~pulse_in_zone).sum()),
        "entries_shocked": int(sum(shocked_entries)),
        "entries_not_shocked": int(len(entries) - sum(shocked_entries)),
        "median_entry_to_shock_s": round(float(np.median(lat)), 2) if lat else None,
    }
    if arena_cm:
        summary["path_cm"] = round(path_r * arena_cm / 2, 0)
    result.update(t=t, x=x, y=y, detected=detected, filled=filled, zone=zone,
                  pulses_t=(pulses - ts[0]) / 1e9, pulse_xy=[(x[i], y[i]) for i in pulse_idx],
                  pulse_in_zone=pulse_in_zone, delivered_t=(delivered[(delivered >= ts[0])
                  & (delivered <= ts[-1])] - ts[0]) / 1e9, summary=summary)
    return summary


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

def write_track_csv(result: dict, path: str):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame_id", "timestamp_ns", "t_s", "x_px", "y_px", "detected",
                    "in_zone"])
        for k, row in enumerate(result["rows"]):
            x, y = result["x"][k], result["y"][k]
            w.writerow([row[0], row[1], f"{result['t'][k]:.3f}",
                        "" if math.isnan(x) else f"{x:.1f}", "" if math.isnan(y) else f"{y:.1f}",
                        int(result["detected"][k]), int(result["zone"][k])])


def write_trajectory(result: dict, title: str, path: str, size: int = 1000):
    g = result["geom"]
    bg = cv2.resize(result["bg"], (size, size))
    img = (bg * 0.6).astype(np.uint8)
    scale = size / result["frame_w"]
    arena = np.zeros(img.shape[:2], np.uint8)
    cv2.circle(arena, (int(g["cx"] * scale), int(g["cy"] * scale)), int(g["outer_r"] * scale), 255, -1)
    img[arena == 0] = 0
    draw_zone(img, g, scale, (0, 0, 220))
    x, y, filled, t = result["x"], result["y"], result["filled"], result["t"]
    colours = cv2.applyColorMap(np.linspace(0, 255, 256).astype(np.uint8)[:, None],
                                cv2.COLORMAP_VIRIDIS)[:, 0]
    tmax = max(1e-9, t[-1])
    for k in range(1, len(x)):
        if filled[k] and filled[k - 1]:
            c = tuple(int(v) for v in colours[int(255 * t[k] / tmax)])
            cv2.line(img, (int(x[k - 1] * scale), int(y[k - 1] * scale)),
                     (int(x[k] * scale), int(y[k] * scale)), c, 2, cv2.LINE_AA)
    for (px, py), inz in zip(result["pulse_xy"], result["pulse_in_zone"]):
        if not math.isnan(px):
            p = (int(px * scale), int(py * scale))
            cv2.drawMarker(img, p, (0, 0, 255) if inz else (0, 165, 255),
                           cv2.MARKER_TILTED_CROSS, 18, 3, cv2.LINE_AA)
    s = result["summary"]
    lines = [title,
             f"in zone {s['time_in_zone_pct']}%  entries {s['entries']}  "
             f"1st entry {s['latency_first_entry_s']}s  max avoid {s['max_time_avoided_s']}s",
             f"pulses {s['shock_pulses']} (outside zone {s['pulses_outside_zone']})  "
             f"x = pulse   path: start purple -> end yellow"]
    for i, line in enumerate(lines):
        cv2.putText(img, line, (12, 28 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(path, img)


def write_timeline(result: dict, title: str, path: str, width: int = 1600):
    t, zone, filled = result["t"], result["zone"], result["filled"]
    tmax = max(1e-9, t[-1])
    left, right = 110, 20
    W = width - left - right
    img = np.full((210, width, 3), 255, np.uint8)
    X = lambda s: int(left + W * s / tmax)  # noqa: E731

    def band(y0, label):
        cv2.putText(img, label, (8, y0 + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        cv2.rectangle(img, (left, y0), (left + W, y0 + 30), (225, 225, 225), -1)

    band(40, "in zone")
    for a, b in _runs(~filled):
        cv2.rectangle(img, (X(t[a]), 40), (X(t[min(b, len(t) - 1)]), 70), (180, 180, 180), -1)
    for a, b in _runs(zone):
        cv2.rectangle(img, (X(t[a]), 40), (max(X(t[a]) + 1, X(t[b - 1])), 70), (0, 0, 220), -1)
    band(90, "pulses")
    for p, inz in zip(result["pulses_t"], result["pulse_in_zone"]):
        cv2.line(img, (X(p), 90), (X(p), 120), (0, 0, 220) if inz else (0, 140, 255), 2)
    band(140, "delivered")
    for p in result["delivered_t"]:
        cv2.line(img, (X(p), 140), (X(p), 170), (60, 60, 60), 2)
    for s in range(0, int(tmax) + 1, 60):
        cv2.line(img, (X(s), 172), (X(s), 180), (0, 0, 0), 1)
        cv2.putText(img, f"{s // 60}m", (X(s) - 8, 198), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
    cv2.putText(img, title + "   (grey = rat not tracked, orange pulse = outside zone)",
                (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.imwrite(path, img)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("date_dirs", nargs="+", help="session date dir(s): <share>/<session>/<DATE>")
    ap.add_argument("--out", default="apa_track", help="output root (default ./apa_track)")
    ap.add_argument("--step", type=int, default=2, help="track every Nth frame (default 2)")
    ap.add_argument("--threshold", type=int, default=15, help="difference threshold, 0-255")
    ap.add_argument("--min-area", type=int, default=150,
                    help=f"minimum blob area in px at {PROC_SIZE}px tracking size")
    ap.add_argument("--arena-diameter-cm", type=float, default=None,
                    help="arena diameter (the mask circle) to report path length in cm")
    ap.add_argument("--skip-s", type=float, default=0.0,
                    help="ignore the first N seconds (e.g. the rat being placed by hand)")
    ap.add_argument("--debug-frames", type=int, default=8,
                    help="write a contact sheet of N tracked frames to check detection")
    args = ap.parse_args()

    for date_dir in args.date_dirs:
        date_dir = os.path.abspath(date_dir.rstrip("/"))
        session = os.path.basename(os.path.dirname(date_dir))
        out_dir = os.path.join(args.out, session)
        os.makedirs(out_dir, exist_ok=True)
        try:
            result = track_session(date_dir, args.step, args.threshold, args.min_area,
                                   args.debug_frames, out_dir)
        except Exception as e:  # noqa: BLE001 -- one bad session shouldn't stop a batch
            print(f"{session}: skipped ({e})", file=sys.stderr)
            continue
        s = analyse(result, load_shock_events(date_dir), args.arena_diameter_cm, args.skip_s)
        write_track_csv(result, os.path.join(out_dir, "track.csv"))
        write_trajectory(result, session, os.path.join(out_dir, "trajectory.png"))
        write_timeline(result, session, os.path.join(out_dir, "timeline.png"))
        with open(os.path.join(out_dir, "summary.json"), "w") as f:
            json.dump({"session": session, "zone": result["geom"], **s}, f, indent=2)
        print(f"{session}: {s['session_s']}s, tracked {100 * s['tracked_fraction']:.0f}%, "
              f"in zone {s['time_in_zone_pct']}%, entries {s['entries']} "
              f"({s['entries_shocked']} shocked), 1st entry {s['latency_first_entry_s']}s, "
              f"max avoid {s['max_time_avoided_s']}s, pulses {s['shock_pulses']} "
              f"({s['pulses_outside_zone']} outside zone)")


if __name__ == "__main__":
    main()
