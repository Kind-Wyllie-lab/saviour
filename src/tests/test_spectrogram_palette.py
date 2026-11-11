"""src/shared/spectrogram_palette.py: palettes, LUTs, call bands."""

import json
import os

import cv2
import numpy as np
import pytest

from src.shared import spectrogram_palette as sp

ALARM = {"label": "Alarm (22 kHz)", "lo_hz": 18000, "hi_hz": 32000,
         "palette": "alarm_blue"}
APPETITIVE = {"label": "Appetitive (50 kHz)", "lo_hz": 45000, "hi_hz": 96000,
              "palette": "appetitive_orange"}


def test_every_builtin_palette_is_valid_and_default_exists():
    builtins = sp.builtin_palettes()
    assert sp.default_palette() in builtins
    for name, stops in builtins.items():
        sp.validate_stops(stops)
        assert sp.lut(stops).shape == (256, 3), name


def test_presets_use_builtin_palettes_and_the_agreed_edges():
    presets = {p["id"]: p for p in sp.band_presets()}
    assert (presets["rat_alarm"]["lo_hz"], presets["rat_alarm"]["hi_hz"]) == (18000, 32000)
    assert (presets["rat_appetitive"]["lo_hz"],
            presets["rat_appetitive"]["hi_hz"]) == (45000, 96000)
    sp.parse_bands(list(presets.values()))  # valid, non-overlapping


@pytest.mark.parametrize("name", ["inferno", "magma", "viridis", "turbo", "jet"])
def test_sampled_palettes_match_opencv_within_tolerance(name):
    """The monitor used cv2.applyColorMap before; the stop tables must not
    change its look by more than the generator's tolerance (+1 rounding)."""
    ref = cv2.applyColorMap(np.arange(256, dtype=np.uint8).reshape(-1, 1),
                            getattr(cv2, f"COLORMAP_{name.upper()}"))[:, 0]
    ours = sp.lut(sp.builtin_palettes()[name])
    assert np.abs(ours.astype(int) - ref.astype(int)).max() <= 3


def test_lut_endpoints_and_bgr_order():
    lut = sp.lut([[0, "#000000"], [1, "#ff8000"]])
    assert lut[0].tolist() == [0, 0, 0]
    assert lut[255].tolist() == [0, 128, 255]  # BGR
    assert np.all(np.diff(lut[:, 2].astype(int)) >= 0)


@pytest.mark.parametrize("stops", [
    [[0, "#000000"]],                              # too few
    [[0.1, "#000000"], [1, "#ffffff"]],            # doesn't start at 0
    [[0, "#000000"], [0.9, "#ffffff"]],            # doesn't end at 1
    [[0, "#000000"], [0.5, "#111111"], [0.5, "#222222"], [1, "#ffffff"]],
    [[0, "black"], [1, "#ffffff"]],                # not hex
    "inferno",
])
def test_bad_stops_rejected(stops):
    with pytest.raises(sp.PaletteError):
        sp.validate_stops(stops)


def test_resolve_stops_prefers_custom_and_falls_back_to_default():
    custom = [[0, "#000000"], [1, "#00ff00"]]
    assert sp.resolve_stops("magma", custom)[-1] == (1.0, "#00ff00")
    assert sp.resolve_stops("magma") == sp.validate_stops(sp.builtin_palettes()["magma"])
    default = sp.validate_stops(sp.builtin_palettes()[sp.default_palette()])
    assert sp.resolve_stops("no-such-palette") == default
    assert sp.resolve_stops("magma", [[0, "bad"]]) == sp.resolve_stops("magma")


def test_parse_bands_sorts_skips_disabled_and_rejects_overlap():
    bands = sp.parse_bands([APPETITIVE, ALARM, {**ALARM, "lo_hz": 1000,
                                                 "hi_hz": 2000, "enabled": False}])
    assert [b.lo_hz for b in bands] == [18000, 45000]
    with pytest.raises(sp.PaletteError, match="overlap"):
        sp.parse_bands([ALARM, {**APPETITIVE, "lo_hz": 30000}])
    with pytest.raises(sp.PaletteError):
        sp.parse_bands([{**ALARM, "palette": "nope"}])
    with pytest.raises(sp.PaletteError):
        sp.parse_bands([{**ALARM, "hi_hz": 10000}])
    with pytest.raises(sp.PaletteError):
        sp.parse_bands([{"lo_hz": i * 1000, "hi_hz": i * 1000 + 500,
                         "palette": "inferno"} for i in range(sp.MAX_BANDS + 1)])


def test_linear_row_hz_top_row_is_high_frequency():
    rows = sp.linear_row_hz(100, 0, 100_000)
    assert rows[0] == pytest.approx(99_500) and rows[-1] == pytest.approx(500)


def test_colourise_without_bands_uses_base_palette():
    grey = np.tile(np.arange(256, dtype=np.uint8), (4, 1))
    base = sp.resolve_stops("viridis")
    out = sp.colourise(grey, base, [], sp.linear_row_hz(4, 0, 96000))
    assert np.array_equal(out[2], sp.lut(base))


def test_colourise_bands_coloured_and_outside_grey():
    h = 96  # 1 kHz per row over 0-96 kHz
    grey = np.full((h, 10), 180, dtype=np.uint8)
    row_hz = sp.linear_row_hz(h, 0, 96000)
    bands = sp.parse_bands([ALARM, APPETITIVE])
    out = sp.colourise(grey, sp.resolve_stops("inferno"), bands, row_hz)

    def at(khz):
        return out[int(np.argmin(np.abs(row_hz - khz * 1000))), 0].astype(int)

    b, g, r = at(25)                    # alarm: blue dominant
    assert b > r and b > g
    b, g, r = at(60)                    # appetitive: orange (red > green > blue)
    assert r > g > b
    for khz in (10, 38, 40):           # outside both bands: grey
        b, g, r = at(khz)
        assert b == g == r


def test_colourise_rejects_mismatched_rows():
    with pytest.raises(ValueError):
        sp.colourise(np.zeros((5, 5), np.uint8), sp.resolve_stops("inferno"), [],
                     sp.linear_row_hz(4, 0, 1000))


def test_json_is_what_the_generator_writes():
    """Guard against hand edits: the committed JSON must parse and carry the
    generator's note (re-run tools/gen_spectrogram_palettes.py instead)."""
    path = os.path.join(os.path.dirname(sp.__file__), "spectrogram_palettes.json")
    with open(path) as f:
        doc = json.load(f)
    assert doc["version"] == 1 and "gen_spectrogram_palettes.py" in doc["_note"]
