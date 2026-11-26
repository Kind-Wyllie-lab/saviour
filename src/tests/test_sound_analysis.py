"""Tests for src/shared/sound_analysis.py and src/shared/resample.py."""

import io

import numpy as np
import pytest
import soundfile

from src.shared import sound_analysis as sa
from src.shared.resample import resample


def _wav(x, rate, subtype="PCM_16"):
    buf = io.BytesIO()
    soundfile.write(buf, x, rate, format="WAV", subtype=subtype)
    return buf.getvalue()


def _tone(freq, rate, secs=1.0, amp=0.5, ramp_s=0.005, channels=2):
    t = np.arange(int(rate * secs)) / rate
    x = amp * np.sin(2 * np.pi * freq * t)
    if ramp_s:
        n = int(rate * ramp_s)
        r = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))
        x[:n] *= r
        x[-n:] *= r[::-1]
    return np.repeat(x[:, None], channels, axis=1).astype(np.float32)


class TestReadWav:
    @pytest.mark.parametrize("subtype,kind", [("PCM_16", "pcm16"), ("PCM_24", "pcm24"),
                                              ("PCM_32", "pcm32"), ("FLOAT", "float32"),
                                              ("PCM_U8", "pcm8")])
    def test_formats_round_trip(self, subtype, kind):
        x = _tone(1000, 48000, 0.1)
        y, rate, fmt = sa.read_wav(_wav(x, 48000, subtype))
        assert rate == 48000 and fmt["format"] == kind and fmt["channels"] == 2
        tol = 0.01 if kind == "pcm8" else 1e-3
        assert np.allclose(y, x, atol=tol)

    def test_extensible_multichannel(self):
        x = np.zeros((100, 4), np.float32)
        x[:, 3] = 0.25
        y, _, fmt = sa.read_wav(_wav(x, 96000, "PCM_24"))
        assert fmt["channels"] == 4 and np.allclose(y[:, 3], 0.25, atol=1e-5)

    @pytest.mark.parametrize("data", [b"", b"ID3...." * 10, b"RIFF\0\0\0\0WAVE"])
    def test_refuses_non_wav(self, data):
        with pytest.raises(sa.SoundFileError):
            sa.read_wav(data)

    def test_refuses_compressed_wav(self):
        buf = io.BytesIO()
        soundfile.write(buf, np.zeros(800, np.float32), 8000, format="WAV", subtype="ULAW")
        with pytest.raises(sa.SoundFileError, match="convert"):
            sa.read_wav(buf.getvalue())


class TestAnalyse:
    def test_clean_tone(self):
        a = sa.analyse_bytes(_wav(_tone(5000, 48000, amp=0.1), 48000, "FLOAT"))
        assert a["sample_rate"] == 48000 and a["duration_s"] == 1.0
        assert a["peak_dbfs"] == pytest.approx(-20, abs=0.1)
        assert a["rms_dbfs"] == pytest.approx(-23, abs=0.2)
        assert a["spectrum"]["dominant_hz"] == pytest.approx(5000, abs=30)
        assert a["stereo"] == "identical" and a["clipped_samples"] == 0
        assert a["warnings"] == []
        assert len(a["sha256"]) == 64

    def test_render_data_shapes(self):
        a = sa.analyse(_tone(1000, 48000), 48000)
        env = a["envelope"]
        assert env["bins"] == sa.ENVELOPE_BINS and len(env["channels"]) == 2
        assert len(env["channels"][0]["max"]) == sa.ENVELOPE_BINS
        spec = a["spectrogram"]
        assert len(spec["db"]) == spec["cols"] and len(spec["db"][0]) == spec["rows"]
        assert "envelope" not in sa.analyse(_tone(1000, 48000), 48000, render=False)

    def test_warnings(self):
        x = _tone(1000, 48000, amp=1.2, ramp_s=0)
        x = np.clip(x, -1, 1)
        x[:, 1] *= 0.5
        a = sa.analyse(x, 48000)
        text = " ".join(a["warnings"])
        assert "Clips" in text and "Ends abruptly" in text and "Left and right differ" in text

    def test_ultrasound_flagged_at_48k_output(self):
        a = sa.analyse(_tone(50000, 192000), 192000)
        assert a["spectrum"]["above_usable_frac"] > 0.99
        assert any("won't play at 48 kHz" in w for w in a["warnings"])
        # Played at 192 kHz it would fit.
        assert sa.analyse(_tone(50000, 192000), 192000, output_rate=192000)["warnings"] == []

    def test_leading_silence_and_silent(self):
        x = np.concatenate([np.zeros((4800, 1), np.float32), _tone(1000, 48000, channels=1)])
        assert any("silence before" in w for w in sa.analyse(x, 48000)["warnings"])
        assert sa.analyse(np.zeros((480, 1), np.float32), 48000)["warnings"] == [
            "Silent: every sample is zero"]

    @pytest.mark.parametrize("name,ok", [("tone_5k.wav", True), ("A-1.WAV", True),
                                         (".hidden.wav", False), ("a b.wav", False),
                                         ("x.mp3", False), ("../x.wav", False)])
    def test_names(self, name, ok):
        assert bool(sa.NAME_RE.match(name)) is ok


class TestResample:
    def _level_db(self, y, rate, freq):
        w = np.hanning(len(y))
        spec = np.abs(np.fft.rfft(y * w)) / w.sum() * 2
        f = np.fft.rfftfreq(len(y), 1 / rate)
        return spec[np.argmin(abs(f - freq))], spec, f

    @pytest.mark.parametrize("src", [8000, 44100, 96000, 192000])
    def test_passband_tone_kept(self, src):
        y = resample(_tone(1000, src, 1.0, ramp_s=0, channels=1)[:, 0], src, 48000)
        assert len(y) == 48000
        amp, _, _ = self._level_db(y, 48000, 1000)
        assert amp == pytest.approx(0.5, rel=0.01)

    def test_ultrasound_filtered_not_aliased(self):
        # np.interp folded 50 kHz at 192 kHz -> 48 kHz down to 2 kHz.
        x = _tone(50000, 192000, 1.0, ramp_s=0)
        y = resample(x, 192000, 48000)
        assert y.shape == (48000, 2)
        assert np.max(np.abs(y[2000:-2000])) < 1e-3

    def test_same_rate_is_untouched(self):
        x = _tone(1000, 48000, 0.01)
        assert resample(x, 48000, 48000) is x
