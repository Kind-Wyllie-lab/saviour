"""
Compose tiles for the ephys headstage accelerometer
(plans/ephys-accelerometer.md, phase 2): `accel_trace`, a scrolling strip
of acceleration / tilt / ODBA like the spectrogram tile, and
`orientation`, the headstage drawn as a 3D box tilted by its pitch and
roll.

Both read `<session>/_ephys/accel/<port>/accel.csv` (ephys_accel.py), and
only once it carries SAVIOUR wall time (a clock model was applied): there
is no video time to place them on otherwise. Drawn with OpenCV and numpy
only, the same code for the render and the preview.

Heading (yaw) can't be measured from an accelerometer, so the orientation
tile keeps it fixed and says so on the tile.
"""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass

import cv2
import numpy as np

from src.controller import ephys_accel

SOURCE_PREFIX = "ephys/accel/"
SIGNALS = ("xyz", "tilt", "odba")

# Series colours: categorical slots 1-3 of the validated palette, the steps
# for a dark surface (the tiles share the spectrogram's dark backing).
_BLUE = (0xE5, 0x87, 0x39)      # BGR of #3987e5
_ORANGE = (0x26, 0x59, 0xD9)    # #d95926
_AQUA = (0x70, 0x9E, 0x19)      # #199e70
_BG = (0x14, 0x10, 0x10)        # video_compose._PANEL_BG
_GRID = (0x3A, 0x36, 0x36)
_TEXT = (0xEC, 0xEF, 0xF0)
_MUTED = (0xB7, 0xC2, 0xC3)

SERIES = {
    "xyz": (("ax_g", "X", _BLUE), ("ay_g", "Y", _ORANGE), ("az_g", "Z", _AQUA)),
    "tilt": (("pitch_deg", "pitch", _BLUE), ("roll_deg", "roll", _ORANGE)),
    "odba": (("odba_g", "ODBA", _BLUE),),
}
UNITS = {"xyz": "g", "tilt": "°", "odba": "g"}
# Fixed default ranges, so the scale doesn't jump between renders. "auto"
# fits the data actually covered instead.
DEFAULT_RANGE = {"xyz": (-2.0, 2.0), "tilt": (-90.0, 90.0), "odba": (0.0, 1.5)}


class AccelTileError(ValueError):
    pass


def port_of(source: str) -> str:
    return source[len(SOURCE_PREFIX):]


def accel_dir(session_dir: str, source: str) -> str:
    return os.path.join(session_dir, "_ephys", "accel", port_of(source))


# --------------------------------------------------------------------------- #
# Data                                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class AccelData:
    """One headstage's aligned output, as arrays. `dyn` is the dynamic
    (non-gravity) acceleration in headstage axes, recomputed the way
    ephys_accel derives it."""
    t_ns: np.ndarray
    cols: dict[str, np.ndarray]
    dyn: np.ndarray
    label: str

    @property
    def start_ns(self) -> int:
        return int(self.t_ns[0])

    @property
    def end_ns(self) -> int:
        return int(self.t_ns[-1])

    def index_at(self, t_ns: int) -> int | None:
        """Nearest sample to `t_ns`, or None outside the recording (more
        than one sample period beyond either end)."""
        if t_ns < self.t_ns[0] - 2e7 or t_ns > self.t_ns[-1] + 2e7:
            return None
        i = int(np.searchsorted(self.t_ns, t_ns))
        if i >= len(self.t_ns):
            return len(self.t_ns) - 1
        if i > 0 and t_ns - self.t_ns[i - 1] < self.t_ns[i] - t_ns:
            return i - 1
        return i


_CACHE: dict[tuple, AccelData] = {}


