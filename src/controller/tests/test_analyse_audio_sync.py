"""
Tests for tools/analyse_audio_sync.py

Synthesises a microphone segment (FLAC + `*_timestamps.txt` sidecar with
the Step 0 probe lines) plus a TTL `*_events.csv`, injects a transient at
a known wall-clock instant a known offset after a TTL edge, and checks the
tool recovers that offset and the probe fields.
"""

import importlib.util
import os
import sys

import numpy as np
import pytest
import soundfile as sf

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_MOD_PATH = os.path.join(_REPO, "tools", "analyse_audio_sync.py")
_spec = importlib.util.spec_from_file_location("analyse_audio_sync", _MOD_PATH)
aas = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = aas  # so @dataclass can resolve the module
_spec.loader.exec_module(aas)

RATE = 192_000
# A small block size keeps the synthetic FLAC short while still giving the
# robust linear fit enough points to reject the two seeded outliers. The
# tool takes frame_num as a parameter, so this is a faithful stand-in for
# the production 131072.
FRAME_NUM = 8_192
SAMPLE0_WALL_S = 1_788_000_000.0
INJECT_OFFSET_MS = 150.0


def _build_session(tmp_path, *, n_blocks=64, onset_offsets_ms=(400, 1400, 2400)):
    date_dir = tmp_path / "20260907"
    mic_dir = date_dir / "audiomoth"
    ttl_dir = date_dir / "ttl"
    mic_dir.mkdir(parents=True)
    ttl_dir.mkdir(parents=True)

    total = n_blocks * FRAME_NUM
    rng = np.random.default_rng(1)
    audio = rng.normal(0.0, 5e-4, total).astype(np.float64)

    # A 30 ms 8 kHz burst at each requested position; remember the true
    # onset sample of each.
    burst_n = int(0.03 * RATE)
    tt = np.arange(burst_n) / RATE
    burst = 0.3 * np.sin(2 * np.pi * 8000 * tt) * np.hanning(burst_n)
    onsets = []
    for off_ms in onset_offsets_ms:
        s0 = int(off_ms / 1000.0 * RATE)
        audio[s0:s0 + burst_n] += burst
        onsets.append(s0)

    audio_path = mic_dir / "SESS_A_(0_20260907-000000).flac"
    sf.write(str(audio_path), audio, RATE, subtype="PCM_16", format="FLAC")

    # Sidecar: STARTED anchor, then block-start times. Sample 0 is captured
    # at STARTED, but the first record() over-primes the ring buffer by ~0.6 s
    # so every k>=1 block stamp is shifted that much later (matches the real
    # bench data: block 0 ~= STARTED, then a ~1.9x first read). Block 4 also
    # takes a scheduler stall.
    first_read_excess_s = 0.60
    sidecar = mic_dir / "SESS_A_(0_20260907-000000)_timestamps.txt"
    lines = [f"STARTED {SAMPLE0_WALL_S:.6f}"]
    lines.append("RECORDER_ENTER_MS 31.4")
    lines.append("FIRST_RECORD_MS 1275.0")
    lines.append(f"FIRST_RECORD_SAMPLES {FRAME_NUM}")
    lines.append("FIRST_RECORD_EXPECTED_MS 682.7")
    for k in range(n_blocks):
        t = SAMPLE0_WALL_S + k * FRAME_NUM / RATE
        if k >= 1:
            t += first_read_excess_s
        if k == 4:
            t += 0.25
        lines.append(f"{t:.6f}")
    lines.append(f"SEGMENT_TOTAL_SAMPLES {total}")
    sidecar.write_text("\n".join(lines) + "\n")

    # TTL edges: one rising edge INJECT_OFFSET_MS *before* each burst's true
    # onset wall time, plus its falling edge 20 ms later.
    ttl_csv = ttl_dir / "SESS_events.csv"
    rows = ["Timestamp_nanoseconds,pin_number,pin_mode,pin_state,pin_description"]
    for s0 in onsets:
        onset_wall_ns = int(SAMPLE0_WALL_S * 1e9) + int(round(s0 / RATE * 1e9))
        edge = onset_wall_ns - int(INJECT_OFFSET_MS * 1e6)
        rows.append(f"{edge},26,interval_pulse,TTLValue.HIGH,buzzer")
        rows.append(f"{edge + 20_000_000},26,interval_pulse,TTLValue.LOW,buzzer")
    ttl_csv.write_text("\n".join(rows) + "\n")

    return str(date_dir), str(audio_path), str(sidecar), str(ttl_csv)


