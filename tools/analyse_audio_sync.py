#!/usr/bin/env python3
"""
analyse_audio_sync.py -- measure the AudioMoth A/V sync residual.

Companion analysis half of
`plans/audio-video-sync-residual-validation.md`. Two jobs:

  probes  Step 0 -- pull the sync-residual probe lines the microphone
          module now writes into every segment sidecar
          (RECORDER_ENTER_MS / FIRST_RECORD_MS / FIRST_RECORD_SAMPLES /
          FIRST_RECORD_EXPECTED_MS) plus the block-fit rate/residual, and
          apply the plan's H1-vs-H2 reading. No reference event, no extra
          hardware -- just a few normal recordings.

  ttl     Phase A -- for every rising edge in a TTL module's *_events.csv
          (a buzzer/piezo pulse near the AudioMoth, `interval_pulse` mode),
          find the matching transient in the FLAC, convert its onset sample
          to a wall-clock instant using the SAME anchor as
          audio_align.parse_mic_sidecar (the `STARTED` line + the fitted
          true sample rate), and difference it against the TTL edge time.
          Positive offset => the audio transient lands LATER than the edge
          => aligned audio lags video (the 2026-09-07 observed sign).

  ref     Same measurement, but against one or more hand-supplied wall-clock
          instants (`--at-ns`), e.g. a clap's hand-contact frame read out
          of a camera `*_timestamps.csv`. One event per take -- strictly
          weaker than `ttl`, provided as a fallback.

Pass several session date directories to any subcommand to get the
between-run spread -- the load-bearing number for whether a single
per-device correction constant is viable (plan decision gate: tight
=> viable, wide => calibration alone is insufficient).

Reads FLAC directly via `soundfile` (no ffmpeg dependency). PTP quality
during each recording window is folded in when a controller
`*ptp*history*.csv` is found next to the session (or `--ptp-history`).

Usage:
    python3 tools/analyse_audio_sync.py probes /path/to/session/DATEDIR [more ...]
    python3 tools/analyse_audio_sync.py ttl    /path/to/session/DATEDIR [more ...] \
        [--pin 26] [--hp-hz 1500] [--search-ms 400]
    python3 tools/analyse_audio_sync.py ref    /path/to/session/DATEDIR \
        --at-ns 1788515800123456789 [--at-ns ...]
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from dataclasses import dataclass

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt

# Reuse the pure helpers from the shipped aligner so the fit and the PTP
# window summary are byte-for-byte the same as the post-hoc tool. Only the
# audio-length probe differs (soundfile here, ffprobe there).
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from src.controller.audio_align import (  # noqa: E402
    DEFAULT_FRAME_NUM,
    _robust_linfit,
    discover_audio_streams,
    discover_ptp_history,
    summarise_ptp_window,
)

PTP_GATE_NS = 50_000  # recording.ptp_start_gate_us default, in ns


# --------------------------------------------------------------------------- #
# Sidecar parsing                                                             #
# --------------------------------------------------------------------------- #


@dataclass
class SidecarFit:
    """Local mirror of audio_align.SidecarFit -- same anchor and rate, but
    the sample count comes from soundfile, and the probe trailer lines are
    carried through."""

    label: str
    audio_path: str
    sidecar_path: str
    sample0_wall_ns: int
    measured_rate_hz: float
    nominal_rate_hz: int
    frame_num: int
    n_blocks: int
    file_samples: int
    residual_p50_ms: float
    residual_p95_ms: float
    n_outliers: int
    first_block_excess_ms: float          # block-0 stamp minus the steady line
    started_minus_intercept_ms: float     # STARTED minus the steady-fit k=0 point
    probes: dict[str, float]              # RECORDER_ENTER_MS etc.

    @property
    def duration_s(self) -> float:
        return self.file_samples / self.measured_rate_hz

    @property
    def end_wall_ns(self) -> int:
        return self.sample0_wall_ns + int(self.duration_s * 1e9)


def _probe_lines(sidecar_path: str) -> dict[str, float]:
    """The `KEY value` trailer lines. audio_align skips any line with a
    space, so these never perturb the block fit -- we want exactly them."""
    wanted = {
        "RECORDER_ENTER_MS", "FIRST_RECORD_MS", "FIRST_RECORD_SAMPLES",
        "FIRST_RECORD_EXPECTED_MS", "STARTUP_LATENCY_MS",
        "SEGMENT_TOTAL_SAMPLES", "SEGMENT_CLIPPED_PCT", "SEGMENT_PEAK_DBFS",
    }
    out: dict[str, float] = {}
    with open(sidecar_path) as f:
        for raw in f:
            parts = raw.split()
            if len(parts) == 2 and parts[0] in wanted:
                try:
                    out[parts[0]] = float(parts[1])
                except ValueError:
                    pass
    return out


def _is_float(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _resolve_frame_num(probes: dict[str, float], n_blocks: int,
                       override: int | None) -> int:
    """Samples per sidecar block. Prefer the explicit override, else the
    `FIRST_RECORD_SAMPLES` probe (== the `numframes` the recorder was told
    to read), else back it out of `SEGMENT_TOTAL_SAMPLES`, else the
    production default. Lets a `block_size` sweep be analysed with no
    per-run bookkeeping."""
    if override:
        return override
    fs = probes.get("FIRST_RECORD_SAMPLES")
    if fs and fs >= 256:
        return int(fs)
    tot = probes.get("SEGMENT_TOTAL_SAMPLES")
    if tot and n_blocks and tot / n_blocks >= 256:
        # snap to the nearest power of two (block sizes are always 2^k here)
        return int(2 ** round(math.log2(tot / n_blocks)))
    return DEFAULT_FRAME_NUM


def fit_sidecar(
    label: str, audio_path: str, sidecar_path: str,
    frame_num: int | None = None,
) -> SidecarFit:
    block_times: list[float] = []
    started_wall_ns = 0
    with open(sidecar_path) as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("STARTED "):
                started_wall_ns = int(float(line.split(maxsplit=1)[1]) * 1e9)
                continue
            if " " in line or not _is_float(line):
                continue
            block_times.append(float(line))

    info = sf.info(audio_path)
    nominal_rate = int(info.samplerate)
    file_samples = int(info.frames)
    n_blocks = len(block_times)

    probes = _probe_lines(sidecar_path)
    frame_num = _resolve_frame_num(probes, n_blocks, frame_num)

    if n_blocks < 3:
        anchor = started_wall_ns or (
            int(block_times[0] * 1e9) if block_times else 0)
        return SidecarFit(
            label, audio_path, sidecar_path, anchor, float(nominal_rate),
            nominal_rate, frame_num, max(n_blocks, 1), file_samples,
            float("nan"), float("nan"), 0, float("nan"), float("nan"), probes,
        )

    # Block 0's stamp is structurally anomalous, not a statistical outlier:
    # the first record() over-primes PipeWire's ring buffer and routinely
    # returns ~2x a block's wall duration for one block's worth of samples
    # (confirmed here: FIRST_RECORD_MS ~1.87x expected, +/-6 ms across runs).
    # On a long production segment it's 1/5000 points and rejection handles
    # it; on a short bench recording it wrecks the slope. Drop it explicitly
    # and fit the steady-state cadence (k >= 1).
    all_t = np.asarray(block_times, dtype=np.float64)
    k = np.arange(1, n_blocks, dtype=np.float64)
    t = all_t[1:]
    slope, intercept, keep = _robust_linfit(k, t)
    resid_ms = np.abs(t - (slope * k + intercept)) * 1e3

    # Anchor sample 0 on the post-open STARTED stamp, exactly like
    # audio_align.parse_mic_sidecar.
    sample0_wall_ns = started_wall_ns or int(round(intercept * 1e9))
    started_s = (started_wall_ns / 1e9) if started_wall_ns else all_t[0]

    return SidecarFit(
        label=label,
        audio_path=audio_path,
        sidecar_path=sidecar_path,
        sample0_wall_ns=sample0_wall_ns,
        measured_rate_hz=frame_num / slope,
        nominal_rate_hz=nominal_rate,
        frame_num=frame_num,
        n_blocks=n_blocks,
        file_samples=file_samples,
        residual_p50_ms=float(np.percentile(resid_ms[keep], 50)),
        residual_p95_ms=float(np.percentile(resid_ms[keep], 95)),
        n_outliers=int(np.count_nonzero(~keep)),
        first_block_excess_ms=float((all_t[0] - intercept) * 1e3),
        started_minus_intercept_ms=float((started_s - intercept) * 1e3),
        probes=probes,
    )


# --------------------------------------------------------------------------- #
# TTL edge parsing                                                            #
# --------------------------------------------------------------------------- #


def find_ttl_events_csv(date_dir: str) -> list[str]:
    hits: list[str] = []
    for root, _dirs, files in os.walk(date_dir):
        for name in files:
            if name.endswith("_events.csv"):
                hits.append(os.path.join(root, name))
    return sorted(hits)


def read_ttl_rising_edges(
    events_csv: str, pin: int | None, rising_is: str = "HIGH",
) -> list[int]:
    """Wall-clock ns of every rising (pulse-onset) edge. The TTL module
    writes `pin_state` as the repr of a TTLValue enum, i.e. literally
    `TTLValue.HIGH` / `TTLValue.LOW`."""
    edges: list[int] = []
    with open(events_csv, newline="") as f:
        for row in csv.DictReader(f):
            state = (row.get("pin_state") or "").strip().rsplit(".", 1)[-1]
            if state != rising_is:
                continue
            if pin is not None and str(row.get("pin_number", "")).strip() != str(pin):
                continue
            try:
                edges.append(int(float(row["Timestamp_nanoseconds"])))
            except (KeyError, ValueError, TypeError):
                continue
    return sorted(edges)


# --------------------------------------------------------------------------- #
# Transient onset within the FLAC                                             #
# --------------------------------------------------------------------------- #


@dataclass
class OnsetResult:
    ref_wall_ns: int
    predicted_sample: int
    onset_sample: int
    onset_wall_ns: int
    offset_ms: float
    snr_db: float
    confident: bool


def _highpass(x: np.ndarray, rate: float, cutoff_hz: float) -> np.ndarray:
    if cutoff_hz <= 0 or cutoff_hz >= rate / 2:
        return x
    sos = butter(4, cutoff_hz / (rate / 2), btype="high", output="sos")
    return sosfiltfilt(sos, x)


def measure_onset(
    fit: SidecarFit, ref_wall_ns: int, *,
    search_ms: float = 400.0, hp_hz: float = 1500.0,
    frame_ms: float = 0.5, thresh_ratio: float = 8.0,
    sustain_frames: int = 3,
) -> OnsetResult | None:
    """Find the transient near where `ref_wall_ns` predicts it, and time
    its onset off the same anchor the aligner uses."""
    rate = fit.measured_rate_hz
    predicted = (ref_wall_ns - fit.sample0_wall_ns) * rate / 1e9
    if predicted < 0 or predicted > fit.file_samples:
        return None
    predicted = int(round(predicted))

    half = int(search_ms / 1000.0 * rate)
    start = max(0, predicted - half)
    stop = min(fit.file_samples, predicted + half)
    if stop - start < int(0.02 * rate):
        return None

    with sf.SoundFile(fit.audio_path) as snd:
        snd.seek(start)
        block = snd.read(stop - start, dtype="float64", always_2d=False)
    if block.ndim > 1:
        block = block[:, 0]
    if block.size == 0:
        return None

    filt = _highpass(block, rate, hp_hz)
    env = np.abs(filt)

    hop = max(1, int(frame_ms / 1000.0 * rate))
    n_frames = env.size // hop
    if n_frames < sustain_frames + 4:
        return None
    fr = env[: n_frames * hop].reshape(n_frames, hop)
    fr_rms = np.sqrt(np.mean(fr * fr, axis=1) + 1e-30)

    # Noise floor from the leading third of the window (before the pulse we
    # deliberately centred later than the edge).
    lead = max(4, n_frames // 3)
    noise = float(np.median(fr_rms[:lead]))
    thr = noise * thresh_ratio

    above = fr_rms > thr
    # Every rising crossing that stays above for `sustain_frames`, with a
    # refractory gap so one burst yields one candidate. Then take the
    # candidate whose position is nearest the predicted one -- so a
    # neighbouring pulse or a stray noise burst elsewhere in the window
    # can't hijack the measurement.
    refractory = max(sustain_frames, int(0.05 / (frame_ms / 1000.0)))
    candidates: list[int] = []
    i = 0
    while i < n_frames - sustain_frames:
        if above[i] and above[i:i + sustain_frames].all() and (
                i == 0 or not above[i - 1]):
            candidates.append(i)
            i += refractory
        else:
            i += 1
    if not candidates:
        return None

    predicted_frame = (predicted - start) / hop
    onset_frame = min(candidates, key=lambda c: abs(c - predicted_frame))

    # Refine to the first sample in that frame past a per-sample threshold.
    seg = np.abs(filt[onset_frame * hop:(onset_frame + 1) * hop])
    local = onset_frame * hop
    past = np.nonzero(seg > thr)[0]
    if past.size:
        local += int(past[0])

    onset_sample = start + local
    onset_wall_ns = fit.sample0_wall_ns + int(round(onset_sample / rate * 1e9))
    peak = float(np.max(fr_rms[onset_frame:onset_frame + 40])) if n_frames else thr
    snr_db = 20.0 * math.log10(max(peak, 1e-12) / max(noise, 1e-12))

    return OnsetResult(
        ref_wall_ns=ref_wall_ns,
        predicted_sample=predicted,
        onset_sample=onset_sample,
        onset_wall_ns=onset_wall_ns,
        offset_ms=(onset_wall_ns - ref_wall_ns) / 1e6,
        snr_db=snr_db,
        confident=snr_db >= 12.0,
    )


# --------------------------------------------------------------------------- #
# Reporting                                                                   #
# --------------------------------------------------------------------------- #


def _fmt_ns(ns: int) -> str:
    return f"{ns / 1e9:.6f}"


def _ptp_for_window(date_dir: str, ptp_history: str | None,
                    t0: int, t1: int) -> dict | None:
    path = ptp_history or discover_ptp_history(date_dir)
    if path and os.path.isfile(path):
        return summarise_ptp_window(path, t0, t1)
    return None


def _print_ptp(summary: dict | None) -> None:
    if not summary:
        print("  PTP window : no ptp_history.csv found -- cannot confirm "
              "<50 us during the recording")
        return
    worst = 0.0
    for key in ("ptp4l_offset", "phc2sys_offset"):
        st = summary.get(key)
        if st:
            worst = max(worst, st["abs_max_ns"])
            print(f"  PTP {key:14s}: p50 {st['abs_p50_ns']:.0f} ns  "
                  f"p95 {st['abs_p95_ns']:.0f} ns  max {st['abs_max_ns']:.0f} ns")
    flag = "  <-- OVER 50 us GATE; measuring PTP error, not audio latency" \
        if worst > PTP_GATE_NS else ""
    print(f"  PTP modules : {', '.join(summary.get('modules') or []) or '?'}"
          f"  ({summary.get('samples', 0)} samples){flag}")


def cmd_probes(date_dirs: list[str], frame_num: int | None) -> int:
    print("== Step 0: sidecar probe lines "
          "(plans/audio-video-sync-residual-validation.md) ==\n")
    # (frame_num, first_read_ratio, first_excess_ms, enter_ms) per stream
    rows: list[tuple[int, float | None, float, float | None]] = []
    for date_dir in date_dirs:
        streams = discover_audio_streams(date_dir)
        if not streams:
            print(f"{date_dir}: no audio streams found\n")
            continue
        print(f"# {os.path.basename(os.path.normpath(date_dir))}")
        for s in streams:
            fit = fit_sidecar(s.label, s.audio_path, s.sidecar_path, frame_num)
            p = fit.probes
            fr_ms = p.get("FIRST_RECORD_MS")
            exp_ms = p.get("FIRST_RECORD_EXPECTED_MS")
            enter_ms = p.get("RECORDER_ENTER_MS")
            ratio = fr_ms / exp_ms if (fr_ms is not None and exp_ms) else None
            ppm = (1e6 * (fit.measured_rate_hz - fit.nominal_rate_hz)
                   / fit.nominal_rate_hz)
            print(f"  {s.label}  ({os.path.basename(s.audio_path)})")
            print(f"    block size              {fit.frame_num:10d} samples "
                  f"(~{fit.frame_num / fit.nominal_rate_hz * 1e3:.1f} ms)")
            print(f"    steady-state rate       {fit.measured_rate_hz:10.1f} Hz "
                  f"({ppm:+.0f} ppm)   fit residual p95 "
                  f"{fit.residual_p95_ms:.2f} ms over {fit.n_blocks - 1} blocks")
            if enter_ms is not None:
                print(f"    RECORDER_ENTER_MS       {enter_ms:10.1f} ms  "
                      f"(soundcard/PipeWire stream open)")
            if ratio is not None:
                fs = p.get("FIRST_RECORD_SAMPLES")
                print(f"    FIRST_RECORD_MS         {fr_ms:10.1f} ms  "
                      f"expected {exp_ms:.1f}  -> {ratio:.2f}x"
                      + (f"   ({int(fs)} samples)" if fs is not None else ""))
            print(f"    block-0 vs steady line  {fit.first_block_excess_ms:10.1f} ms  "
                  f"(negative: first stamp sits below the extrapolated cadence)")
            print(f"    STARTED - steady k=0    "
                  f"{fit.started_minus_intercept_ms:10.1f} ms  "
                  f"(<0: STARTED precedes the steady cadence's sample-0 point)")
            lat = p.get("STARTUP_LATENCY_MS")
            if lat is not None:
                print(f"    STARTUP_LATENCY_MS      {lat:10.1f} ms  "
                      f"(STARTED minus intended start-at)")
            rows.append((fit.frame_num, ratio, fit.first_block_excess_ms, enter_ms))
        print()

    if rows:
        _print_probe_reading(rows)
    return 0


def _print_probe_reading(
    rows: list[tuple[int, float | None, float, float | None]],
) -> None:
    print("-- reading --")
    by_bs: dict[int, list[tuple[float | None, float, float | None]]] = {}
    for bs, ratio, exc, ent in rows:
        by_bs.setdefault(bs, []).append((ratio, exc, ent))

    hdr = (f"  {'block_size':>10}  {'n':>3}  {'first_read':>10}  "
           f"{'excess_ms':>12}  {'excess/blk':>10}  {'enter_ms':>9}")
    print(hdr)
    stats: list[tuple[int, float, float]] = []  # (block_size, |excess|, blk_ms)
    for bs in sorted(by_bs):
        grp = by_bs[bs]
        ratios = [r for r, _e, _n in grp if r is not None]
        excs = np.asarray([abs(e) for _r, e, _n in grp])
        ents = [n for _r, _e, n in grp if n is not None]
        blk_ms = bs / 192000 * 1e3
        mean_exc = float(excs.mean())
        stats.append((bs, mean_exc, blk_ms))
        rr = f"{np.mean(ratios):.2f}x" if ratios else "  -  "
        print(f"  {bs:>10}  {len(grp):>3}  {rr:>10}  "
              f"{mean_exc:>9.1f}+-{excs.std(ddof=1) if excs.size > 1 else 0:<2.0f}  "
              f"{mean_exc / blk_ms:>10.2f}  "
              f"{np.mean(ents) if ents else float('nan'):>9.1f}")
    print()

    if len(stats) >= 2:
        bs_arr = np.array([s[0] for s in stats], float)
        ex_arr = np.maximum(np.array([s[1] for s in stats], float), 1e-6)
        # power-law slope of first-read excess vs block size
        slope = float(np.polyfit(np.log(bs_arr), np.log(ex_arr), 1)[0])
        lo, hi = stats[0], stats[-1]
        print(f"  first-read excess scales as block_size^{slope:.2f}  "
              f"({lo[1]:.0f} ms @ {lo[0]} -> {hi[1]:.0f} ms @ {hi[0]})")
        if slope > 0.5:
            print("  -> super-linear growth with block_size ==> H1: the first "
                  "read over-primes PipeWire's ring buffer. block_size IS the "
                  "knob -- a small block nearly removes the first-read anomaly "
                  "(the block-0 ambiguity term shrinks with it). Still need "
                  "Phase A (TTL buzzer) for the residual's SIGN, and a "
                  "stress-ng run before shipping a small value (xrun headroom).")
        else:
            print("  -> flat across block_size ==> H2: fixed source/USB "
                  "latency; block_size is a red herring.")
    else:
        bs, exc, blk_ms = stats[0]
        print(f"  single block size ({bs}); excess ~{exc:.0f} ms "
              f"({exc / blk_ms:.2f} blocks). Run the sweep "
              f"(8192/32768/131072/262144) to tell H1 from H2.")


def _report_run(date_dir: str, fit: SidecarFit,
                results: list[OnsetResult], ptp: dict | None) -> dict:
    offs = np.asarray([r.offset_ms for r in results], dtype=np.float64)
    ref_s = np.asarray([r.ref_wall_ns / 1e9 for r in results], dtype=np.float64)
    print(f"# {date_dir}  [{fit.label}]")
    print(f"  anchor STARTED   : {_fmt_ns(fit.sample0_wall_ns)}  "
          f"rate {fit.measured_rate_hz:.3f} Hz  "
          f"block-fit residual p95 {fit.residual_p95_ms:.2f} ms")
    _print_ptp(ptp)
    print(f"  pulses matched   : {len(results)}")
    if not results:
        print("  (no transients found near the TTL edges -- buzzer wired / "
              "loud enough? right pin? within the recording window?)\n")
        return {"date_dir": date_dir, "label": fit.label, "n": 0}

    lowconf = sum(not r.confident for r in results)
    drift_ms_per_s = float("nan")
    if len(results) >= 3 and np.ptp(ref_s) > 0:
        m, _b = np.polyfit(ref_s - ref_s[0], offs, 1)
        drift_ms_per_s = m
    print("  offset (audio - reference), positive => audio late / lags video:")
    print(f"    mean   {offs.mean():+8.2f} ms")
    print(f"    median {np.median(offs):+8.2f} ms")
    print(f"    std    {offs.std(ddof=1) if offs.size > 1 else 0.0:8.2f} ms  "
          f"(within-run spread)")
    print(f"    range  {offs.min():+.2f} .. {offs.max():+.2f} ms")
    if not math.isnan(drift_ms_per_s):
        print(f"    drift  {drift_ms_per_s * 1000:+.2f} ms per 1000 s "
              f"(slope over the run -> constant vs drift)")
    if lowconf:
        print(f"    note   {lowconf}/{len(results)} matches low-confidence "
              f"(SNR < 12 dB)")
    print("    per-pulse: " + "  ".join(f"{v:+.1f}" for v in offs) + "\n")
    return {
        "date_dir": date_dir,
        "label": fit.label,
        "n": int(offs.size),
        "mean_ms": float(offs.mean()),
        "std_ms": float(offs.std(ddof=1)) if offs.size > 1 else 0.0,
        "drift_ms_per_1000s": None if math.isnan(drift_ms_per_s)
        else float(drift_ms_per_s * 1000),
    }


def _cross_run_summary(rows: list[dict]) -> None:
    good = [r for r in rows if r.get("n")]
    if len(good) < 2:
        return
    means = np.asarray([r["mean_ms"] for r in good])
    print("== between-run summary (the load-bearing number) ==")
    print(f"  runs           : {len(good)}")
    print("  per-run means  : " + "  ".join(f"{m:+.1f}" for m in means))
    print(f"  grand mean     : {means.mean():+.2f} ms")
    print(f"  between-run std : {means.std(ddof=1):.2f} ms  "
          f"(range {means.min():+.1f} .. {means.max():+.1f})")
    spread = means.max() - means.min()
    if spread <= 40:
        print(f"  spread {spread:.1f} ms  ->  tight: a single per-device "
              f"correction constant looks viable (plan decision gate). Wire it "
              f"into parse_mic_sidecar as an additive sample0_wall_ns term.")
    else:
        print(f"  spread {spread:.1f} ms  ->  wide: calibration alone is "
              f"insufficient; go to Phase C (drain-then-stamp / raw-ALSA "
              f"htstamp anchor).")


def cmd_measure(
    date_dirs: list[str], *, pin: int | None, at_ns: list[int] | None,
    frame_num: int | None, ptp_history: str | None,
    search_ms: float, hp_hz: float, thresh_ratio: float,
) -> int:
    rows: list[dict] = []
    for date_dir in date_dirs:
        streams = discover_audio_streams(date_dir)
        if not streams:
            print(f"{date_dir}: no audio streams found\n")
            continue

        if at_ns:
            edges = sorted(at_ns)
        else:
            ttl_csvs = find_ttl_events_csv(date_dir)
            if not ttl_csvs:
                print(f"{date_dir}: no *_events.csv (TTL) found -- pass "
                      f"--at-ns, or check the TTL module recorded\n")
                continue
            edges = []
            for c in ttl_csvs:
                edges += read_ttl_rising_edges(c, pin)
            edges = sorted(edges)
            if not edges:
                print(f"{date_dir}: TTL CSV has no rising edges"
                      + (f" on pin {pin}" if pin is not None else "") + "\n")
                continue

        for s in streams:
            fit = fit_sidecar(s.label, s.audio_path, s.sidecar_path, frame_num)
            in_window = [e for e in edges
                         if fit.sample0_wall_ns <= e <= fit.end_wall_ns]
            results: list[OnsetResult] = []
            for e in in_window:
                r = measure_onset(fit, e, search_ms=search_ms, hp_hz=hp_hz,
                                  thresh_ratio=thresh_ratio)
                if r is not None:
                    results.append(r)
            ptp = _ptp_for_window(date_dir, ptp_history,
                                  fit.sample0_wall_ns, fit.end_wall_ns)
            rows.append(_report_run(date_dir, fit, results, ptp))

    _cross_run_summary(rows)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("date_dirs", nargs="+",
                        help="Session date dir(s), e.g. .../session/20260907")
    common.add_argument("--frame-num", type=int, default=None,
                        help="Samples per sidecar block; default: auto from "
                        "FIRST_RECORD_SAMPLES in each sidecar (handles a "
                        "block_size sweep with no bookkeeping)")

    sub.add_parser("probes", parents=[common],
                   help="Step 0: sidecar probe lines only")

    p_tt = sub.add_parser("ttl", parents=[common],
                          help="Phase A: offset vs TTL buzzer edges")
    p_tt.add_argument("--pin", type=int, default=None,
                      help="Only use edges on this GPIO pin")

    p_rf = sub.add_parser("ref", parents=[common],
                          help="offset vs hand-supplied wall-clock instants")
    p_rf.add_argument("--at-ns", type=int, action="append", required=True,
                      dest="at_ns", help="Reference event wall time in ns "
                      "(repeatable); e.g. a camera CSV timestamp_ns")

    for p in (p_tt, p_rf):
        p.add_argument("--ptp-history", default=None,
                       help="Controller ptp_history.csv (else auto-discovered)")
        p.add_argument("--search-ms", type=float, default=400.0,
                       help="Half-width of the search window around the "
                       "predicted transient position")
        p.add_argument("--hp-hz", type=float, default=1500.0,
                       help="High-pass before onset detection (Hz)")
        p.add_argument("--thresh-ratio", type=float, default=8.0,
                       help="Onset threshold as a multiple of the noise floor")

    args = ap.parse_args(argv)

    if args.cmd == "probes":
        return cmd_probes(args.date_dirs, args.frame_num)
    return cmd_measure(
        args.date_dirs,
        pin=getattr(args, "pin", None),
        at_ns=getattr(args, "at_ns", None),
        frame_num=args.frame_num,
        ptp_history=args.ptp_history,
        search_ms=args.search_ms,
        hp_hz=args.hp_hz,
        thresh_ratio=args.thresh_ratio,
    )


if __name__ == "__main__":
    raise SystemExit(main())