def load(session_dir: str, source: str) -> AccelData:
    """The aligned accel.csv for `source` (cached per file and mtime).
    AccelTileError if it's missing or has no SAVIOUR time."""
    path = os.path.join(accel_dir(session_dir, source), "accel.csv")
    try:
        st = os.stat(path)
    except OSError as exc:
        raise AccelTileError(
            f"no accelerometer output for {port_of(source)}: run Accelerometer "
            "on the Ephys panel") from exc
    key = (path, st.st_size, st.st_mtime_ns)
    if key in _CACHE:
        return _CACHE[key]
    names = ("ax_g", "ay_g", "az_g", "pitch_deg", "roll_deg", "odba_g")
    ts: list[int] = []
    rows: list[list[float]] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("timestamp_ns"):
                raise AccelTileError(
                    f"accelerometer {port_of(source)} isn't aligned to the session "
                    "(no clock model): run Align on the Ephys panel, then Accelerometer")
            ts.append(int(row["timestamp_ns"]))
            rows.append([float(row[k]) for k in names])
    if len(ts) < 2:
        raise AccelTileError(f"accelerometer {port_of(source)} has no samples")
    arr = np.asarray(rows, dtype=np.float64)
    cols = {k: arr[:, i] for i, k in enumerate(names)}
    acc = arr[:, :3]
    dyn = acc - ephys_accel.gravity(acc)
    data = AccelData(np.asarray(ts, dtype=np.int64), cols, dyn,
                     f"Headstage {port_of(source)}")
    if len(_CACHE) > 8:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = data
    return data


def y_range(data: AccelData, signal: str, options: dict,
            t0_ns: int, t1_ns: int) -> tuple[float, float]:
    """The trace's y range: the options' fixed min/max, the signal's
    default, or (`y_auto`) the data between t0 and t1 with a margin."""
    lo, hi = DEFAULT_RANGE[signal]
    if options.get("y_auto"):
        sel = (data.t_ns >= t0_ns) & (data.t_ns <= t1_ns)
        if sel.any():
            vals = np.concatenate([data.cols[k][sel] for k, _l, _c in SERIES[signal]])
            vlo, vhi = float(np.min(vals)), float(np.max(vals))
            pad = max(0.05 * (vhi - vlo), 0.05 if signal != "tilt" else 2.0)
            lo, hi = vlo - pad, vhi + pad
    else:
        if options.get("y_min") is not None:
            lo = float(options["y_min"])
        if options.get("y_max") is not None:
            hi = float(options["y_max"])
    if hi <= lo:
        hi = lo + 1.0
    return lo, hi


# --------------------------------------------------------------------------- #
# Trace strip                                                                 #
# --------------------------------------------------------------------------- #


def _grid_values(lo: float, hi: float) -> list[float]:
    span = hi - lo
    raw = span / 4
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = math.ceil(lo / step) * step
    return [round(first + k * step, 6) for k in range(int((hi - first) / step) + 1)]


