"""
Tests for tools/apa_track.py: the shock-zone geometry (which must match the
apa_camera overlay the operator shocks from) and the entry/pulse analysis,
on synthetic tracks.
"""

import importlib.util
import math
import os

import numpy as np
import pytest

pytest.importorskip("cv2")

_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "tools", "apa_track.py")
_spec = importlib.util.spec_from_file_location("apa_track", _PATH)
apa_track = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(apa_track)

SIZE = 1080


def _cfg(start=180, span=60, inner=0.3, radius=0.68):
    return {
        "mask": {"mask_radius": radius, "mask_center_x_offset": 0, "mask_center_y_offset": 0},
        "shock_zone": {"shock_zone_start_angle_deg": start, "shock_zone_angle_span_deg": span,
                       "shock_zone_inner_offset": inner},
    }


def _point(g, frac_r, config_deg):
    """A point at frac_r of the arena radius, at a config-convention angle
    (0 = top of frame, increasing clockwise on screen)."""
    a = math.radians(config_deg - 90)
    r = frac_r * g["outer_r"]
    return np.array([g["cx"] + r * math.cos(a)]), np.array([g["cy"] + r * math.sin(a)])


def _inside(g, frac_r, deg):
    return bool(apa_track.in_zone(*_point(g, frac_r, deg), g)[0])


def test_default_zone_is_bottom_sector():
    g = apa_track.zone_geometry(_cfg(), SIZE, SIZE)
    assert g["outer_r"] == int(0.5 * 0.68 * SIZE)
    assert _inside(g, 0.6, 180)        # straight down
    assert _inside(g, 0.6, 155)        # within +-30 deg
    assert not _inside(g, 0.6, 145)    # outside the span
    assert not _inside(g, 0.6, 0)      # top
    assert not _inside(g, 0.2, 180)    # inside the inner offset
    assert not _inside(g, 1.1, 180)    # past the arena edge


def test_zone_crossing_zero_degrees_wraps():
    # Config 90 deg (right of frame) puts the arc at -30..30 in OpenCV angles;
    # both sides of 0 must count.
    g = apa_track.zone_geometry(_cfg(start=90), SIZE, SIZE)
    assert _inside(g, 0.6, 70)
    assert _inside(g, 0.6, 110)
    assert not _inside(g, 0.6, 270)


def test_zone_at_top():
    g = apa_track.zone_geometry(_cfg(start=0), SIZE, SIZE)
    assert _inside(g, 0.6, 0)
    assert _inside(g, 0.6, 350)
    assert not _inside(g, 0.6, 180)


def test_nan_position_is_not_in_zone():
    g = apa_track.zone_geometry(_cfg(), SIZE, SIZE)
    assert not apa_track.in_zone(np.array([np.nan]), np.array([np.nan]), g)[0]


def test_runs():
    assert apa_track._runs(np.array([0, 1, 1, 0, 1], bool)) == [(1, 3), (4, 5)]
    assert apa_track._runs(np.zeros(3, bool)) == []


def test_analyse_counts_entries_and_pulses():
    cfg = _cfg()
    g = apa_track.zone_geometry(cfg, SIZE, SIZE)
    fps, t0 = 10, 1_000_000_000_000
    # 60 s: out 0-10, in 10-15, out 15-30, in 30-32, out 32-60.
    rows = []
    for i in range(60 * fps):
        s = i / fps
        deg = 180 if (10 <= s < 15 or 30 <= s < 32) else 0
        x, y = _point(g, 0.6, deg)
        rows.append((i, t0 + int(s * 1e9), float(x[0]), float(y[0]), 1000))
    shocks = [(t0 + int(11e9), "SENDING_SHOCK"), (t0 + int(11e9) + 1, "SHOCK_DELIVERY"),
              (t0 + int(45e9), "SENDING_SHOCK")]
    result = {"geom": g, "rows": rows}
    s = apa_track.analyse(result, shocks, arena_cm=None)
    assert s["entries"] == 2
    assert s["time_in_zone_s"] == pytest.approx(7.0, abs=0.2)
    assert s["latency_first_entry_s"] == pytest.approx(10.0, abs=0.2)
    assert s["max_time_avoided_s"] == pytest.approx(28.0, abs=0.3)
    assert s["shock_pulses"] == 2
    assert s["shock_deliveries"] == 1
    assert s["pulses_in_zone"] == 1
    assert s["pulses_outside_zone"] == 1
    assert s["entries_shocked"] == 1
    assert s["entries_not_shocked"] == 1
    assert s["median_entry_to_shock_s"] == pytest.approx(1.0, abs=0.15)
