"""Stimulus generator for the sound library (plans/sound-library.md, phase 2).

A sound is described by parameters ({"type": ..., ...}); `generate` checks
them and renders mono float32 samples. The controller renders once, writes a
WAV into the library and keeps the parameters and GENERATOR_VERSION in the
sidecar, so a protocol plays a fixed file (by hash), never a re-synthesis
that a later code change could alter. Bump GENERATOR_VERSION whenever the
same parameters would render different samples.

Types:
  tone         freq_hz
  noise        colour (white/pink/brown/blue/violet), low_hz, high_hz,
               notch_low_hz/notch_high_hz (optional), seed
  gap          a noise (same parameters) with a silent gap: gap_start_s,
               gap_ms, gap_ramp_ms (gap-prepulse inhibition)
  click_train  rate_hz, click_us, polarity (positive/alternating)
  am_tone      carrier_hz, mod_hz, depth (0-1)
  fm_sweep     start_hz, end_hz, sweep (linear/log)
Every type: duration_s, level_dbfs, ramp_ms (raised-cosine onset/offset).
Level is the peak for tones, clicks, AM and sweeps, and the RMS for noise
and gaps (Gaussian noise peaks ~12 dB above its RMS; a level whose peaks
wouldn't fit under full scale is refused, not clipped).

NumPy only (controller env).
"""

from __future__ import annotations

import math
import struct

import numpy as np

GENERATOR_VERSION = 1
RATE = 48_000                  # the sound module's output rate
MAX_HZ = 0.45 * RATE           # its usable band (sound_analysis.PASSBAND_FRACTION)
MIN_HZ = 20.0
MAX_DURATION_S = 300.0
PEAK_CEILING_DBFS = -0.1       # rendered peaks must stay under this

TYPES = ("tone", "noise", "gap", "click_train", "am_tone", "fm_sweep")
COLOURS = {                    # amplitude slope: |X(f)| ∝ f^slope
    "white": 0.0, "pink": -0.5, "brown": -1.0, "blue": 0.5, "violet": 1.0,
}
RMS_TYPES = ("noise", "gap")

DEFAULTS = {
    "common": {"duration_s": 1.0, "level_dbfs": -20.0, "ramp_ms": 5.0},
    "tone": {"freq_hz": 5000.0},
    "noise": {"colour": "white", "low_hz": 1000.0, "high_hz": 20000.0,
              "notch_low_hz": None, "notch_high_hz": None, "seed": None},
    "gap": {"colour": "white", "low_hz": 1000.0, "high_hz": 20000.0,
            "notch_low_hz": None, "notch_high_hz": None, "seed": None,
            "gap_start_s": 0.5, "gap_ms": 50.0, "gap_ramp_ms": 1.0},
    "click_train": {"rate_hz": 10.0, "click_us": 100.0, "polarity": "positive",
                    "ramp_ms": 0.0},          # a ramp would swallow the first click
    "am_tone": {"carrier_hz": 8000.0, "mod_hz": 40.0, "depth": 1.0},
    "fm_sweep": {"start_hz": 2000.0, "end_hz": 20000.0, "sweep": "log"},
}


