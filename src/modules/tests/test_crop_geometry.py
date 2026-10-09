"""
Tests for src/modules/crop_geometry.py, using the case reproduced on
hardware 2026-10-09: IMX477 sensor mode 2 (2028x1520, crop_limits the full
4056x3040 array, 4:3) recording 1920x1080 (16:9).
"""

import pytest

from src.modules.crop_geometry import (
    clamp_rect,
    default_crop,
    fit_aspect,
    legacy_to_normalised,
    normalised_to_sensor,
    output_size_for_crop,
    validate_normalised,
)

LIMITS = (0, 0, 4056, 3040)
MODE_SIZE = (2028, 1520)
OUT = (1920, 1080)
ASPECT_16_9 = 1920 / 1080


def _aspect(r):
    return r[2] / r[3]


def test_default_crop_is_the_centred_16_9_band_not_the_full_4_3_area():
    x, y, w, h = default_crop(LIMITS, ASPECT_16_9)
    assert w == 4056
    assert abs(_aspect((x, y, w, h)) - ASPECT_16_9) < 0.002
    assert y == (3040 - h) // 2          # centred vertically
    assert h < 3040                      # NOT the full 4:3 area


def test_default_crop_for_a_matching_aspect_is_the_whole_area():
    assert default_crop(LIMITS, 4056 / 3040) == LIMITS


def test_default_crop_for_portrait_output_is_a_centred_column():
    x, y, w, h = default_crop(LIMITS, 9 / 16)
    assert h == 3040 and abs(_aspect((x, y, w, h)) - 9 / 16) < 0.002
    assert x == (4056 - w) // 2


def test_legacy_whole_preview_crop_maps_to_the_default_view():
    """The bug: a crop of the whole 640x360 preview used to become the full
    4:3 area. It must map to exactly what the preview showed."""
    norm = legacy_to_normalised(
        {"x": 0, "y": 0, "width": 640, "height": 360,
         "preview_width": 640, "preview_height": 360}, LIMITS)
    rect = normalised_to_sensor(norm, LIMITS)
    band = default_crop(LIMITS, ASPECT_16_9)
    assert all(abs(a - b) <= 2 for a, b in zip(rect, band, strict=True))


def test_legacy_crop_keeps_its_aspect():
    """One scale factor: a square drawn on the preview stays square."""
    norm = legacy_to_normalised(
        {"x": 220, "y": 80, "width": 200, "height": 200,
         "preview_width": 640, "preview_height": 360}, LIMITS)
    rect = normalised_to_sensor(norm, LIMITS)
    assert abs(_aspect(rect) - 1.0) < 0.01


def test_fit_aspect_trims_the_long_side_around_the_centre():
    r = fit_aspect((1000, 1000, 2000, 1000), 1.0, LIMITS)
    assert r[2] == r[3] == 1000
    assert r[0] + r[2] / 2 == pytest.approx(2000, abs=1)   # centre kept


def test_fit_aspect_stays_inside_limits():
    r = fit_aspect((3900, 2900, 400, 400), 16 / 9, LIMITS)
    x, y, w, h = r
    assert x >= 0 and y >= 0 and x + w <= 4056 and y + h <= 3040


def test_clamp_rect_shifts_then_shrinks():
    assert clamp_rect((4000, 3000, 200, 200), LIMITS) == (3856, 2840, 200, 200)
    assert clamp_rect((-10, -10, 9000, 9000), LIMITS) == LIMITS


@pytest.mark.parametrize("bad", [
    {"x": 0, "y": 0, "width": 0.01, "height": 0.5},     # too small
    {"x": 0.5, "y": 0, "width": 0.6, "height": 0.5},    # off the right edge
    {"x": -0.1, "y": 0, "width": 0.5, "height": 0.5},
    {"x": "a", "y": 0, "width": 0.5, "height": 0.5},
    {"x": float("nan"), "y": 0, "width": 0.5, "height": 0.5},
    {"y": 0, "width": 0.5, "height": 0.5},
])
def test_validate_normalised_rejects(bad):
    with pytest.raises(ValueError):
        validate_normalised(bad)


def test_validate_normalised_accepts_and_trims_rounding():
    v = validate_normalised({"x": 0.5, "y": 0.25, "width": 0.5000000001, "height": 0.5})
    assert v["x"] + v["width"] <= 1.0


@pytest.mark.parametrize("aspect", [1.0, 4 / 3, 16 / 9, 3 / 4, 9 / 16])
def test_output_size_follows_the_crop_aspect(aspect):
    # A large central crop of the given aspect.
    rect = fit_aspect(LIMITS, aspect, LIMITS)
    w, h = output_size_for_crop(rect, LIMITS, MODE_SIZE, OUT)
    assert w % 32 == 0 and h % 2 == 0
    assert abs(w / h - aspect) / aspect < 0.03
    assert w <= MODE_SIZE[0] and h <= MODE_SIZE[1]
    # The uncropped pixel budget, or the pixels the mode actually reads for
    # that region if fewer (a 9:16 column of a 4:3 sensor is ~1.3 MP).
    native = (rect[2] * MODE_SIZE[0] / LIMITS[2]) * (rect[3] * MODE_SIZE[1] / LIMITS[3])
    expect = min(1920 * 1080, native)
    assert 0.9 * expect <= w * h <= 1.01 * expect


def test_output_size_never_upscales_a_small_crop():
    # 400x400 sensor px in a 2x-binned mode = 200x200 px actually read.
    w, h = output_size_for_crop((1000, 1000, 400, 400), LIMITS, MODE_SIZE, OUT)
    assert w * h <= 200 * 200 * 1.01
    assert w >= 64 and h >= 64