def render_strip(data: AccelData, signal: str, t0_ns: int, width: int, height: int,
                 pps: float, yr: tuple[float, float]) -> np.ndarray:
    """The trace over `width` strip pixels from `t0_ns` at `pps` pixels per
    second: grid lines, then each series as an anti-aliased polyline (or,
    when a pixel holds several samples, its min-max per pixel column).
    Times outside the recording are left blank."""
    img = np.empty((height, width, 3), dtype=np.uint8)
    img[:] = _BG
    lo, hi = yr
    pad = max(2, height // 20)

    def ypix(v):
        return np.clip((hi - v) / (hi - lo), 0, 1) * (height - 1 - 2 * pad) + pad

    for g in _grid_values(lo, hi):
        y = int(round(ypix(g)))
        cv2.line(img, (0, y), (width - 1, y), _GRID if g else (0x55, 0x50, 0x50), 1)

    t1_ns = t0_ns + int(width / pps * 1e9)
    i0 = max(0, int(np.searchsorted(data.t_ns, t0_ns)) - 1)
    i1 = min(len(data.t_ns), int(np.searchsorted(data.t_ns, t1_ns)) + 1)
    if i1 - i0 < 2:
        return img
    x = (data.t_ns[i0:i1] - t0_ns) / 1e9 * pps
    thick = 2 if height >= 80 else 1
    per_px = 1.0 / max(pps, 1e-9) * ephys_accel.FS     # samples per strip pixel
    for key, _label, colour in SERIES[signal]:
        v = data.cols[key][i0:i1]
        if per_px <= 1.5:
            pts = np.stack([x, ypix(v)], axis=1).round().astype(np.int32)
            cv2.polylines(img, [pts], False, colour, thick, cv2.LINE_AA)
            continue
        col = np.floor(x).astype(np.int64)
        starts = np.flatnonzero(np.r_[True, np.diff(col) != 0])
        cmin = np.minimum.reduceat(v, starts)
        cmax = np.maximum.reduceat(v, starts)
        cx = col[starts]
        ymin, ymax = ypix(cmax), ypix(cmin)
        pts = np.empty((2 * len(cx), 2), dtype=np.float64)
        pts[0::2, 0] = cx
        pts[1::2, 0] = cx
        pts[0::2, 1] = ymin
        pts[1::2, 1] = ymax
        cv2.polylines(img, [pts.round().astype(np.int32)], False, colour, 1, cv2.LINE_AA)
    return img


def _put(img, text, org, scale, colour, thick=1, anchor="left"):
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(text, font, scale, thick)
    x, y = org
    if anchor == "right":
        x -= tw
    cv2.putText(img, text, (x, y), font, scale, colour, thick, cv2.LINE_AA)
    return tw, th


def _fmt(v: float, signal: str) -> str:
    return f"{v:+.0f}°" if signal == "tilt" else f"{v:+.2f}"


def decorate_trace(pane: np.ndarray, data: AccelData, signal: str, yr, t_ns: int,
                   options: dict, label: str | None) -> np.ndarray:
    """Axis ticks on the left, the label and each series' value at the
    playhead along the top (the legend), drawn on the tile itself so they
    stay put while the trace scrolls."""
    h, w = pane.shape[:2]
    lo, hi = yr
    pad = max(2, h // 20)
    scale = max(0.35, min(0.6, h / 300))
    for g in _grid_values(lo, hi):
        y = int(round((hi - g) / (hi - lo) * (h - 1 - 2 * pad) + pad))
        if 10 < y < h - 4:
            unit = UNITS[signal] if signal == "tilt" else ""
            _put(pane, f"{g:g}{unit}", (4, y - 3), scale * 0.8, _MUTED)
    i = data.index_at(t_ns)
    x = w - 6
    y = int(18 * scale / 0.5)
    for key, name, colour in reversed(SERIES[signal]):
        text = f"{name} {_fmt(data.cols[key][i], signal) if i is not None else '--'}"
        tw, _th = _put(pane, text, (x, y), scale, colour, 1, anchor="right")
        x -= tw + int(14 * scale / 0.5)
    if label:
        unit = UNITS[signal]
        _put(pane, f"{label} ({unit})", (x - 4, y), scale, _TEXT, 1, anchor="right")
    return pane


# --------------------------------------------------------------------------- #
# Orientation                                                                 #
# --------------------------------------------------------------------------- #

# Headstage half-sizes along its X / Y / Z (an Intan RHD headstage is about
# 18 x 13 x 5 mm).
_HALF = np.array([1.0, 0.72, 0.28])
_AZ = math.radians(-35.0)       # view from front-left, a little above
_EL = math.radians(30.0)
_LIGHT = np.array([-0.3, -0.5, 0.8])
_LIGHT = _LIGHT / np.linalg.norm(_LIGHT)
_FACES = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
_FACE_NORMALS = np.array([[-1, 0, 0], [1, 0, 0], [0, -1, 0], [0, 1, 0], [0, 0, -1], [0, 0, 1]], float)
_CORNERS = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], float)


def rotation(pitch_deg: float, roll_deg: float) -> np.ndarray:
    """Headstage -> world rotation with heading held at 0: Ry(-pitch)
    Rx(roll). It puts world "up" at the gravity direction ephys_accel
    measured, u = (sin p, cos p sin r, cos p cos r) in headstage axes."""
    p, r = math.radians(pitch_deg), math.radians(roll_deg)
    ry = np.array([[math.cos(-p), 0, math.sin(-p)], [0, 1, 0],
                   [-math.sin(-p), 0, math.cos(-p)]])
    rx = np.array([[1, 0, 0], [0, math.cos(r), -math.sin(r)],
                   [0, math.sin(r), math.cos(r)]])
    return ry @ rx


def _view():
    """World -> camera: x right, y up, z towards the viewer."""
    ca, sa, ce, se = math.cos(_AZ), math.sin(_AZ), math.cos(_EL), math.sin(_EL)
    rz = np.array([[ca, -sa, 0], [sa, ca, 0], [0, 0, 1]])
    # world X (forward) points right-ish and away; Z up
    base = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], float)
    rel = np.array([[1, 0, 0], [0, ce, -se], [0, se, ce]])
    return rel @ base @ rz


_VIEW = _view()


def _shade(colour, k):
    return tuple(int(min(255, c * k)) for c in colour)