def test_fit_sidecar_recovers_anchor_rate_and_probes(tmp_path):
    date_dir, audio_path, sidecar, _ = _build_session(tmp_path)
    fit = aas.fit_sidecar("A", audio_path, sidecar, FRAME_NUM)

    assert fit.sample0_wall_ns == int(SAMPLE0_WALL_S * 1e9)
    # Rate comes from the steady k>=1 slope, so the first-read excess (a pure
    # intercept shift) and block 0 being excluded both leave it clean.
    assert abs(fit.measured_rate_hz - RATE) < 5.0
    # Block 0 sits ~0.6 s BELOW the extrapolated steady cadence.
    assert fit.first_block_excess_ms < -400
    assert fit.started_minus_intercept_ms < -400
    assert fit.n_outliers >= 1  # the block-4 stall
    assert fit.probes["RECORDER_ENTER_MS"] == pytest.approx(31.4)
    assert fit.probes["FIRST_RECORD_MS"] == pytest.approx(1275.0)
    assert fit.probes["FIRST_RECORD_EXPECTED_MS"] == pytest.approx(682.7)


def test_fit_sidecar_auto_detects_frame_num(tmp_path):
    """With no --frame-num, block size is read from FIRST_RECORD_SAMPLES."""
    _, audio_path, sidecar, _ = _build_session(tmp_path)
    fit = aas.fit_sidecar("A", audio_path, sidecar, None)
    assert fit.frame_num == FRAME_NUM
    assert abs(fit.measured_rate_hz - RATE) < 5.0


def test_read_ttl_rising_edges(tmp_path):
    _, _, _, ttl_csv = _build_session(tmp_path)
    edges = aas.read_ttl_rising_edges(ttl_csv, pin=26)
    assert len(edges) == 3
    assert edges == sorted(edges)
    # Wrong pin -> nothing.
    assert aas.read_ttl_rising_edges(ttl_csv, pin=99) == []


def test_measure_onset_recovers_injected_offset(tmp_path):
    date_dir, audio_path, sidecar, ttl_csv = _build_session(tmp_path)
    fit = aas.fit_sidecar("A", audio_path, sidecar, FRAME_NUM)
    edges = aas.read_ttl_rising_edges(ttl_csv, pin=26)

    offs = []
    for e in edges:
        r = aas.measure_onset(fit, e)
        assert r is not None
        offs.append(r.offset_ms)
        assert r.confident

    mean = float(np.mean(offs))
    # Injected 150 ms; onset detection + fit slop is a couple of ms.
    assert abs(mean - INJECT_OFFSET_MS) < 5.0
    assert np.std(offs) < 3.0


def test_cmd_measure_runs_end_to_end(tmp_path, capsys):
    date_dir, *_ = _build_session(tmp_path)
    rc = aas.cmd_measure(
        [date_dir], pin=26, at_ns=None, frame_num=FRAME_NUM,
        ptp_history=None, search_ms=400.0, hp_hz=1500.0, thresh_ratio=8.0,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "pulses matched   : 3" in out
    assert "mean" in out


def test_cmd_probes_reads_first_record_ratio(tmp_path, capsys):
    date_dir, *_ = _build_session(tmp_path)
    rc = aas.cmd_probes([date_dir], FRAME_NUM)
    assert rc == 0
    out = capsys.readouterr().out
    assert "FIRST_RECORD_MS" in out
    assert "STARTED - steady k=0" in out
    # 1275 / 682.7 ~= 1.87x shows in the per-block-size summary row; one
    # block size only -> the "run the sweep" reading.
    assert "1.87x" in out
    assert "single block size" in out


def test_probe_reading_sweep_detects_super_linear_scaling(capsys):
    """Excess growing super-linearly with block size -> the H1 verdict."""
    rows = [
        (8192, 1.14, -12.0, 50.0), (8192, 1.10, -10.0, 52.0),
        (32768, 1.55, -100.0, 52.0), (32768, 1.57, -102.0, 62.0),
        (131072, 1.87, -597.0, 52.0), (131072, 1.85, -590.0, 43.0),
    ]
    aas._print_probe_reading(rows)
    out = capsys.readouterr().out
    assert "block_size^1." in out          # slope ~1.3-1.4
    assert "block_size IS the" in out
    # per-block-size table rows
    assert "8192" in out and "131072" in out


def test_probe_reading_sweep_flat_excess_is_h2(capsys):
    rows = [
        (8192, 1.0, -40.0, 50.0), (32768, 1.0, -42.0, 52.0),
        (131072, 1.0, -41.0, 51.0),
    ]
    aas._print_probe_reading(rows)
    assert "red herring" in capsys.readouterr().out
