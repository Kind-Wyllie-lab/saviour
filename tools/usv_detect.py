"""Find rat ultrasonic vocalisations (USVs) in a session's AudioMoth recordings.

A first look, not a trained detector: a spectrogram from 18 to 95 kHz,
whitened by each frequency's background level, keeps the frames that are
both loud (peak SNR over a threshold) and tonal (low spectral flatness,
the measure USVSEG uses). Broadband clicks from movement are loud but flat,
so they drop out. Nearby frames merge into calls; each call is labelled
22 kHz (peak below 32 kHz: long, flat aversive calls) or 50 kHz (short,
FM/trill appetitive and social calls).

Needs a 192 kHz recording: at 48 kHz neither band is recorded. Main env
(numpy, soundfile, matplotlib); no ffmpeg.

    python tools/usv_detect.py /path/to/session/<DATE> [--out DIR]
        [--snr-db 6] [--flatness 0.5] [--contrast-db 8.5]
        [--window START END]

Writes, per AudioMoth, into --out (default <date_dir>/usv):
    <label>_calls.csv     one row per call: times from file start and PTP
                          wall clock, duration, peak/min/max frequency, SNR
    <label>_summary.json  counts per band, parameters used
    <label>_timeline.png  every call (time x peak frequency) + counts/30 s
    <label>_gallery.png   spectrogram snippets of the strongest calls
    <label>_busiest.png   the 10 s with the most calls, boxes on each call
    <label>_window.png    with --window, that span (seconds from file start)
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

F_LO_HZ = 18_000           # lowest call peak accepted
F_ANALYSIS_LO_HZ = 14_000  # spectrogram starts lower so an 18 kHz peak has
                           # neighbours below it to compare against
F_HI_HZ = 95_000
SPLIT_22_50_HZ = 32_000
NFFT = 512          # 375 Hz bins at 192 kHz
HOP = 256           # 1.33 ms frames at 192 kHz
CHUNK_S = 10.0
SMOOTH_BINS = 3      # 1.1 kHz
SMOOTH_FRAMES = 5    # 6.7 ms
CONTRAST_INNER_HZ = 1_500
CONTRAST_OUTER_HZ = 8_000


@dataclass
class Call:
    start_s: float
    end_s: float
    duration_ms: float
    peak_hz: float
    min_hz: float
    max_hz: float
    snr_db: float
    flatness: float
    contrast_db: float
    band: str
    start_wall_ns: int | None = None


def _frames_db(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Power spectrogram in dB, band-limited. Returns (db[bins, frames], freqs)."""
    if len(x) < NFFT:
        return np.empty((0, 0)), np.empty(0)
    win = np.hanning(NFFT).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(x, NFFT)[::HOP] * win
    spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    freqs = np.fft.rfftfreq(NFFT, 1 / sr)
    band = (freqs >= F_ANALYSIS_LO_HZ) & (freqs <= F_HI_HZ)
    db = 10 * np.log10(spec[:, band].T + 1e-12)
    return db.astype(np.float32), freqs[band]


def _noise_floor(path: str, sr: int) -> np.ndarray:
    """Per-frequency background level: the median of per-chunk medians, so a
    chunk full of calls can't lift it."""
    meds = []
    for block in sf.blocks(path, blocksize=int(CHUNK_S * sr), dtype="float32",
                           always_2d=True):
        db, _ = _frames_db(block[:, 0], sr)
        if db.size:
            meds.append(np.median(db, axis=1))
    return np.median(np.stack(meds), axis=0)


def _smooth(white_db: np.ndarray) -> np.ndarray:
    """Box-average SMOOTH_BINS x SMOOTH_FRAMES (dB). Single-pixel speckle
    averages away; a call, a continuous line, survives."""
    out = white_db
    for axis, n in ((0, SMOOTH_BINS), (1, SMOOTH_FRAMES)):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (n // 2, n - 1 - n // 2)
        c = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis, dtype=np.float64)
        c = np.concatenate([np.zeros_like(c.take([0], axis=axis)), c], axis=axis)
        out = ((c.take(range(n, c.shape[axis]), axis=axis)
                - c.take(range(0, c.shape[axis] - n), axis=axis)) / n
               ).astype(np.float32)
    return out


