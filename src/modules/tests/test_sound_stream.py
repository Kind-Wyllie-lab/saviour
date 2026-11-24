"""Tests for src/modules/variants/sound/sound_stream.py with a fake sink
that consumes audio in real time (no sound card)."""

import time

import numpy as np
import pytest
import soundfile

from src.modules.variants.sound.sound_stream import SoundStream, load_sound

RATE = 48_000


class FakeSink:
    """Takes one block per block-duration, like a full queue to a card."""

    instances = []

    def __init__(self, device, channels, rate, fail_after=None):
        self.channels, self.rate = channels, rate
        self.pipe_bytes, self.buffer_s = 0, 0.0
        self.blocks = []          # (return time, int16 frames)
        self.closed = False
        self.fail_after = fail_after
        FakeSink.instances.append(self)

    def write(self, data):
        frames = np.frombuffer(data, dtype="<i2").reshape(-1, self.channels)
        if self.fail_after is not None and len(self.blocks) >= self.fail_after:
            raise RuntimeError("aplay exited (1): device gone")
        time.sleep(len(frames) / self.rate)
        self.blocks.append((time.time(), frames.copy()))

    def close(self):
        self.closed = True


def _stream(channels=2, **kw):
    fired = []
    s = SoundStream("fake", channels, rate=RATE, sink_factory=FakeSink,
                    on_fire=lambda meta, at, late: fired.append((meta, at, late)), **kw)
    return s, fired


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_sound_starts_at_its_deadline_on_its_output():
    s, fired = _stream(channels=8, extra_latency_s=0.02)
    s.start()
    try:
        assert s.outputs == 4 and s.latency_s == pytest.approx(0.02)
        assert s.ready.wait(2)
        target = time.time() + 0.3
        tone = np.full((960, 2), 0.5, dtype=np.float32)  # 20 ms
        s.schedule(target, tone, 3, {"file": "t.wav"})
        assert _wait(lambda: fired and s.pending() == 0)
        meta, at, late = fired[0]
        assert meta == {"file": "t.wav"} and late == 0.0
        assert abs(at - target) <= 1 / RATE + 1e-9
        assert _wait(lambda: len(s._voices) == 0)
    finally:
        s.stop()
    sink = FakeSink.instances[-1]
    audio = np.concatenate([b for _, b in sink.blocks])
    loud = np.nonzero(audio.any(axis=1))[0]
    assert len(loud) == 960                     # the whole sound, once
    assert set(np.nonzero(audio.any(axis=0))[0]) == {4, 5}   # output 3
    assert sink.closed


def test_play_now_is_not_late_and_late_sounds_are_counted():
    s, fired = _stream()
    s.start()
    try:
        assert s.ready.wait(2)
        snd = np.full((48, 2), 0.1, dtype=np.float32)
        s.schedule(None, snd, 1, {"n": "now"})
        s.schedule(time.time() - 0.05, snd, 1, {"n": "past"})
        assert _wait(lambda: len(fired) == 2)
        by = {m["n"]: late for m, _, late in fired}
        assert by["now"] == 0.0 and by["past"] >= 0.04
    finally:
        s.stop()


def test_bad_output_refused():
    s, _ = _stream(channels=2)
    with pytest.raises(ValueError):
        s.schedule(None, np.zeros((1, 2), np.float32), 2)


def test_sink_failure_is_reported():
    errors = []
    s = SoundStream("fake", 2, rate=RATE, on_error=errors.append,
                    sink_factory=lambda d, c, r: FakeSink(d, c, r, fail_after=3))
    s.start()
    assert _wait(lambda: errors)
    assert "device gone" in errors[0] and "device gone" in s.error
    assert _wait(lambda: not s.running)
    s.stop()


def test_load_sound_resamples_cuts_and_scales(tmp_path):
    path = tmp_path / "a.wav"
    soundfile.write(path, np.full(24_000, 0.5, dtype=np.float32), 24_000)  # 1 s mono
    full = load_sound(str(path), RATE)
    assert full.shape == (48_000, 2) and np.allclose(full[100], [0.5, 0.5], atol=1e-3)
    cut = load_sound(str(path), RATE, volume=0.5, duration_s=0.25)
    assert cut.shape == (12_000, 2)
    assert cut[100, 0] == pytest.approx(0.25, abs=1e-3)
    assert abs(cut[-1, 0]) < 1e-3                # faded out, no click


def test_stop_without_start_is_safe():
    s, _ = _stream()
    s.stop()
    assert not s.running