class GeneratorError(ValueError):
    """Parameters the generator refuses; .errors lists every problem."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _num(p: dict, key: str, errors: list, lo: float, hi: float,
         optional: bool = False) -> float | None:
    v = p.get(key)
    if v is None or v == "":
        if optional:
            return None
        errors.append(f"{key} is required")
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        errors.append(f"{key} must be a number")
        return None
    if not math.isfinite(v) or not lo <= v <= hi:
        errors.append(f"{key} must be between {lo:g} and {hi:g} (got {v:g})")
        return None
    return v


def normalise(params: dict) -> dict:
    """Defaults filled in, values checked and converted; raises
    GeneratorError listing every problem. A missing noise seed is drawn here
    so the returned parameters reproduce the sound exactly."""
    if not isinstance(params, dict):
        raise GeneratorError(["parameters must be an object"])
    kind = params.get("type")
    if kind not in TYPES:
        raise GeneratorError([f"type must be one of {', '.join(TYPES)}"])
    p = {**DEFAULTS["common"], **DEFAULTS[kind],
         **{k: v for k, v in params.items() if v is not None or k in ("seed",)}}
    errors: list[str] = []
    out: dict = {"type": kind, "sample_rate": RATE}
    out["duration_s"] = _num(p, "duration_s", errors, 0.001, MAX_DURATION_S)
    out["level_dbfs"] = _num(p, "level_dbfs", errors, -100.0, 0.0)
    out["ramp_ms"] = _num(p, "ramp_ms", errors, 0.0, 10_000.0)
    dur = out["duration_s"]
    if dur and out["ramp_ms"] is not None and 2 * out["ramp_ms"] / 1000 > dur + 1e-9:
        errors.append("onset + offset ramps are longer than the sound")

    if kind == "tone":
        out["freq_hz"] = _num(p, "freq_hz", errors, MIN_HZ, MAX_HZ)
    elif kind in ("noise", "gap"):
        colour = p.get("colour")
        if colour not in COLOURS:
            errors.append(f"colour must be one of {', '.join(COLOURS)}")
        out["colour"] = colour
        lo = out["low_hz"] = _num(p, "low_hz", errors, MIN_HZ, MAX_HZ)
        hi = out["high_hz"] = _num(p, "high_hz", errors, MIN_HZ, MAX_HZ)
        if lo is not None and hi is not None and hi - lo < 10:
            errors.append("high_hz must be at least 10 Hz above low_hz")
        nlo = out["notch_low_hz"] = _num(p, "notch_low_hz", errors, 0.0, MAX_HZ, True)
        nhi = out["notch_high_hz"] = _num(p, "notch_high_hz", errors, 0.0, MAX_HZ, True)
        if (nlo is None) != (nhi is None):
            errors.append("a notch needs both notch_low_hz and notch_high_hz")
        elif nlo is not None and nhi <= nlo:
            errors.append("notch_high_hz must be above notch_low_hz")
        elif (nlo is not None and lo is not None and hi is not None
              and nlo <= lo and nhi >= hi):
            errors.append("the notch removes the whole band")
        seed = p.get("seed")
        if seed in (None, ""):
            seed = int(np.random.default_rng().integers(0, 2**31 - 1))
        try:
            seed = int(seed)
            if not 0 <= seed < 2**63:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("seed must be a whole number ≥ 0")
            seed = None
        out["seed"] = seed
        if kind == "gap":
            gs = out["gap_start_s"] = _num(p, "gap_start_s", errors, 0.0, MAX_DURATION_S)
            gm = out["gap_ms"] = _num(p, "gap_ms", errors, 0.1, 60_000.0)
            gr = out["gap_ramp_ms"] = _num(p, "gap_ramp_ms", errors, 0.0, 1000.0)
            if None not in (gs, gm, gr, dur):
                if gs + (gm + 2 * gr) / 1000 > dur + 1e-9:
                    errors.append("the gap (with its ramps) runs past the end of the sound")
    elif kind == "click_train":
        out["rate_hz"] = _num(p, "rate_hz", errors, 0.1, 1000.0)
        out["click_us"] = _num(p, "click_us", errors, 1e6 / RATE, 100_000.0)
        if p.get("polarity") not in ("positive", "alternating"):
            errors.append("polarity must be positive or alternating")
        out["polarity"] = p.get("polarity")
        if out["rate_hz"] and out["click_us"] and out["click_us"] / 1e6 >= 1 / out["rate_hz"]:
            errors.append("clicks are as long as the gap between them")
    elif kind == "am_tone":
        out["carrier_hz"] = _num(p, "carrier_hz", errors, MIN_HZ, MAX_HZ)
        out["mod_hz"] = _num(p, "mod_hz", errors, 0.1, 5000.0)
        out["depth"] = _num(p, "depth", errors, 0.0, 1.0)
        c, m = out["carrier_hz"], out["mod_hz"]
        if c and m and c + m > MAX_HZ:
            errors.append(f"carrier + modulation ({c + m:g} Hz) is above {MAX_HZ:g} Hz")
    elif kind == "fm_sweep":
        out["start_hz"] = _num(p, "start_hz", errors, MIN_HZ, MAX_HZ)
        out["end_hz"] = _num(p, "end_hz", errors, MIN_HZ, MAX_HZ)
        if p.get("sweep") not in ("linear", "log"):
            errors.append("sweep must be linear or log")
        out["sweep"] = p.get("sweep")
        if out["start_hz"] and out["end_hz"] and out["start_hz"] == out["end_hz"]:
            errors.append("start and end frequencies are the same (use a tone)")
    if errors:
        raise GeneratorError(errors)
    return out


def _ramp(n: int, samples: int) -> np.ndarray:
    """Raised-cosine rise over *samples* (0 -> 1), length n."""
    env = np.ones(n)
    k = min(samples, n // 2)
    if k > 0:
        r = 0.5 - 0.5 * np.cos(np.pi * (np.arange(k) + 0.5) / k)
        env[:k] = r
        env[n - k:] = r[::-1]
    return env


def _noise(p: dict, n: int) -> np.ndarray:
    """Coloured, band-limited Gaussian noise with unit RMS."""
    rng = np.random.default_rng(p["seed"])
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / RATE)
    gain = np.zeros_like(f)
    pos = f > 0
    gain[pos] = f[pos] ** COLOURS[p["colour"]]
    gain[(f < p["low_hz"]) | (f > p["high_hz"])] = 0.0
    if p.get("notch_low_hz") is not None:
        gain[(f >= p["notch_low_hz"]) & (f <= p["notch_high_hz"])] = 0.0
    x = np.fft.irfft(spec * gain, n)
    rms = np.sqrt(np.mean(x ** 2))
    return x / rms if rms > 0 else x


def render(p: dict) -> np.ndarray:
    """Samples (float64, mono) for normalised parameters *p*."""
    n = max(1, round(p["duration_s"] * RATE))
    t = np.arange(n) / RATE
    level = 10 ** (p["level_dbfs"] / 20)
    kind = p["type"]
    if kind == "tone":
        x = level * np.sin(2 * np.pi * p["freq_hz"] * t)
    elif kind in ("noise", "gap"):
        x = level * _noise(p, n)
        if kind == "gap":
            g0 = round(p["gap_start_s"] * RATE)
            gr = round(p["gap_ramp_ms"] / 1000 * RATE)
            gn = round(p["gap_ms"] / 1000 * RATE)
            env = np.ones(n)
            if gr:
                fall = 0.5 + 0.5 * np.cos(np.pi * (np.arange(gr) + 0.5) / gr)
                env[g0:g0 + gr] = fall[:len(env[g0:g0 + gr])]
                env[g0 + gr + gn:g0 + 2 * gr + gn] = fall[::-1][:len(env[g0 + gr + gn:g0 + 2 * gr + gn])]
            env[g0 + gr:g0 + gr + gn] = 0.0
            x = x * env
    elif kind == "click_train":
        x = np.zeros(n)
        width = max(1, round(p["click_us"] / 1e6 * RATE))
        period = RATE / p["rate_hz"]
        k = 0
        while round(k * period) < n:
            s = round(k * period)
            sign = -1.0 if p["polarity"] == "alternating" and k % 2 else 1.0
            x[s:s + width] = sign * level
            k += 1
    elif kind == "am_tone":
        mod = 1 - p["depth"] / 2 * (1 - np.cos(2 * np.pi * p["mod_hz"] * t))
        x = level * mod * np.sin(2 * np.pi * p["carrier_hz"] * t)
    elif kind == "fm_sweep":
        f0, f1, dur = p["start_hz"], p["end_hz"], p["duration_s"]
        if p["sweep"] == "linear":
            phase = 2 * np.pi * (f0 * t + (f1 - f0) / (2 * dur) * t ** 2)
        else:
            k = math.log(f1 / f0) / dur
            phase = 2 * np.pi * f0 * (np.expm1(k * t)) / k
        x = level * np.sin(phase)
    else:  # pragma: no cover -- normalise() refuses unknown types
        raise GeneratorError([f"unknown type {kind}"])
    return x * _ramp(n, round(p["ramp_ms"] / 1000 * RATE))


def generate(params: dict) -> tuple[np.ndarray, dict]:
    """(float32 mono samples, normalised parameters incl. seed and version).
    Raises GeneratorError (bad parameters, or peaks over full scale)."""
    p = normalise(params)
    x = render(p)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 10 ** (PEAK_CEILING_DBFS / 20):
        peak_db = 20 * math.log10(peak)
        what = "RMS" if p["type"] in RMS_TYPES else "level"
        raise GeneratorError([
            f"peaks would reach {peak_db:+.1f} dBFS and clip: lower the {what} "
            f"by at least {peak_db - PEAK_CEILING_DBFS:.1f} dB"])
    p["generator_version"] = GENERATOR_VERSION
    return x.astype(np.float32), p


def default_name(p: dict) -> str:
    """A file name describing the sound, e.g. noise_pink_1-20kHz_40ms_-20dB.wav."""
    def hz(v):
        return f"{v / 1000:g}k" if v >= 1000 else f"{v:g}"

    def dur(s):
        return f"{s * 1000:g}ms" if s < 1 else f"{s:g}s"

    lvl = f"{p['level_dbfs']:g}dB"
    kind = p["type"]
    if kind == "tone":
        parts = ["tone", f"{hz(p['freq_hz'])}Hz"]
    elif kind in ("noise", "gap"):
        parts = [kind, p["colour"], f"{hz(p['low_hz'])}-{hz(p['high_hz'])}Hz"]
        if kind == "gap":
            parts.append(f"gap{p['gap_ms']:g}ms")
    elif kind == "click_train":
        parts = ["clicks", f"{p['rate_hz']:g}Hz", f"{p['click_us']:g}us"]
    elif kind == "am_tone":
        parts = ["am", f"{hz(p['carrier_hz'])}Hz", f"mod{p['mod_hz']:g}Hz"]
    else:
        parts = ["sweep", f"{hz(p['start_hz'])}-{hz(p['end_hz'])}Hz", p["sweep"]]
    name = "_".join([*parts, dur(p["duration_s"]), lvl]).replace(".", "p")
    return name[:80] + ".wav"


def wav_bytes(x: np.ndarray, rate: int = RATE) -> bytes:
    """Mono 24-bit PCM WAV of *x* (-1..1)."""
    v = np.round(np.clip(x, -1.0, 1.0 - 2**-23) * 2**23).astype(np.int32)
    b = np.empty((len(v), 3), dtype=np.uint8)
    b[:, 0] = v & 0xFF
    b[:, 1] = (v >> 8) & 0xFF
    b[:, 2] = (v >> 16) & 0xFF
    data = b.tobytes()
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 3, 3, 24)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(data)) + data
            + (b"\0" if len(data) & 1 else b""))