def _frame_features(white_db: np.ndarray, smooth_db: np.ndarray,
                    freqs: np.ndarray, lo_hz: float, hi_hz: float
                    ) -> tuple[np.ndarray, ...]:
    """Per frame of a whitened spectrogram: peak SNR (searched in
    [lo_hz, hi_hz]), its bin, spectral flatness, and contrast -- how far the peak stands
    above the louder of its two sides (median of the bins CONTRAST_INNER_HZ
    to CONTRAST_OUTER_HZ below it, and above it). A USV is a line a few kHz
    wide (high contrast both sides); rustling, chewing and footsteps are
    broadband blobs rising towards low frequencies, so the side below them
    is as loud as the peak."""
    n_bins, _ = white_db.shape
    in_band = (freqs >= lo_hz) & (freqs <= hi_hz)
    search = np.where(in_band[:, None], smooth_db, -np.inf)
    peak = search.max(axis=0)
    peak_bin = search.argmax(axis=0)
    # A peak on the band's edge is the shoulder of something outside it
    # (movement noise rising below 18 kHz), not a call inside it.
    band_idx = np.flatnonzero(in_band)
    on_edge = (peak_bin <= band_idx[0] + 1) | (peak_bin >= band_idx[-1] - 1)
    peak = np.where(on_edge, -np.inf, peak)
    p = 10 ** (white_db / 10)
    flat = np.exp(np.mean(np.log(p), axis=0)) / np.mean(p, axis=0)
    bin_hz = freqs[1] - freqs[0]
    inner = int(round(CONTRAST_INNER_HZ / bin_hz))
    outer = int(round(CONTRAST_OUTER_HZ / bin_hz))

    def side(offs: np.ndarray) -> np.ndarray:
        idx = peak_bin[None, :] + offs[:, None]
        valid = (idx >= 0) & (idx < n_bins)
        vals = np.take_along_axis(smooth_db, np.clip(idx, 0, n_bins - 1), axis=0)
        # A side running off the top of the band counts as background (0 dB).
        return np.median(np.where(valid, vals, 0.0), axis=0)

    below = side(np.arange(-outer, -inner))
    above = side(np.arange(inner + 1, outer + 1))
    contrast = peak - np.maximum(below, above)
    return peak, peak_bin, flat, contrast


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1), strict=True))


@dataclass(frozen=True)
class Band:
    """One call class, detected on its own: peak searched in [lo_hz, hi_hz],
    frames kept when loud, tonal and narrow, runs kept between min_ms and
    max_ms whose peak frequency wanders by at most max_sd_hz."""
    name: str
    lo_hz: float
    hi_hz: float
    min_ms: float
    max_sd_hz: float


# 50 kHz calls: short FM/flat/trill, above the movement noise. 22 kHz calls:
# long and flat; they share their band with rustling, chewing and footsteps
# (dense below ~30 kHz in an open arena), so only long steady tones count.
BANDS = (
    Band("50kHz", 35_000, F_HI_HZ, min_ms=5, max_sd_hz=np.inf),
    Band("22kHz", F_LO_HZ, SPLIT_22_50_HZ, min_ms=200, max_sd_hz=1_500),
)


def detect(path: str, snr_db: float, flatness: float, contrast_db: float,
           gap_ms: float, bands=BANDS) -> tuple[list[Call], dict]:
    info = sf.info(path)
    sr = info.samplerate
    if sr < 2 * F_HI_HZ * 0.9:
        raise SystemExit(f"{path}: {sr} Hz is too low for USVs (need 192 kHz)")
    floor = _noise_floor(path, sr)
    frame_s = HOP / sr
    feats: dict[str, list[list[np.ndarray]]] = {b.name: [[], [], [], []]
                                                for b in bands}
    freqs = None
    # Overlap chunks by NFFT - HOP samples so the frame grid is continuous.
    overlap = NFFT - HOP
    for block in sf.blocks(path, blocksize=int(CHUNK_S * sr), overlap=overlap,
                           dtype="float32", always_2d=True):
        db, freqs = _frames_db(block[:, 0], sr)
        if not db.size:
            continue
        white = db - floor[:, None]
        smooth = _smooth(white)
        for b in bands:
            for acc, arr in zip(feats[b.name],
                                _frame_features(white, smooth, freqs, b.lo_hz,
                                                b.hi_hz),
                                strict=True):
                acc.append(arr)

    gap = int(round(gap_ms / 1000 / frame_s))
    calls = []
    for b in bands:
        peak, pbin, flat, contrast = (np.concatenate(x) for x in feats[b.name])
        active = (peak >= snr_db) & (flat <= flatness) & (contrast >= contrast_db)
        merged: list[list[int]] = []
        for a, e in _runs(active):
            if merged and a - merged[-1][1] <= gap:
                merged[-1][1] = e
            else:
                merged.append([a, e])
        for a, e in merged:
            dur_ms = (e - a) * frame_s * 1000
            if dur_ms < b.min_ms:
                continue
            sel = slice(a, e)
            on = active[sel]
            f = freqs[pbin[sel][on]]
            if np.std(f) > b.max_sd_hz:
                continue
            calls.append(Call(
                start_s=round(a * frame_s, 4), end_s=round(e * frame_s, 4),
                duration_ms=round(dur_ms, 1), peak_hz=round(float(np.median(f))),
                min_hz=round(float(f.min())), max_hz=round(float(f.max())),
                snr_db=round(float(peak[sel].max()), 1),
                flatness=round(float(np.median(flat[sel][on])), 3),
                contrast_db=round(float(np.median(contrast[sel][on])), 1),
                band=b.name,
            ))
    calls.sort(key=lambda c: c.start_s)
    meta = {"sample_rate": sr, "duration_s": info.frames / sr,
            "frame_ms": frame_s * 1000, "floor_db": floor, "freqs": freqs}
    return calls, meta


