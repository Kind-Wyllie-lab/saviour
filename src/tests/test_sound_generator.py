"""Tests for src/shared/sound_generator.py."""

import numpy as np
import pytest

from src.shared import sound_analysis as sa
from src.shared import sound_generator as g


def _octave_db(x, lo, ref=1000):
    p = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / g.RATE)

    def band(a):
        return p[(f >= a) & (f < 2 * a)].sum()

    return 10 * np.log10(band(lo) / band(ref))


class TestNoise:
    # Energy per octave relative to 1-2 kHz: white +3 dB/oct, pink 0,
    # brown -3, blue +6, violet +9.
    @pytest.mark.parametrize("colour,per_octave", [("white", 3), ("pink", 0), ("brown", -3),
                                                   ("blue", 6), ("violet", 9)])
    def test_colour_slopes(self, colour, per_octave):
        x, _ = g.generate({"type": "noise", "colour": colour, "duration_s": 10,
                           "low_hz": 100, "high_hz": 20000, "level_dbfs": -30, "seed": 3})
        assert _octave_db(x, 4000) == pytest.approx(2 * per_octave, abs=0.5)
        assert _octave_db(x, 250) == pytest.approx(-2 * per_octave, abs=0.7)

    def test_rms_level_band_and_seed(self):
        x, p = g.generate({"type": "noise", "level_dbfs": -24, "low_hz": 2000,
                           "high_hz": 8000, "duration_s": 2, "ramp_ms": 0})
        assert isinstance(p["seed"], int) and p["generator_version"] == g.GENERATOR_VERSION
        a = sa.analyse(x, g.RATE, render=False)
        assert a["rms_dbfs"] == pytest.approx(-24, abs=0.05)
        lo, hi = a["spectrum"]["band_hz"]
        assert 1900 <= lo and hi <= 8100
        again, _ = g.generate(p)
        assert np.array_equal(x, again)                  # frozen by the seed

    def test_notch(self):
        x, _ = g.generate({"type": "noise", "notch_low_hz": 4000, "notch_high_hz": 6000,
                           "duration_s": 2, "seed": 1, "ramp_ms": 0})
        p = np.abs(np.fft.rfft(x)) ** 2
        f = np.fft.rfftfreq(len(x), 1 / g.RATE)
        assert p[(f > 4100) & (f < 5900)].max() < 1e-6 * p.max()

    def test_clipping_refused_not_clipped(self):
        with pytest.raises(g.GeneratorError, match="lower the level"):
            g.generate({"type": "noise", "level_dbfs": -3})


class TestOtherTypes:
    def test_tone_level_is_rms_and_ramps(self):
        x, p = g.generate({"type": "tone", "freq_hz": 5000, "level_dbfs": -20, "ramp_ms": 5})
        a = sa.analyse(x, g.RATE, render=False)
        assert a["peak_dbfs"] == pytest.approx(-16.99, abs=0.05)      # RMS + 3.01 dB
        lv = g.levels(x, p)
        assert lv["plateau_rms_dbfs"] == pytest.approx(-20, abs=0.01)
        assert lv["headroom_db"] == pytest.approx(16.99, abs=0.05)
        assert a["spectrum"]["dominant_hz"] == pytest.approx(5000, abs=5)
        assert not a["abrupt_start"] and not a["abrupt_end"]
        assert abs(x[0]) < 1e-3

    def test_single_click_level_is_peak(self):
        x, p = g.generate({"type": "click", "click_us": 100, "polarity": "negative",
                           "level_dbfs": -6, "duration_s": 5, "ramp_ms": 3})
        assert len(x) == 5 and np.allclose(x, -(10 ** (-6 / 20)))   # 100 µs at 48 kHz
        assert p["duration_s"] == pytest.approx(5 / g.RATE, abs=1e-8) and p["ramp_ms"] == 0
        assert g.default_name(p) == "click_100us_negative_-6dBpk.wav"

    @pytest.mark.parametrize("params", [
        {"type": "noise", "duration_s": 0.04, "ramp_ms": 2, "seed": 4},
        {"type": "tone", "duration_s": 0.05, "ramp_ms": 10},
        {"type": "am_tone", "duration_s": 1, "ramp_ms": 100},
        {"type": "fm_sweep", "duration_s": 0.5, "ramp_ms": 20},
    ])
    def test_short_burst_level_measured_on_its_plateau(self, params):
        x, p = g.generate({**params, "level_dbfs": -24})
        assert g.levels(x, p)["plateau_rms_dbfs"] == pytest.approx(-24, abs=0.01)
        whole = 20 * np.log10(np.sqrt(np.mean(x.astype(float) ** 2)))
        assert whole < -24                       # the ramps pull the whole-file RMS down

    def test_preview_renders_at_most_max_s(self):
        x, p = g.generate({"type": "noise", "duration_s": 60, "seed": 1}, max_s=10)
        assert len(x) == 10 * g.RATE and p["duration_s"] == 60

    def test_sweep_spans_its_band(self):
        x, _ = g.generate({"type": "fm_sweep", "start_hz": 2000, "end_hz": 16000,
                           "sweep": "linear", "duration_s": 1})
        a = sa.analyse(x, g.RATE)
        spec = np.array(a["spectrogram"]["db"])
        rows = spec.argmax(axis=1)
        assert rows[5] < rows[len(rows) // 2] < rows[-5]        # rising

    def test_am_depth(self):
        x, _ = g.generate({"type": "am_tone", "carrier_hz": 4000, "mod_hz": 10,
                           "depth": 1.0, "ramp_ms": 0, "duration_s": 1})
        env = np.abs(x).reshape(-1, 48).max(axis=1)               # 1 ms blocks
        assert env.min() < 0.01 * env.max()


class TestValidation:
    @pytest.mark.parametrize("params,msg", [
        ({"type": "buzz"}, "type must be"),
        ({"type": "tone", "freq_hz": 30000}, "freq_hz must be between"),
        ({"type": "tone", "duration_s": 0.004, "ramp_ms": 5}, "ramps are longer"),
        ({"type": "noise", "colour": "green"}, "colour must be"),
        ({"type": "noise", "low_hz": 5000, "high_hz": 5005}, "10 Hz above"),
        ({"type": "noise", "notch_low_hz": 3000}, "needs both"),
        ({"type": "gap"}, "type must be"),
        ({"type": "am_tone", "carrier_hz": 21000, "mod_hz": 1000}, r"carrier \+ modulation"),
        ({"type": "click", "polarity": "alternating"}, "positive or negative"),
        ({"type": "fm_sweep", "start_hz": 4000, "end_hz": 4000}, "use a tone"),
    ])
    def test_refusals(self, params, msg):
        with pytest.raises(g.GeneratorError, match=msg):
            g.generate(params)

    def test_every_problem_listed(self):
        with pytest.raises(g.GeneratorError) as e:
            g.normalise({"type": "tone", "freq_hz": 1, "level_dbfs": 5})
        assert len(e.value.errors) == 2


def test_names_are_library_names():
    for t in g.TYPES:
        _, p = g.generate({"type": t, "level_dbfs": -30})
        assert sa.NAME_RE.match(g.default_name(p)), g.default_name(p)


@pytest.mark.parametrize("bits,tol", [(24, 2e-7), (16, 4e-5)])
def test_wav_round_trip(bits, tol):
    x, _ = g.generate({"type": "tone", "level_dbfs": -6})
    y, rate, fmt = sa.read_wav(g.wav_bytes(x, bits=bits))
    assert rate == g.RATE and fmt["format"] == f"pcm{bits}" and fmt["channels"] == 1
    assert np.allclose(y[:, 0], x, atol=tol)
