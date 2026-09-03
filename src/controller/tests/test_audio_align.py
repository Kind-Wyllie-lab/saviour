"""
Tests for src/controller/audio_align.py

The least-squares fit, window resolution, discovery and filter-string
construction are covered without ffmpeg by faking the ffprobe call. One
end-to-end test is guarded on a real ffmpeg being present.
"""

import json
import shutil
import subprocess
from unittest.mock import patch

import numpy as np
import pytest

from src.controller.audio_align import (
    AlignOptions,
    _is_float,
    _label_from_filename,
    align_session_audio,
    build_align_filter,
    discover_audio_streams,
    discover_ptp_history,
    map_grid_to_frames,
    parse_mic_sidecar,
    resolve_window,
    summarise_ptp_window,
)

FRAME_NUM = 1024 * 128
NOMINAL_RATE = 192000


def _write_sidecar(path, sample0_wall_s, true_rate_hz, n_blocks,
                   frame_num=FRAME_NUM, jitter_s=0.0, seed=0):
    """Synthesise a microphone timestamp sidecar for a recording that
    started at `sample0_wall_s` and ran at `true_rate_hz`."""
    slope = frame_num / true_rate_hz
    rng = np.random.default_rng(seed)
    with open(path, "w") as f:
        f.write(f"START_AT {sample0_wall_s - 0.01:.6f}\n")
        f.write(f"STARTED {sample0_wall_s:.6f}\n")
        f.write("STARTUP_LATENCY_MS 4.0\n")
        for k in range(n_blocks):
            t = sample0_wall_s + k * slope + rng.normal(0.0, jitter_s)
            f.write(f"{t:.6f}\n")
        f.write("SEGMENT_CLIPPED_SAMPLES 0\n")
        f.write(f"SEGMENT_TOTAL_SAMPLES {n_blocks * frame_num}\n")


# --------------------------------------------------------------------------- #
# parse_mic_sidecar                                                           #
# --------------------------------------------------------------------------- #


def test_fit_recovers_rate_and_sample0(tmp_path):
    sidecar = tmp_path / "mic_timestamps.txt"
    n_blocks = 1500  # ~17 min recording at the default block size
    true_rate = 192_015.0  # ~78 ppm fast, a realistic crystal offset
    sample0 = 1_700_000_000.0
    _write_sidecar(sidecar, sample0, true_rate, n_blocks, jitter_s=0.002)

    with patch(
        "src.controller.audio_align._probe_audio",
        return_value=(n_blocks * FRAME_NUM, NOMINAL_RATE),
    ):
        fit = parse_mic_sidecar(str(sidecar), "unused.flac")

    assert fit.measured_rate_hz == pytest.approx(true_rate, rel=1e-5)
    assert fit.sample0_wall_ns == pytest.approx(int(sample0 * 1e9), abs=2_000_000)
    assert fit.nominal_rate_hz == NOMINAL_RATE
    assert fit.n_blocks == n_blocks
    assert fit.n_samples == n_blocks * FRAME_NUM
    assert 0.0 < fit.residual_p95_ms < 5.0
    assert fit.residual_p50_ms <= fit.residual_p95_ms
    assert fit.duration_s == pytest.approx(n_blocks * FRAME_NUM / true_rate, rel=1e-4)


def test_fit_rejects_scheduler_stalls(tmp_path):
    """A few blocks stalled ~1 s (real behaviour on a loaded Pi) must be
    dropped from the fit, not allowed to drag the sample-0 intercept."""
    sidecar = tmp_path / "mic_timestamps.txt"
    n_blocks = 800
    sample0 = 1_700_000_000.0
    _write_sidecar(sidecar, sample0, NOMINAL_RATE, n_blocks, jitter_s=0.001, seed=1)
    lines = sidecar.read_text().splitlines()
    body = [i for i, ln in enumerate(lines) if _is_float(ln)]
    for stall_block in (3, 50, 400, 700):  # push these ~1 s late
        idx = body[stall_block]
        lines[idx] = f"{float(lines[idx]) + 1.0:.6f}"
    sidecar.write_text("\n".join(lines) + "\n")

    with patch(
        "src.controller.audio_align._probe_audio",
        return_value=(n_blocks * FRAME_NUM, NOMINAL_RATE),
    ):
        fit = parse_mic_sidecar(str(sidecar), "unused.flac")

    assert fit.n_outliers >= 4
    assert fit.measured_rate_hz == pytest.approx(NOMINAL_RATE, rel=1e-5)
    assert fit.sample0_wall_ns == pytest.approx(int(sample0 * 1e9), abs=3_000_000)


