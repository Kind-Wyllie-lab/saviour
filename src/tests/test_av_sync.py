"""
Tests for src/shared/av_sync.py and tools/av_sync_check.py against
synthetic audio / video with known offsets (docs/AV_SYNC_TEST.md).
"""

import csv

import numpy as np
import pytest

from src.shared import av_sync

sf = pytest.importorskip("soundfile")
RATE = 48_000


def _burst(n, at, rate=RATE, tone=3000.0, length_s=0.05, amp=0.5, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 0.002, n)
    k = np.arange(int(length_s * rate))
    end = min(n, at + k.size)
    x[at:end] += amp * np.sin(2 * np.pi * tone * k[: end - at] / rate)
    return x


# --------------------------------------------------------------------------- #
# audio                                                                       #
# --------------------------------------------------------------------------- #


def test_transient_onset_is_sample_accurate():
    x = _burst(RATE, at=20_000)
    idx, snr = av_sync.find_transient_onset(x, RATE, noise_percentile=20.0)
    assert abs(idx - 20_000) <= RATE * 0.0005  # within one 0.5 ms frame
    assert snr > 30


def test_no_transient_returns_none():
    x = np.random.default_rng(1).normal(0, 0.002, RATE)
    assert av_sync.find_transient_onset(x, RATE, noise_percentile=20.0) is None


@pytest.mark.parametrize("true_offset_ms", [+120.0, -80.0])
def test_audio_onsets_report_offset_either_sign(tmp_path, true_offset_ms):
    """The anchor error's sign isn't settled, so a buzz placed before its
    own edge must be found too."""
    sample0 = 1_790_000_000_000_000_000
    edges = [sample0 + int(s * 1e9) for s in (1.0, 2.5, 4.0)]
    n = RATE * 5
    x = np.random.default_rng(2).normal(0, 0.002, n)
    for e in edges:
        at = int((e - sample0 + true_offset_ms * 1e6) / 1e9 * RATE)
        x += _burst(n, at, seed=3) - np.random.default_rng(3).normal(0, 0.002, n)
    path = str(tmp_path / "a.flac")
    sf.write(path, x.astype(np.float32), RATE, subtype="PCM_16")

    onsets = av_sync.audio_onsets(path, sample0, RATE, edges)
    assert all(o is not None for o in onsets)
    for o in onsets:
        assert o.offset_ms == pytest.approx(true_offset_ms, abs=1.0)


def test_flight_time_is_removed(tmp_path):
    sample0 = 1_790_000_000_000_000_000
    edge = sample0 + int(1e9)
    at = int((1.0 + 0.010) * RATE)  # 10 ms late in the file
    path = str(tmp_path / "a.flac")
    sf.write(path, _burst(RATE * 2, at).astype(np.float32), RATE, subtype="PCM_16")
    (o,) = av_sync.audio_onsets(path, sample0, RATE, [edge], mic_distance_m=3.43)
    assert o.offset_ms == pytest.approx(0.0, abs=1.0)  # 3.43 m = 10 ms of air


# --------------------------------------------------------------------------- #
# video                                                                       #
# --------------------------------------------------------------------------- #

FPS = 30.0
PERIOD_NS = int(1e9 / FPS)
EXPOSURE_NS = 20_000_000


def _led_level(frame_start_ns, on_ns, off_ns):
    """Fraction of a frame's exposure [start, start+E] the LED was on."""
    lo, hi = max(frame_start_ns, on_ns), min(frame_start_ns + EXPOSURE_NS, off_ns)
    return max(0, hi - lo) / EXPOSURE_NS


def _write_led_video(path, t0_ns, n_frames, pulses, size=(160, 120)):
    cv2 = pytest.importorskip("cv2")
    w, h = size
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), FPS, (w, h))
    assert vw.isOpened()
    ts = []
    rng = np.random.default_rng(4)
    for i in range(n_frames):
        start = t0_ns + i * PERIOD_NS
        ts.append(start)  # timestamp = start of exposure
        frame = np.full((h, w, 3), 40, np.uint8)
        frame += rng.integers(0, 4, frame.shape, dtype=np.uint8)
        level = max((_led_level(start, on, off) for on, off in pulses), default=0)
        frame[10:22, 120:132] = int(40 + 200 * level)  # the LED
        vw.write(frame)
    vw.release()
    return np.asarray(ts, dtype=np.int64)


