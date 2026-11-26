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
  click        click_us, polarity (positive/negative): one rectangular click;
               a protocol repeats it (the library holds single sounds)
  am_tone      carrier_hz, mod_hz, depth (0-1)
  fm_sweep     start_hz, end_hz, sweep (linear/log)
Every type except click: duration_s, level_dbfs, ramp_ms (raised-cosine
onset/offset). Level is the RMS over the plateau (the sound without its
ramps), the convention for stimulus levels
and the figure that maps to dB SPL once an output is calibrated. A click's
level is its peak (peak-equivalent SPL). Peaks that wouldn't fit under full
scale are refused, not clipped (Gaussian noise peaks ~12 dB above its RMS).
Version 1 (2026-11-26, morning) used the peak for tones, AM and sweeps and
had a click train type. A gap-in-noise type was dropped the same day: a
protocol builds a gap from two noise steps.

NumPy only (controller env).
"""

from __future__ import annotations

import math
import struct

import numpy as np

GENERATOR_VERSION = 2
RATE = 48_000                  # the sound module's output rate
MAX_HZ = 0.45 * RATE           # its usable band (sound_analysis.PASSBAND_FRACTION)
MIN_HZ = 20.0
MAX_DURATION_S = 300.0
PEAK_CEILING_DBFS = -0.1       # rendered peaks must stay under this

TYPES = ("tone", "noise", "click", "am_tone", "fm_sweep")
COLOURS = {                    # amplitude slope: |X(f)| ∝ f^slope
    "white": 0.0, "pink": -0.5, "brown": -1.0, "blue": 0.5, "violet": 1.0,
}
PEAK_TYPES = ("click",)

DEFAULTS = {
    "common": {"duration_s": 1.0, "level_dbfs": -20.0, "ramp_ms": 5.0},
    "tone": {"freq_hz": 5000.0},
    "noise": {"colour": "white", "low_hz": 1000.0, "high_hz": 20000.0,
              "notch_low_hz": None, "notch_high_hz": None, "seed": None},
    "click": {"click_us": 100.0, "polarity": "positive", "ramp_ms": 0.0},
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
    if kind == "click":
        # The file is the click itself; ramps don't apply.
        p["duration_s"], p["ramp_ms"] = 0.001, 0.0
    out["duration_s"] = _num(p, "duration_s", errors, 0.001, MAX_DURATION_S)
    out["level_dbfs"] = _num(p, "level_dbfs", errors, -100.0, 0.0)
    out["ramp_ms"] = _num(p, "ramp_ms", errors, 0.0, 10_000.0)
    dur = out["duration_s"]
    if dur and out["ramp_ms"] is not None and 2 * out["ramp_ms"] / 1000 > dur + 1e-9:
        errors.append("onset + offset ramps are longer than the sound")

    if kind == "tone":
        out["freq_hz"] = _num(p, "freq_hz", errors, MIN_HZ, MAX_HZ)
    elif kind == "noise":
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
    elif kind == "click":
        out["click_us"] = _num(p, "click_us", errors, 1e6 / RATE, 100_000.0)
        if p.get("polarity") not in ("positive", "negative"):
            errors.append("polarity must be positive or negative")
        out["polarity"] = p.get("polarity")
        if out["click_us"]:
            out["duration_s"] = round(max(1, round(out["click_us"] / 1e6 * RATE)) / RATE, 9)
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


def _plateau(n: int, ramp: int) -> slice:
    """The samples a level is measured over: all but the ramps."""
    k = min(ramp, n // 2)
    return slice(k, n - k) if n - 2 * k > 0 else slice(0, n)


def render(p: dict) -> np.ndarray:
    """Samples (float64, mono) for normalised parameters *p*."""
    n = max(1, round(p["duration_s"] * RATE))
    t = np.arange(n) / RATE
    level = 10 ** (p["level_dbfs"] / 20)
    ramp = round(p["ramp_ms"] / 1000 * RATE)
    kind = p["type"]
    if kind == "click":
        sign = -1.0 if p["polarity"] == "negative" else 1.0
        return np.full(n, sign * level)
    # Unit-shaped waveform, then scaled so its plateau RMS is the level.
    if kind == "tone":
        x = np.sin(2 * np.pi * p["freq_hz"] * t)
    elif kind == "noise":
        x = _noise(p, n)
    elif kind == "am_tone":
        mod = 1 - p["depth"] / 2 * (1 - np.cos(2 * np.pi * p["mod_hz"] * t))
        x = mod * np.sin(2 * np.pi * p["carrier_hz"] * t)
    elif kind == "fm_sweep":
        f0, f1, dur = p["start_hz"], p["end_hz"], p["duration_s"]
        if p["sweep"] == "linear":
            phase = 2 * np.pi * (f0 * t + (f1 - f0) / (2 * dur) * t ** 2)
        else:
            k = math.log(f1 / f0) / dur
            phase = 2 * np.pi * f0 * (np.expm1(k * t)) / k
        x = np.sin(phase)
    else:  # pragma: no cover -- normalise() refuses unknown types
        raise GeneratorError([f"unknown type {kind}"])
    rms = float(np.sqrt(np.mean(x[_plateau(n, ramp)] ** 2)))
    x = x * (level / rms) if rms > 0 else x
    return x * _ramp(n, ramp)


def levels(x: np.ndarray, p: dict) -> dict:
    """What a rendered sound measures: plateau RMS, peak and headroom (dB
    below full scale), shown beside the level setting."""
    n = len(x)
    ramp = round(p["ramp_ms"] / 1000 * RATE)
    sl = _plateau(n, ramp)
    seg = x[sl]
    rms = float(np.sqrt(np.mean(np.asarray(seg, float) ** 2))) if len(seg) else 0.0
    peak = float(np.max(np.abs(x))) if n else 0.0

    def db(v):
        return round(20 * math.log10(v), 2) if v > 0 else None

    return {"plateau_rms_dbfs": db(rms), "peak_dbfs": db(peak),
            "headroom_db": round(-20 * math.log10(peak), 2) if peak > 0 else None}


def generate(params: dict, *, max_s: float | None = None) -> tuple[np.ndarray, dict]:
    """(float32 mono samples, normalised parameters incl. seed and version).
    *max_s* renders only that much (a preview of a long sound; the
    parameters still say the full length). Raises GeneratorError (bad
    parameters, or peaks over full scale)."""
    p = normalise(params)
    q = dict(p)
    if max_s is not None and q["duration_s"] > max_s:
        q["duration_s"] = max_s
    x = render(q)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak > 10 ** (PEAK_CEILING_DBFS / 20):
        peak_db = 20 * math.log10(peak)
        raise GeneratorError([
            f"peaks would reach {peak_db:+.1f} dBFS and clip: lower the level "
            f"by at least {peak_db - PEAK_CEILING_DBFS:.1f} dB"])
    p["generator_version"] = GENERATOR_VERSION
    return x.astype(np.float32), p


def default_name(p: dict) -> str:
    """A file name describing the sound, e.g. noise_pink_1k-20kHz_40ms_-20dBrms.wav."""
    def hz(v):
        return f"{v / 1000:g}k" if v >= 1000 else f"{v:g}"

    def dur(s):
        return f"{s * 1000:g}ms" if s < 1 else f"{s:g}s"

    kind = p["type"]
    lvl = f"{p['level_dbfs']:g}dB" + ("pk" if kind in PEAK_TYPES else "rms")
    if kind == "tone":
        parts = ["tone", f"{hz(p['freq_hz'])}Hz"]
    elif kind == "noise":
        parts = [kind, p["colour"], f"{hz(p['low_hz'])}-{hz(p['high_hz'])}Hz"]
    elif kind == "click":
        parts = ["click", f"{p['click_us']:g}us", p["polarity"]]
    elif kind == "am_tone":
        parts = ["am", f"{hz(p['carrier_hz'])}Hz", f"mod{p['mod_hz']:g}Hz"]
    else:
        parts = ["sweep", f"{hz(p['start_hz'])}-{hz(p['end_hz'])}Hz", p["sweep"]]
    if kind != "click":                 # a click's length is its width
        parts.append(dur(p["duration_s"]))
    name = "_".join([*parts, lvl]).replace(".", "p")
    return name[:80] + ".wav"


def wav_bytes(x: np.ndarray, rate: int = RATE, bits: int = 24) -> bytes:
    """Mono PCM WAV of *x* (-1..1): 24-bit for the library, 16-bit for a
    browser preview."""
    if bits == 16:
        data = np.round(np.clip(x, -1.0, 1.0 - 2**-15) * 2**15).astype("<i2").tobytes()
    else:
        v = np.round(np.clip(x, -1.0, 1.0 - 2**-23) * 2**23).astype(np.int32)
        b = np.empty((len(v), 3), dtype=np.uint8)
        b[:, 0] = v & 0xFF
        b[:, 1] = (v >> 8) & 0xFF
        b[:, 2] = (v >> 16) & 0xFF
        data = b.tobytes()
        bits = 24
    width = bits // 8
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * width, width, bits)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"data" + struct.pack("<I", len(data)) + data
            + (b"\0" if len(data) & 1 else b""))