def _wall_anchor(audio_path: str) -> int | None:
    """PTP wall time of sample 0 from the sidecar, via audio_align (which
    uses ffprobe only for a cross-check; swapped for soundfile here)."""
    sidecar = audio_path.rsplit(".", 1)[0] + "_timestamps.txt"
    if not os.path.exists(sidecar):
        return None
    try:
        from controller import audio_align
        if shutil.which("ffprobe") is None:
            def _probe(p):
                i = sf.info(p)
                return i.frames, i.samplerate
            audio_align._probe_audio = _probe
        return audio_align.parse_mic_sidecar(sidecar, audio_path).sample0_wall_ns
    except Exception as exc:  # noqa: BLE001 -- wall time is optional output
        print(f"  (no wall-clock anchor: {exc})")
        return None


def _read_span(path: str, sr: int, t0: float, t1: float) -> np.ndarray:
    a = max(0, int(t0 * sr))
    return sf.read(path, start=a, stop=int(t1 * sr), dtype="float32",
                   always_2d=True)[0][:, 0]


def _plot_span(ax, path, meta, t0, t1, calls=(), vmax=30):
    sr = meta["sample_rate"]
    x = _read_span(path, sr, t0, t1)
    db, freqs = _frames_db(x, sr)
    if not db.size:
        return
    white = db - meta["floor_db"][:, None]
    ax.imshow(white, origin="lower", aspect="auto", cmap="magma",
              vmin=0, vmax=vmax,
              extent=[t0, t0 + db.shape[1] * HOP / sr, freqs[0] / 1e3,
                      freqs[-1] / 1e3])
    for c in calls:
        if c.end_s < t0 or c.start_s > t1:
            continue
        colour = "cyan" if c.band == "50kHz" else "lime"
        ax.add_patch(_rect(c, colour))
    ax.set_ylabel("kHz")


def _rect(c: Call, colour: str):
    from matplotlib.patches import Rectangle
    return Rectangle((c.start_s, c.min_hz / 1e3 - 2), c.end_s - c.start_s,
                     (c.max_hz - c.min_hz) / 1e3 + 4, fill=False,
                     edgecolor=colour, lw=0.8)