def orientation_pane(data: AccelData | None, t_ns: int, w: int, h: int,
                     options: dict, label: str | None) -> np.ndarray:
    pane = np.empty((h, w, 3), dtype=np.uint8)
    pane[:] = _BG
    s = min(w, h)
    scale_txt = max(0.35, min(0.6, s / 400))
    i = data.index_at(t_ns) if data is not None else None
    if i is None:
        _put(pane, "no headstage data here", (8, h // 2), scale_txt, _MUTED)
        if label:
            _put(pane, label, (8, int(20 * scale_txt / 0.5)), scale_txt, _TEXT)
        return pane
    pitch = float(data.cols["pitch_deg"][i])
    roll = float(data.cols["roll_deg"][i])
    rot = rotation(pitch, roll)
    cx, cy = w / 2, h / 2 + s * 0.04
    unit = s * 0.27

    def project(pw):
        c = _VIEW @ pw
        return np.array([cx + c[0] * unit, cy - c[1] * unit]), c[2]

    # Floor reference: a ring under the box, so tilt reads against it.
    floor_z = -1.15
    ring = [project(np.array([1.35 * math.cos(a), 1.35 * math.sin(a), floor_z]))[0]
            for a in np.linspace(0, 2 * math.pi, 48)]
    cv2.polylines(pane, [np.array(ring).round().astype(np.int32)], True, _GRID, 1, cv2.LINE_AA)
    for a in (0, math.pi / 2):
        p0 = project(np.array([-1.35 * math.cos(a), -1.35 * math.sin(a), floor_z]))[0]
        p1 = project(np.array([1.35 * math.cos(a), 1.35 * math.sin(a), floor_z]))[0]
        cv2.line(pane, tuple(p0.round().astype(int)), tuple(p1.round().astype(int)),
                 _GRID, 1, cv2.LINE_AA)

    corners = (rot @ (_CORNERS * _HALF).T).T
    proj = [project(c) for c in corners]
    pts2 = np.array([p[0] for p in proj])
    depth = np.array([p[1] for p in proj])
    normals = (rot @ _FACE_NORMALS.T).T
    view_dir = _VIEW.T @ np.array([0, 0, 1.0])
    base_col = (0x9C, 0x98, 0x92)
    top_col = (0xD4, 0xD0, 0xCA)
    faces = []
    for fi, f in enumerate(_FACES):
        if normals[fi] @ view_dir <= 0:
            continue
        light = 0.55 + 0.45 * max(0.0, float(normals[fi] @ _LIGHT))
        colour = top_col if fi == 5 else base_col
        faces.append((depth[list(f)].mean(), f, _shade(colour, light)))
    for _d, f, colour in sorted(faces, key=lambda q: q[0]):
        poly = pts2[list(f)].round().astype(np.int32)
        cv2.fillPoly(pane, [poly], colour, cv2.LINE_AA)
        cv2.polylines(pane, [poly], True, (0x30, 0x2C, 0x2C), 1, cv2.LINE_AA)

    # Headstage axes from the centre, labelled.
    o = project(np.zeros(3))[0]
    for axis, (name, colour) in enumerate((("X", _BLUE), ("Y", _ORANGE), ("Z", _AQUA))):
        e = np.zeros(3)
        e[axis] = _HALF[axis] + 0.45
        tip = project(rot @ e)[0]
        cv2.arrowedLine(pane, tuple(o.round().astype(int)), tuple(tip.round().astype(int)),
                        colour, 2, cv2.LINE_AA, tipLength=0.12)
        _put(pane, name, (int(tip[0]) + 4, int(tip[1]) - 4), scale_txt, colour)

    if options.get("arrow", True):
        dyn = rot @ data.dyn[i]
        mag = float(np.linalg.norm(dyn))
        if mag > 0.03:
            tip = project(dyn / mag * min(1.6, 0.4 + mag * 2.0))[0]
            cv2.arrowedLine(pane, tuple(o.round().astype(int)), tuple(tip.round().astype(int)),
                            _TEXT, 1, cv2.LINE_AA, tipLength=0.15)

    lh = int(20 * scale_txt / 0.5)
    y = lh
    if label:
        _put(pane, label, (8, y), scale_txt, _TEXT)
        y += lh
    _put(pane, f"pitch {pitch:+.0f}°  roll {roll:+.0f}°", (8, y), scale_txt, _TEXT)
    _put(pane, "heading not measured (fixed)", (8, h - 8), scale_txt * 0.85, _MUTED)
    if options.get("arrow", True):
        _put(pane, "arrow: movement", (w - 8, h - 8), scale_txt * 0.85, _MUTED, anchor="right")
    return pane
