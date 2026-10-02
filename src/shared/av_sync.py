"""
Buzz/LED sync measurement helpers -- shared by the microphone module's
on-device self-test (`sync_selftest`) and `tools/av_sync_check.py`.

The ground truth is a GPIO edge the microphone module drives itself
(a piezo buzzer and/or an LED, `src/modules/sync_pulser.py`), stamped with
`time.time_ns()` on the PTP-disciplined clock. Against that edge:

  audio  the buzz's onset in the FLAC, converted to wall time with exactly
         the anchor `audio_align.parse_mic_sidecar` uses (STARTED + fitted
         true rate), so the measured offset is what the aligner/compose
         would show and can be calibrated out there.
  video  the first frame in which the LED is lit, placed at that frame's
         `timestamp_ns` (what compose / video_compose use), plus how lit it
         was (a partially lit first frame means the LED came on part-way
         through that frame's exposure).

numpy only (no scipy): this runs on the microphone Pi. soundfile / cv2 are
imported lazily by the functions that read files.

See docs/AV_SYNC_TEST.md for wiring and interpretation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Speed of sound at ~20 C; used to remove the buzzer->mic flight time.
SPEED_OF_SOUND_M_S = 343.0


# --------------------------------------------------------------------------- #
# Audio                                                                       #
# --------------------------------------------------------------------------- #


def highpass(x: np.ndarray, rate: float, cutoff_hz: float) -> np.ndarray:
    """Brick-wall FFT high-pass (zero-phase). Windows here are < 1 s, so a
    single FFT is cheap and needs no filter design dependency."""
    if cutoff_hz <= 0 or cutoff_hz >= rate / 2 or x.size < 8:
        return x
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, d=1.0 / rate)
    spec[freqs < cutoff_hz] = 0
    return np.fft.irfft(spec, n=x.size)


def find_transient_onset(
    x: np.ndarray,
    rate: float,
    *,
    expected_index: int | None = None,
    hp_hz: float = 1000.0,
    frame_ms: float = 0.5,
    thresh_ratio: float = 8.0,
    sustain_frames: int = 3,
    noise_lead_frac: float = 0.33,
    noise_percentile: float | None = None,
) -> tuple[int, float] | None:
    """Index of the first sample of a sharp onset in `x`, and its SNR (dB).

    Same detector as tools/analyse_audio_sync.measure_onset: high-pass,
    0.5 ms RMS frames, noise floor from the leading part of the window,
    first crossing of `thresh_ratio` x floor that stays up for
    `sustain_frames`, nearest to `expected_index` if several.

    The noise floor is the median of the leading `noise_lead_frac` of the
    window, or -- with `noise_percentile` -- that percentile of the whole
    window, which works wherever the burst sits as long as it is short
    relative to the window.
    """
    if x.ndim > 1:
        x = x[:, 0]
    filt = highpass(np.asarray(x, dtype=np.float64), rate, hp_hz)
    env = np.abs(filt)
    hop = max(1, int(frame_ms / 1000.0 * rate))
    n_frames = env.size // hop
    if n_frames < sustain_frames + 4:
        return None
    fr = env[: n_frames * hop].reshape(n_frames, hop)
    fr_rms = np.sqrt(np.mean(fr * fr, axis=1) + 1e-30)
    if noise_percentile is not None:
        noise = float(np.percentile(fr_rms, noise_percentile))
    else:
        lead = max(4, int(n_frames * noise_lead_frac))
        noise = float(np.median(fr_rms[:lead]))
    thr = noise * thresh_ratio
    above = fr_rms > thr

    refractory = max(sustain_frames, int(0.05 / (frame_ms / 1000.0)))
    candidates: list[int] = []
    i = 0
    while i < n_frames - sustain_frames:
        if (
            above[i]
            and above[i : i + sustain_frames].all()
            and (i == 0 or not above[i - 1])
        ):
            candidates.append(i)
            i += refractory
        else:
            i += 1
    if not candidates:
        return None
    if expected_index is not None:
        want = expected_index / hop
        onset_frame = min(candidates, key=lambda c: abs(c - want))
    else:
        onset_frame = candidates[0]

    seg = env[onset_frame * hop : (onset_frame + 1) * hop]
    local = onset_frame * hop
    past = np.nonzero(seg > thr)[0]
    if past.size:
        local += int(past[0])
    peak = float(np.max(fr_rms[onset_frame : onset_frame + 40]))
    snr_db = 20.0 * math.log10(max(peak, 1e-12) / max(noise, 1e-12))
    return local, snr_db


@dataclass
class AudioOnset:
    edge_ns: int
    onset_wall_ns: int
    offset_ms: float  # onset - edge, flight time removed
    snr_db: float


def audio_onsets(
    audio_path: str,
    sample0_wall_ns: int,
    rate_hz: float,
    edges_ns: list[int],
    *,
    search_before_ms: float = 300.0,
    search_after_ms: float = 400.0,
    hp_hz: float = 1000.0,
    mic_distance_m: float = 0.0,
    min_snr_db: float = 12.0,
) -> list[AudioOnset | None]:
    """For each GPIO edge, find the buzz onset in `audio_path` and express it
    as `onset_wall - edge` in ms. `sample0_wall_ns` / `rate_hz` must come
    from audio_align.parse_mic_sidecar so this matches the aligner.

    The window spans both sides of the edge: the sign of the anchor error
    is not settled (plans/audio-video-sync-residual-validation.md), so a
    buzz may be placed before its own edge. Pulses must be spaced further
    apart than the window (~0.7 s) so only one falls in it."""
    import soundfile as sf

    flight_ns = int(mic_distance_m / SPEED_OF_SOUND_M_S * 1e9)
    out: list[AudioOnset | None] = []
    with sf.SoundFile(audio_path) as snd:
        n_total = snd.frames
        for edge in edges_ns:
            centre = (edge - sample0_wall_ns) * rate_hz / 1e9
            start = int(centre - search_before_ms / 1000.0 * rate_hz)
            stop = int(centre + search_after_ms / 1000.0 * rate_hz)
            start, stop = max(0, start), min(n_total, stop)
            if stop - start < int(0.02 * rate_hz):
                out.append(None)
                continue
            snd.seek(start)
            block = snd.read(stop - start, dtype="float64", always_2d=False)
            hit = find_transient_onset(
                block,
                rate_hz,
                expected_index=int(centre - start),
                hp_hz=hp_hz,
                noise_percentile=20.0,
            )
            if hit is None or hit[1] < min_snr_db:
                out.append(None)
                continue
            onset_wall = sample0_wall_ns + int(round((start + hit[0]) / rate_hz * 1e9))
            out.append(
                AudioOnset(
                    edge_ns=edge,
                    onset_wall_ns=onset_wall,
                    offset_ms=(onset_wall - flight_ns - edge) / 1e6,
                    snr_db=hit[1],
                )
            )
    return out


# --------------------------------------------------------------------------- #
# Video                                                                       #
# --------------------------------------------------------------------------- #


def _iter_gray_frames(video_path: str, size: tuple[int, int]):
    import cv2

    cap = cv2.VideoCapture(video_path)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            yield cv2.resize(gray, size, interpolation=cv2.INTER_AREA).astype(
                np.float32
            )
    finally:
        cap.release()


def expected_led_state(
    frame_ts_ns: np.ndarray,
    edges_ns: list[int],
    pulse_ms: float,
    exposure_us: np.ndarray | None = None,
) -> np.ndarray:
    """1.0 for frames whose exposure plausibly overlaps an LED pulse, else 0.
    Generous on purpose (it only picks which pixels are the LED)."""
    exp_ns = (
        np.asarray(exposure_us, dtype=np.float64) * 1e3
        if exposure_us is not None
        else np.full(frame_ts_ns.size, 20e6)
    )
    state = np.zeros(frame_ts_ns.size, dtype=np.float64)
    for edge in edges_ns:
        lo, hi = edge - exp_ns - 40e6, edge + pulse_ms * 1e6 + 40e6
        state[(frame_ts_ns >= lo) & (frame_ts_ns <= hi)] = 1.0
    return state


def led_trace(
    video_path: str,
    expected: np.ndarray,
    *,
    size: tuple[int, int] = (160, 90),
    top_frac: float = 0.002,
) -> tuple[np.ndarray, np.ndarray]:
    """Brightness of the LED region per frame, and the pixel mask used.

    Two streaming passes over the video (memory stays flat for long files):
    1) per-pixel correlation with `expected` (frames during pulses vs not);
       the most positively correlated pixels are the LED,
    2) mean brightness of those pixels per frame."""
    n = expected.size
    w, h = size
    sx = np.zeros((h, w))
    sxx = np.zeros((h, w))
    sxy = np.zeros((h, w))
    y_mean = float(expected.mean()) if n else 0.0
    count = 0
    for i, g in enumerate(_iter_gray_frames(video_path, size)):
        if i >= n:
            break
        y = expected[i] - y_mean
        sx += g
        sxx += g * g
        sxy += g * y
        count += 1
    if count < 3:
        raise ValueError(f"{video_path}: only {count} frames decoded")
    y = expected[:count] - y_mean
    var_x = sxx - sx * sx / count
    var_y = float(np.sum(y * y))
    corr = sxy / np.sqrt(np.maximum(var_x * var_y, 1e-12))
    k = max(4, int(corr.size * top_frac))
    thresh = np.partition(corr.ravel(), -k)[-k]
    mask = corr >= thresh

    trace = np.empty(count)
    for i, g in enumerate(_iter_gray_frames(video_path, size)):
        if i >= count:
            break
        trace[i] = float(g[mask].mean())
    return trace, mask


@dataclass
class LedOnset:
    edge_ns: int
    frame_index: int
    frame_ts_ns: int
    offset_ms: float  # first lit frame's timestamp - edge
    lit_fraction: float  # 0..1 brightness of that frame vs fully on
    exposure_us: float | None


def led_onsets(
    trace: np.ndarray,
    frame_ts_ns: np.ndarray,
    edges_ns: list[int],
    *,
    exposure_us: np.ndarray | None = None,
    frame_period_ms: float | None = None,
    min_contrast: float = 10.0,
) -> list[LedOnset | None]:
    """For each edge, the first frame (from 2 frames before the edge) whose
    LED brightness rises clearly above the off level. The threshold is low
    (15% of the on/off range, or 4x the off-level noise if larger) so a
    frame lit for only the tail of its exposure still counts as first lit
    -- a halfway threshold would skip it and bias the offset late."""
    n = min(trace.size, frame_ts_ns.size)
    trace, ts = trace[:n], frame_ts_ns[:n]
    lo, hi = float(np.percentile(trace, 10)), float(np.percentile(trace, 99))
    if hi - lo < min_contrast:
        return [None] * len(edges_ns)
    off = trace[trace < lo + 0.5 * (hi - lo)]
    noise_sd = (
        1.4826 * float(np.median(np.abs(off - np.median(off)))) if off.size else 0.0
    )
    thr = lo + max(0.15 * (hi - lo), 4.0 * noise_sd)
    period_ns = (
        frame_period_ms * 1e6
        if frame_period_ms
        else float(np.median(np.diff(ts)))
        if n > 1
        else 33e6
    )
    out: list[LedOnset | None] = []
    for edge in edges_ns:
        first = int(np.searchsorted(ts, edge - 2 * period_ns))
        last = int(np.searchsorted(ts, edge + 6 * period_ns))
        idx = next((i for i in range(first, min(last, n)) if trace[i] >= thr), None)
        if idx is None:
            out.append(None)
            continue
        on_level = float(np.max(trace[idx : min(n, idx + 4)]))
        frac = (trace[idx] - lo) / max(on_level - lo, 1e-9)
        out.append(
            LedOnset(
                edge_ns=edge,
                frame_index=idx,
                frame_ts_ns=int(ts[idx]),
                offset_ms=(int(ts[idx]) - edge) / 1e6,
                lit_fraction=float(min(1.0, max(0.0, frac))),
                exposure_us=(
                    float(exposure_us[idx])
                    if exposure_us is not None and idx < len(exposure_us)
                    else None
                ),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Summaries                                                                   #
# --------------------------------------------------------------------------- #


def summarise(values_ms: list[float | None]) -> dict:
    """Stats over the detected values (None = not detected)."""
    vals = np.asarray([v for v in values_ms if v is not None], dtype=np.float64)
    out: dict = {"n": len(values_ms), "detected": int(vals.size)}
    if vals.size:
        out.update(
            {
                "mean_ms": round(float(vals.mean()), 2),
                "median_ms": round(float(np.median(vals)), 2),
                "std_ms": round(float(vals.std(ddof=1)), 2) if vals.size > 1 else 0.0,
                "min_ms": round(float(vals.min()), 2),
                "max_ms": round(float(vals.max()), 2),
            }
        )
    return out


def fraction_fit(offsets_ms: list[float], fractions: list[float]) -> dict | None:
    """Linear fit offset = a + b * lit_fraction across pulses.

    Pulses land at random phases within a frame, so the first lit frame is
    lit by anything from ~0 to 100% of its exposure. b should come out
    close to the exposure time (a check that detection works), and a is
    where the frame timestamp sits relative to the moment light arrives:
    about -exposure if timestamps mark the start of exposure, about 0 if
    they mark its end."""
    x = np.asarray(fractions, dtype=np.float64)
    y = np.asarray(offsets_ms, dtype=np.float64)
    if x.size < 4 or np.ptp(x) < 0.2:
        return None
    b, a = np.polyfit(x, y, 1)
    return {"intercept_ms": round(float(a), 2), "slope_ms": round(float(b), 2)}