def test_fit_degenerate_single_block_falls_back_to_started(tmp_path):
    sidecar = tmp_path / "mic_timestamps.txt"
    sample0 = 1_700_000_123.0
    _write_sidecar(sidecar, sample0, NOMINAL_RATE, n_blocks=1)

    with patch(
        "src.controller.audio_align._probe_audio",
        return_value=(FRAME_NUM, NOMINAL_RATE),
    ):
        fit = parse_mic_sidecar(str(sidecar), "unused.flac")

    assert fit.measured_rate_hz == float(NOMINAL_RATE)
    assert fit.sample0_wall_ns == int(sample0 * 1e9)
    assert np.isnan(fit.residual_p95_ms)
    # NaN must not leak into the JSON report (strict parsers reject it).
    report = fit.as_report()
    assert report["residual_p50_ms"] is None
    assert report["residual_p95_ms"] is None
    json.loads(json.dumps(report, allow_nan=False))


def test_fit_ignores_trailer_and_header_lines(tmp_path):
    sidecar = tmp_path / "mic_timestamps.txt"
    _write_sidecar(sidecar, 1_700_000_000.0, NOMINAL_RATE, n_blocks=50)
    with patch(
        "src.controller.audio_align._probe_audio",
        return_value=(50 * FRAME_NUM, NOMINAL_RATE),
    ):
        fit = parse_mic_sidecar(str(sidecar), "unused.flac")
    assert fit.n_blocks == 50  # SEGMENT_* / START_AT / STARTED / STARTUP_* excluded


def test_fit_warns_on_probe_sample_mismatch(tmp_path, caplog):
    """A decoded sample count far from n_blocks * frame_num means a
    truncated recording or the wrong --frame-num -- warn, don't fail."""
    sidecar = tmp_path / "mic_timestamps.txt"
    _write_sidecar(sidecar, 1_700_000_000.0, NOMINAL_RATE, n_blocks=100)
    with patch(
        "src.controller.audio_align._probe_audio",
        return_value=((100 + 5) * FRAME_NUM, NOMINAL_RATE),  # 5 blocks over
    ), caplog.at_level("WARNING"):
        fit = parse_mic_sidecar(str(sidecar), "unused.flac")
    assert fit.n_blocks == 100
    assert "blocks" in caplog.text.lower()


# --------------------------------------------------------------------------- #
# build_align_filter                                                          #
# --------------------------------------------------------------------------- #


def _fit(sample0_wall_ns, rate=NOMINAL_RATE):
    from src.controller.audio_align import SidecarFit

    return SidecarFit(
        sample0_wall_ns=sample0_wall_ns, measured_rate_hz=float(rate),
        nominal_rate_hz=NOMINAL_RATE, frame_num=FRAME_NUM, n_blocks=10,
        probe_samples=FRAME_NUM * 10, residual_p50_ms=0.5, residual_p95_ms=1.0,
        n_outliers=0, started_wall_ns=sample0_wall_ns,
    )


def test_filter_delays_when_audio_starts_after_window():
    window = 1_700_000_000_000_000_000
    filt = build_align_filter(_fit(window + 300_000_000), window, NOMINAL_RATE)
    assert "adelay=300:all=1" in filt
    assert "atrim" not in filt
    assert f"aresample={NOMINAL_RATE}" in filt


def test_filter_trims_when_audio_starts_before_window():
    window = 1_700_000_000_000_000_000
    filt = build_align_filter(_fit(window - 120_000_000), window, NOMINAL_RATE)
    assert "atrim=start=0.12" in filt
    assert "adelay" not in filt


def test_filter_uses_measured_rate_for_asetrate():
    filt = build_align_filter(_fit(0, rate=192_015.0), 0, 48000)
    assert "asetrate=192015" in filt
    assert "aresample=48000" in filt


# --------------------------------------------------------------------------- #
# resolve_window                                                              #
# --------------------------------------------------------------------------- #