def write_outputs(path, label, calls, meta, out_dir, window):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dur = meta["duration_s"]
    stem = os.path.join(out_dir, label)

    with open(f"{stem}_calls.csv", "w", newline="") as f:
        fields = list(asdict(Call(0, 0, 0, 0, 0, 0, 0, 0, 0, "")).keys())
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in calls:
            w.writerow(asdict(c))

    # Timeline: every call, and counts per 30 s.
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(14, 6), sharex=True,
                                 gridspec_kw={"height_ratios": [2, 1]})
    for band, colour in (("22kHz", "tab:green"), ("50kHz", "tab:blue")):
        cs = [c for c in calls if c.band == band]
        a1.scatter([c.start_s / 60 for c in cs], [c.peak_hz / 1e3 for c in cs],
                   s=[max(4, min(c.duration_ms / 5, 60)) for c in cs],
                   c=colour, alpha=0.6, label=f"{band} ({len(cs)})", lw=0)
        bins = np.arange(0, dur + 30, 30)
        h, _ = np.histogram([c.start_s for c in cs], bins=bins)
        a2.step(bins[:-1] / 60, h, where="post", color=colour, label=band)
    a1.set_ylabel("peak kHz")
    a1.set_ylim(F_LO_HZ / 1e3, F_HI_HZ / 1e3)
    a1.legend(loc="upper right")
    a1.set_title(f"{label}: {len(calls)} candidate calls")
    a2.set_ylabel("calls / 30 s")
    a2.set_xlabel("minutes from start of recording")
    fig.tight_layout()
    fig.savefig(f"{stem}_timeline.png", dpi=110)
    plt.close(fig)

    # Gallery: strongest calls, 400 ms around each.
    top = sorted(calls, key=lambda c: -c.snr_db)[:40]
    top.sort(key=lambda c: c.start_s)
    if top:
        n_col = min(6, len(top))
        n_row = int(np.ceil(len(top) / n_col))
        fig, axes = plt.subplots(n_row, n_col, figsize=(3 * n_col, 2.8 * n_row + 0.6),
                                 squeeze=False)
        for ax, c in zip(axes.flat, top, strict=False):
            mid = (c.start_s + c.end_s) / 2
            half = max(0.08, (c.end_s - c.start_s) / 2 + 0.04)
            _plot_span(ax, path, meta, max(0, mid - half), mid + half, [c])
            ax.set_ylim(F_LO_HZ / 1e3, F_HI_HZ / 1e3)
            ax.set_title(f"{c.start_s:.2f}s {c.peak_hz / 1e3:.0f}k "
                         f"{c.duration_ms:.0f}ms", fontsize=7)
            ax.tick_params(labelsize=6)
            ax.set_ylabel("")
        for ax in list(axes.flat)[len(top):]:
            ax.axis("off")
        fig.suptitle(f"{label}: {len(top)} strongest calls "
                     "(whitened dB, box = detection)")
        fig.tight_layout()
        fig.savefig(f"{stem}_gallery.png", dpi=110)
        plt.close(fig)

    spans = []
    if calls:
        starts = np.array([c.start_s for c in calls])
        best = max(range(int(dur // 10)),
                   key=lambda i: np.sum((starts >= i * 10) & (starts < i * 10 + 10)))
        spans.append(("busiest", best * 10.0, best * 10.0 + 10))
    if window:
        spans.append(("window", *window))
    for name, t0, t1 in spans:
        rows = int(np.ceil((t1 - t0) / 2.5))
        fig, axes = plt.subplots(rows, 1, figsize=(14, 2.2 * rows), squeeze=False)
        for i, ax in enumerate(axes[:, 0]):
            a = t0 + i * 2.5
            _plot_span(ax, path, meta, a, min(a + 2.5, t1), calls)
        axes[-1, 0].set_xlabel("seconds from start of recording")
        axes[0, 0].set_title(f"{label}: {t0:.1f}-{t1:.1f} s "
                             "(cyan = 50 kHz, green = 22 kHz)")
        fig.tight_layout()
        fig.savefig(f"{stem}_{name}.png", dpi=110)
        plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("date_dir")
    ap.add_argument("--out")
    ap.add_argument("--snr-db", type=float, default=6.0,
                    help="peak whitened level a frame needs (dB over background)")
    ap.add_argument("--flatness", type=float, default=0.5,
                    help="max spectral flatness of a frame (noise ~0.56, tone ~0)")
    ap.add_argument("--contrast-db", type=float, default=8.5,
                    help="how far a frame's peak must stand above its +/-8 kHz "
                         "neighbours (line vs broadband blob)")
    ap.add_argument("--gap-ms", type=float, default=20.0,
                    help="merge detections closer than this")
    ap.add_argument("--window", type=float, nargs=2, metavar=("START", "END"),
                    help="also plot this span (seconds from file start)")
    args = ap.parse_args()

    flacs = sorted(glob.glob(os.path.join(args.date_dir, "microphone_*", "*.flac"))
                   + glob.glob(os.path.join(args.date_dir, "microphone_*", "*.wav")))
    if not flacs:
        raise SystemExit(f"no microphone_*/*.flac under {args.date_dir}")
    out_dir = args.out or os.path.join(args.date_dir, "usv")
    os.makedirs(out_dir, exist_ok=True)

    for path in flacs:
        base = os.path.basename(path).rsplit(".", 1)[0]
        label = base.split("_", 1)[1] if "_" in base else base
        print(f"{os.path.basename(path)}")
        calls, meta = detect(path, args.snr_db, args.flatness,
                             args.contrast_db, args.gap_ms)
        sample0 = _wall_anchor(path)
        if sample0 is not None:
            for c in calls:
                c.start_wall_ns = sample0 + int(c.start_s * 1e9)
        counts = {b: sum(c.band == b for c in calls) for b in ("22kHz", "50kHz")}
        print(f"  {meta['duration_s']:.0f} s at {meta['sample_rate']} Hz: "
              f"{len(calls)} calls {counts}")
        summary = {
            "file": os.path.basename(path), "duration_s": meta["duration_s"],
            "sample_rate": meta["sample_rate"], "sample0_wall_ns": sample0,
            "n_calls": len(calls), "by_band": counts,
            "calls_per_min": round(len(calls) / (meta["duration_s"] / 60), 2),
            "params": {k: v for k, v in vars(args).items()
                       if k not in ("date_dir", "out")},
        }
        with open(os.path.join(out_dir, f"{label}_summary.json"), "w") as f:
            json.dump(summary, f, indent=2)
        write_outputs(path, label, calls, meta, out_dir, args.window)
    print(f"-> {out_dir}")


if __name__ == "__main__":
    main()
