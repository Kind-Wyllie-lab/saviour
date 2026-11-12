"""
Tests for src/controller/accel_tiles.py: the headstage accelerometer
compose tiles (plans/ephys-accelerometer.md, phase 2).
"""

import json
import math

import numpy as np
import pytest

from src.controller import accel_tiles as at
from src.controller import compose, compose_layout

T0 = 1_794_393_128_000_000_000
FS = 100


def _write(session, n=1000, aligned=True, pitch=lambda t: 20.0, roll=lambda t: -10.0,
           az=lambda t: 1.0):
    d = session / "_ephys" / "accel" / "A1"
    d.mkdir(parents=True)
    lines = ["timestamp_ns,oe_s,ax_g,ay_g,az_g,pitch_deg,roll_deg,odba_g,invalid"]
    for i in range(n):
        t = i / FS
        ts = str(T0 + i * 10_000_000) if aligned else ""
        lines.append(f"{ts},{t:.4f},0.1,0.0,{az(t):.4f},{pitch(t):.2f},{roll(t):.2f},0.05,0")
    (d / "accel.csv").write_text("\n".join(lines) + "\n")
    (d / "summary.json").write_text(json.dumps({"aligned": aligned}))
    return session


def test_load_and_index(tmp_path):
    data = at.load(str(_write(tmp_path)), "ephys/accel/A1")
    assert data.start_ns == T0 and len(data.t_ns) == 1000
    assert data.index_at(T0 + 5_004_000_000) == 500
    assert data.index_at(T0 - 10**9) is None
    assert data.index_at(data.end_ns + 10**9) is None
    assert data.dyn.shape == (1000, 3)


def test_unaligned_or_missing_output_is_refused(tmp_path):
    with pytest.raises(at.AccelTileError, match="run Accelerometer"):
        at.load(str(tmp_path), "ephys/accel/A1")
    with pytest.raises(at.AccelTileError, match="isn't aligned"):
        at.load(str(_write(tmp_path, aligned=False)), "ephys/accel/A1")


@pytest.mark.parametrize("pitch, roll", [(0, 0), (30, 0), (0, -40), (45, 60), (-70, 120)])
def test_rotation_puts_measured_gravity_up(pitch, roll):
    p, r = math.radians(pitch), math.radians(roll)
    # ephys_accel: gravity ("up") in headstage axes for this pitch/roll
    u = np.array([math.sin(p), math.cos(p) * math.sin(r), math.cos(p) * math.cos(r)])
    np.testing.assert_allclose(at.rotation(pitch, roll) @ u, [0, 0, 1], atol=1e-9)


def _rows_with(img_col, colour, tol=60):
    return np.flatnonzero(np.abs(img_col.astype(int) - np.array(colour)).sum(1) < tol)


def test_strip_draws_the_value_where_expected(tmp_path):
    data = at.load(str(_write(tmp_path, az=lambda t: 1.0)), "ephys/accel/A1")
    h = 101
    img = at.render_strip(data, "xyz", T0, 200, h, 20.0, (-2.0, 2.0))
    pad = max(2, h // 20)
    y = round((2.0 - 1.0) / 4.0 * (h - 1 - 2 * pad) + pad)
    hits = _rows_with(img[:, 100], at._AQUA)
    assert hits.size and abs(hits.mean() - y) <= 1.5
    # Before the recording starts: background and grid only.
    before = at.render_strip(data, "xyz", T0 - 100 * 10**9, 50, h, 20.0, (-2.0, 2.0))
    assert not (np.abs(before.astype(int) - np.array(at._AQUA)).sum(2) < 60).any()


def test_strip_envelope_when_pixels_hold_many_samples(tmp_path):
    data = at.load(str(_write(tmp_path, n=6000, az=lambda t: math.sin(t * 20))),
                   "ephys/accel/A1")
    img = at.render_strip(data, "xyz", T0, 60, 80, 1.0, (-2.0, 2.0))
    hits = _rows_with(img[:, 30], at._AQUA, tol=80)
    assert hits.max() - hits.min() > 20      # each column spans the ±1 g swing


def test_y_range_auto_and_fixed(tmp_path):
    data = at.load(str(_write(tmp_path, pitch=lambda t: 10 + t)), "ephys/accel/A1")
    assert at.y_range(data, "tilt", {"y_min": -5, "y_max": 5}, T0, data.end_ns) == (-5, 5)
    lo, hi = at.y_range(data, "tilt", {"y_auto": True}, T0, data.end_ns)
    assert lo < -10 and 19 < hi < 25          # roll -10 .. pitch ~20, with margin
    assert at.y_range(data, "odba", {}, T0, data.end_ns) == at.DEFAULT_RANGE["odba"]


def test_orientation_pane(tmp_path):
    data = at.load(str(_write(tmp_path)), "ephys/accel/A1")
    pane = at.orientation_pane(data, T0 + 10**9, 320, 240, {"arrow": True}, "Headstage A1")
    assert pane.shape == (240, 320, 3)
    assert (pane != np.array(at._BG)).any(axis=2).mean() > 0.05   # something drawn
    outside = at.orientation_pane(data, T0 - 10**10, 320, 240, {}, None)
    assert (outside != np.array(at._BG)).any(axis=2).mean() < 0.05


def test_render_tile_scrolls_with_time(tmp_path):
    session = _write(tmp_path, n=3000, az=lambda t: 1.0 if t < 15 else -1.0)
    doc = compose_layout.validate_layout({
        "canvas": {"width": 400, "height": 200},
        "tiles": [{"kind": "camera", "source": "c", "rect": [0, 0, 100, 100]},
                  {"id": "t", "kind": "accel_trace", "source": "ephys/accel/A1",
                   "rect": [0, 100, 400, 100], "options": {"window_s": 4}}]})
    fields = compose.ComposeWorker._accel_render_tile(
        str(session), doc["tiles"][1], T0, T0 + 30 * 10**9, 30.0)
    assert fields["label_text"] is None

    def row_at_playhead(t_s):
        pane = fields["draw"](T0 + int(t_s * 1e9), 400, 100)
        return _rows_with(pane[:, 190], at._AQUA).mean()   # just left of the playhead

    assert row_at_playhead(10) < 50 < row_at_playhead(20)   # +1 g high, -1 g low
