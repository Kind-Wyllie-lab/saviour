"""
Tests for tools/apa_autolabel.py: where-is-the-rat bands, frame spacing,
rat ids from session names, and re-finding the offline blob's box
(including a blob cut off by the mask edge).
"""

import importlib.util
import itertools
import math
import os
import sys

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

_TOOLS = os.path.join(os.path.dirname(__file__), "..", "..", "tools")
sys.path.insert(0, _TOOLS)
_spec = importlib.util.spec_from_file_location("apa_autolabel",
                                               os.path.join(_TOOLS, "apa_autolabel.py"))
al = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(al)
apa_track = al.apa_track

G = {"cx": 500.0, "cy": 500.0, "outer_r": 400.0, "inner_r": 120.0,
     "start_deg": 60.0, "span_deg": 60.0}   # OpenCV angles: zone centred straight down


def _at(frac_r, deg):
    a = math.radians(deg)
    return [G["cx"] + frac_r * G["outer_r"] * math.cos(a),
            G["cy"] + frac_r * G["outer_r"] * math.sin(a)]


def test_rat_id_from_both_naming_schemes():
    assert al.rat_id("Nlgn3FlexCamk2a16_2_APA-149-conflict-1-113009") == "149"
    assert al.rat_id("Nlgn3FlexNew_Test-853-training-2-091049") == "853"


def test_band_of():
    off = np.array([_at(0.2, 270), _at(0.6, 270), _at(0.9, 270),
                    _at(0.6, 90), _at(0.6, 60.5)], float)
    z = apa_track.in_zone(off[:, 0], off[:, 1], G)
    assert list(al.band_of(off, G, z)) == ["centre", "middle", "rim", "zone", "zone_edge"]


def test_spaced_sample_keeps_the_gap_and_extends_taken():
    rng = np.random.default_rng(0)
    taken = [100]
    got = al.spaced_sample(np.arange(0, 300), 50, taken, 30, rng)
    allp = sorted(taken)
    assert all(b - a >= 30 for a, b in itertools.pairwise(allp))
    assert 100 in taken and set(got) <= set(taken)
    assert all(abs(g - 100) >= 30 for g in got)


def _tracker(radius):
    bg = np.zeros((540, 540), np.uint8)
    yy, xx = np.mgrid[:540, :540]
    arena = np.hypot(xx - 270, yy - 270) <= radius
    return apa_track.RatTracker(bg, arena, 15, 150, 60.0, centre=(270, 270), radius=radius)


def test_find_box_returns_the_blob_at_the_stored_position():
    t = _tracker(200)
    f = np.zeros((540, 540), np.uint8)
    cv2.rectangle(f, (250, 250), (310, 280), 200, -1)
    cv2.rectangle(f, (100, 100), (160, 130), 200, -1)   # a second, decoy blob
    scale = 0.5                                          # full-res frame is 1080
    box, clipped = al.find_box(t, f, (560.0, 530.0), scale)
    x0, y0, x1, y1 = box
    assert abs(x0 - 500) < 12 and abs(x1 - 622) < 12 and abs(y0 - 500) < 12 and abs(y1 - 562) < 12
    assert not clipped
    assert al.find_box(t, f, (np.nan, np.nan), scale) == (None, False)
    assert al.find_box(t, f, (1000.0, 1000.0), scale) == (None, False)


def test_find_box_flags_a_blob_cut_off_by_the_mask():
    t = _tracker(150)
    f = np.zeros((540, 540), np.uint8)
    cv2.rectangle(f, (400, 250), (460, 290), 200, -1)   # straddles the r=150 edge
    box, clipped = al.find_box(t, f, (2 * 412.0, 2 * 270.0), 0.5)
    assert box is not None and clipped


def test_write_calib_takes_train_images_only(tmp_path):
    rows = []
    for k, (kind, split) in enumerate([("positive", "train")] * 20 + [("negative", "train")] * 5
                                      + [("positive", "valid")] * 5 + [("review", "train")] * 5):
        rel = f"x/{split}/images/{kind}_{k}.jpg"
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"")
        rows.append({"image": rel, "set": kind, "split": split})
    assert al.write_calib(rows, str(tmp_path), 10, 0) == 10
    got = sorted(p.name for p in (tmp_path / "calib").iterdir())
    assert sum(g.startswith("negative") for g in got) == 1
    assert all(g.startswith(("positive", "negative")) for g in got)
    assert not any(int(g.split("_")[1][:-4]) >= 25 for g in got)   # no valid/ or review


def test_box_fill():
    assert al.box_fill(50.0, (0, 0, 10, 10)) == 0.5
    assert al.box_fill(10.0, (5, 5, 5, 5)) == 10.0   # degenerate box doesn't divide by zero