def _write_camera(date_dir, name, first_ns, last_ns):
    cam_dir = date_dir / name
    cam_dir.mkdir()
    (cam_dir / "vid.mp4").write_bytes(b"x")
    with open(cam_dir / "vid_timestamps.csv", "w", newline="") as f:
        f.write("frame_id,timestamp_ns,timestamp_utc\n")
        f.write(f"0,{first_ns},u\n")
        f.write(f"1,{(first_ns + last_ns) // 2},u\n")
        f.write(f"2,{last_ns},u\n")


def test_window_from_overlapping_cameras(tmp_path):
    date_dir = tmp_path / "20260703"
    date_dir.mkdir()
    _write_camera(date_dir, "camera_a", 1_000_000_000_000, 9_000_000_000_000)
    _write_camera(date_dir, "camera_b", 1_500_000_000_000, 8_000_000_000_000)

    start, dur = resolve_window(str(date_dir), [], None, None)
    assert start == 1_500_000_000_000
    assert dur == pytest.approx((8_000_000_000_000 - 1_500_000_000_000) / 1e9)


def test_window_explicit_args_win(tmp_path):
    date_dir = tmp_path / "20260703"
    date_dir.mkdir()
    _write_camera(date_dir, "camera_a", 1_000_000_000_000, 9_000_000_000_000)
    start, dur = resolve_window(str(date_dir), [], 2_000_000_000_000, 42.0)
    assert (start, dur) == (2_000_000_000_000, 42.0)


def test_window_without_cameras_uses_audio_union(tmp_path):
    date_dir = tmp_path / "20260703"
    date_dir.mkdir()
    fits = [_fit(5_000_000_000), _fit(7_000_000_000)]
    start, dur = resolve_window(str(date_dir), fits, None, None)
    assert start == 5_000_000_000
    assert dur > 0


# --------------------------------------------------------------------------- #
# map_grid_to_frames                                                          #
# --------------------------------------------------------------------------- #


def test_grid_maps_to_nearest_real_frame():
    # 5 fps nominal but the real frames drift late (210 ms by frame 20).
    ts = [1_000_000_000 + i * 210_000_000 for i in range(21)]
    step = 200_000_000  # ask for a clean 5 fps output grid
    idx = map_grid_to_frames(ts, ts[0], step, 21)
    assert idx[0] == 0
    assert idx == sorted(idx)  # forward-only
    # by output time j*200ms the closest real frame has slipped behind j
    assert idx[20] < 20 and idx[20] >= 18


def test_grid_applies_index_offset_and_clamps():
    ts = [i * 40_000_000 for i in range(10)]
    idx = map_grid_to_frames(ts, 0, 40_000_000, 10, index_offset=3)
    assert idx[0] == 3
    assert max(idx) <= len(ts) - 1  # clamped, never runs past the last frame


def test_grid_empty_timestamps():
    assert map_grid_to_frames([], 0, 1, 5) == []


# --------------------------------------------------------------------------- #
# PTP window summary                                                          #
# --------------------------------------------------------------------------- #


_PTP_HEADER = (
    "module_id,timestamp_utc,timestamp_epoch,ptp4l_offset_ns,ptp4l_offset_ns_min,"
    "ptp4l_offset_ns_max,phc2sys_offset_ns,phc2sys_offset_ns_min,"
    "phc2sys_offset_ns_max,ptp4l_freq,phc2sys_freq\n"
)


def _write_ptp_csv(path, rows):
    with open(path, "w") as f:
        f.write(_PTP_HEADER)
        for epoch, ptp4l, phc in rows:
            f.write(f"cam_1,x,{epoch},{ptp4l},,,{phc},,,0,0\n")


def test_ptp_summary_windows_and_aggregates(tmp_path):
    csv_path = tmp_path / "ptp_history_24h.csv"
    _write_ptp_csv(csv_path, [
        (100.0, -50_000, 1_000),   # before window -> excluded
        (150.0, 2_000, -3_000),
        (160.0, -8_000, 4_000),
        (170.0, 40_000, -1_000),
        (900.0, 999_999, 1),       # after window -> excluded
    ])
    s = summarise_ptp_window(str(csv_path), 149 * 10**9, 171 * 10**9)
    assert s["samples"] == 3
    assert s["modules"] == ["cam_1"]
    assert s["ptp4l_offset"]["abs_max_ns"] == 40_000.0
    assert s["phc2sys_offset"]["abs_max_ns"] == 4_000.0
    assert s["ptp4l_offset"]["abs_p50_ns"] == 8_000.0