def test_led_onsets_and_lit_fraction_fit(tmp_path):
    t0 = 1_790_000_000_000_000_000
    rng = np.random.default_rng(5)
    # Pulse phase within its frame: either >=30% of that frame's exposure
    # still to run, or in the gap after exposure ends (next frame fully lit).
    # A first frame lit only a few % sits at the detection threshold and
    # comes out encoder-dependent (it differed between Windows and Linux
    # OpenCV builds), so the test avoids that sliver.
    edges = []
    for i in range(10):
        frame = int((0.5 + 1.1 * i) * FPS)
        if i % 2:
            phase = rng.uniform(0, 0.7 * EXPOSURE_NS)
        else:
            phase = rng.uniform(EXPOSURE_NS + 0.5e6, PERIOD_NS - 0.5e6)
        edges.append(t0 + frame * PERIOD_NS + int(phase))
    pulses = [(e, e + 50_000_000) for e in edges]
    path = str(tmp_path / "cam.avi")
    ts = _write_led_video(path, t0, int(12.5 * FPS), pulses)

    expected = av_sync.expected_led_state(ts, edges, 50.0)
    trace, mask = av_sync.led_trace(path, expected)
    assert mask.sum() >= 4
    onsets = av_sync.led_onsets(trace, ts, edges)
    assert all(o is not None for o in onsets)
    for o in onsets:
        # timestamps mark exposure start: first lit frame starts within
        # (edge - E, edge - E + one period]
        assert (
            -EXPOSURE_NS / 1e6 - 1 <= o.offset_ms <= (PERIOD_NS - EXPOSURE_NS) / 1e6 + 1
        )
    fit = av_sync.fraction_fit(
        [o.offset_ms for o in onsets], [o.lit_fraction for o in onsets]
    )
    assert fit is not None
    assert fit["intercept_ms"] == pytest.approx(-EXPOSURE_NS / 1e6, abs=4)
    assert fit["slope_ms"] == pytest.approx(EXPOSURE_NS / 1e6, abs=5)


def test_summarise_ignores_missed_pulses():
    s = av_sync.summarise([10.0, None, 12.0, 14.0])
    assert s == {
        "n": 4,
        "detected": 3,
        "mean_ms": 12.0,
        "median_ms": 12.0,
        "std_ms": 2.0,
        "min_ms": 10.0,
        "max_ms": 14.0,
    }


# --------------------------------------------------------------------------- #
# tools/av_sync_check.py on a synthetic session                               #
# --------------------------------------------------------------------------- #


def _sf_probe(path):
    info = sf.info(path)
    return int(info.frames), int(info.samplerate)


def test_av_sync_check_end_to_end(tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    from src.controller import audio_align
    from tools import av_sync_check

    monkeypatch.setattr(audio_align, "_probe_audio", _sf_probe)

    t0 = 1_790_000_000_000_000_000
    rng = np.random.default_rng(6)
    edges = [t0 + int((1.0 + 1.2 * i + rng.uniform(0, 0.033)) * 1e9) for i in range(8)]
    audio_late_ms = 25.0
    date = tmp_path / "sess-1" / "20261002"
    mic, cam = date / "audiomoth", date / "camera"
    mic.mkdir(parents=True)
    cam.mkdir()

    with open(
        mic / "sess-1_audiomoth_4703_sync_pulses_(0_20261002-100000).csv",
        "w",
        newline="",
    ) as f:
        w = csv.writer(f)
        w.writerow(
            [
                "edge_on_ns",
                "edge_spread_ns",
                "edge_off_ns",
                "pulse_ms",
                "buzzer_pin",
                "led_pin",
                "drive",
                "tone_hz",
                "mic_distance_m",
            ]
        )
        for e in edges:
            w.writerow([e, 5000, e + 50_000_000, 50, 17, 27, "dc", 4000, 0.0])

    # audio: sample 0 at t0, 32768-sample blocks, buzz 25 ms after each edge
    n = RATE * 12
    x = np.random.default_rng(7).normal(0, 0.002, n)
    k = np.arange(int(0.05 * RATE))
    for e in edges:
        at = int((e - t0 + audio_late_ms * 1e6) / 1e9 * RATE)
        x[at : at + k.size] += 0.5 * np.sin(2 * np.pi * 3000 * k / RATE)
    stem = mic / "sess-1_audiomoth_4703_24FC_(0_20261002-100000)"
    sf.write(f"{stem}.flac", x.astype(np.float32), RATE, subtype="PCM_16")
    fn = 32768
    with open(f"{stem}_timestamps.txt", "w") as f:
        f.write(f"STARTED {t0 / 1e9:.6f}\n")
        for b in range(n // fn):
            f.write(f"{t0 / 1e9 + b * fn / RATE:.6f}\n")
        f.write(f"FIRST_RECORD_SAMPLES {fn}\n")

    # video: LED on with each edge, timestamps = exposure start
    pulses = [(e, e + 50_000_000) for e in edges]
    vstem = cam / "sess-1_camera_d074_(0_20261002-100000)"
    ts = _write_led_video(f"{vstem}.avi", t0, int(11.5 * FPS), pulses)
    with open(f"{vstem}_timestamps.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame_id", "timestamp_ns", "exposure_time_us"])
        for i, t in enumerate(ts):
            w.writerow([i, int(t), EXPOSURE_NS // 1000])

    report, rows = av_sync_check.build_report(str(tmp_path / "sess-1"))
    assert report["pulses"] == 8
    (a,) = report["audio_minus_edge"].values()
    assert a["detected"] == 8
    assert a["mean_ms"] == pytest.approx(audio_late_ms, abs=1.5)
    (v,) = report["video_minus_edge"].values()
    assert v["detected"] == 8
    assert v["exposure_ms"] == pytest.approx(20.0)
    (av,) = report["audio_minus_video"].values()
    assert av["detected"] == 8
    # audio onset - first-lit frame start: within one frame of audio_late + E
    assert av["mean_ms"] == pytest.approx(
        audio_late_ms + EXPOSURE_NS / 1e6 - PERIOD_NS / 2e6, abs=PERIOD_NS / 1e6
    )
    assert len(rows) == 8