def test_ptp_summary_none_when_no_samples_in_window(tmp_path):
    csv_path = tmp_path / "ptp_history.csv"
    _write_ptp_csv(csv_path, [(10.0, 1, 1), (20.0, 2, 2)])
    assert summarise_ptp_window(str(csv_path), 100 * 10**9, 200 * 10**9) is None


def test_discover_ptp_history_finds_sibling_csv(tmp_path):
    date_dir = tmp_path / "sess" / "20260703"
    date_dir.mkdir(parents=True)
    (date_dir.parent / "ptp_history_24h.csv").write_text(_PTP_HEADER)
    assert discover_ptp_history(str(date_dir)) is not None
    assert discover_ptp_history(str(tmp_path / "empty")) is None


# --------------------------------------------------------------------------- #
# discovery                                                                   #
# --------------------------------------------------------------------------- #


def test_discover_audio_streams_skips_cameras(tmp_path):
    date_dir = tmp_path / "20260703"
    date_dir.mkdir()
    _write_camera(date_dir, "camera_a", 1, 2)

    mic_dir = date_dir / "microphone_1234"
    mic_dir.mkdir()
    (mic_dir / "sess_MicA_(0_120000).flac").write_bytes(b"x")
    (mic_dir / "sess_MicA_(0_120000)_timestamps.txt").write_text("STARTED 1.0\n")
    (mic_dir / "orphan.flac").write_bytes(b"x")  # no sidecar -> ignored

    streams = discover_audio_streams(str(date_dir))
    assert len(streams) == 1
    assert streams[0].label == "MicA"
    assert streams[0].audio_path.endswith(".flac")


def test_label_from_filename():
    assert _label_from_filename("sess_MicA_(3_093012).flac", "fb") == "MicA"
    assert _label_from_filename("weirdname.flac", "mic_1234") == "mic_1234"


# --------------------------------------------------------------------------- #
# end-to-end (needs ffmpeg)                                                   #
# --------------------------------------------------------------------------- #

_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_end_to_end_alignment_pins_window(tmp_path):
    session = tmp_path / "usv-session"
    date_dir = session / "20260703"
    mic_dir = date_dir / "microphone_1234"
    mic_dir.mkdir(parents=True)

    duration_s = 30.0  # long enough for a well-conditioned fit at the real block size
    n_blocks = int(duration_s * NOMINAL_RATE / FRAME_NUM)
    n_samples = n_blocks * FRAME_NUM
    audio_path = mic_dir / "usv-session_MicA_(0_120000).flac"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"sine=frequency=40000:sample_rate={NOMINAL_RATE}:duration={duration_s + 1}",
         "-af", f"atrim=end_sample={n_samples}", str(audio_path)],
        check=True, capture_output=True,
    )

    # Audio sample 0 sits 250 ms after the video window start.
    window_start_ns = 1_700_000_000_000_000_000
    sample0_wall_s = (window_start_ns / 1e9) + 0.25
    _write_sidecar(mic_dir / "usv-session_MicA_(0_120000)_timestamps.txt",
                   sample0_wall_s, NOMINAL_RATE, n_blocks, jitter_s=0.001)

    out_dir = tmp_path / "out"
    results = align_session_audio(
        str(date_dir), AlignOptions(out_dir=str(out_dir), spectrogram=True),
        t_start_ns=window_start_ns, duration_s=5.0,
    )

    assert len(results) == 1
    rep = results[0]
    assert rep["offset_from_window_ms"] == pytest.approx(250.0, abs=5.0)
    assert rep["measured_rate_hz"] == pytest.approx(NOMINAL_RATE, rel=5e-4)

    aligned = out_dir / "usv-session_MicA_aligned.flac"
    assert aligned.is_file()
    assert (out_dir / "usv-session_MicA_spectrogram.png").is_file()
    report = json.loads((out_dir / "usv-session_MicA_align.json").read_text())
    assert report["label"] == "MicA"

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(aligned)],
        check=True, capture_output=True, text=True,
    )
    assert float(probe.stdout.strip()) == pytest.approx(5.0, abs=0.05)


@pytest.mark.skipif(not _HAVE_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_raises_without_audio_streams(tmp_path):
    date_dir = tmp_path / "20260703"
    date_dir.mkdir()
    with pytest.raises(ValueError, match="No audio streams"):
        align_session_audio(str(date_dir), AlignOptions(out_dir=str(tmp_path / "o")))
