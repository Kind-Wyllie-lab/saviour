"""What a sound file contains: format, level, edges, spectrum, plus the data
the frontend draws (waveform envelope, coarse spectrogram) and plain
warnings. Used by the controller's sound library on upload
(plans/sound-library.md) and by the sound module for its own files.

NumPy only (the controller env has no soundfile): a small RIFF/WAV reader
for PCM 8/16/24/32-bit and float 32/64, including WAVE_FORMAT_EXTENSIBLE.
Anything else is refused with a message saying to convert it to WAV.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct

import numpy as np

# Library file names: also used as protocol `file` values and URL paths.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.wav$", re.IGNORECASE)

# The sound module's output rate (SoundModule._RATE) and the band its
# output can reproduce at it (the resampler's passband edge).
DEFAULT_OUTPUT_RATE = 48_000
PASSBAND_FRACTION = 0.45      # of the output rate: 21.6 kHz at 48 kHz

CLIP_LEVEL = 0.999            # |sample| at or above this counts as clipped
SILENCE_DBFS = -60.0          # leading/trailing samples below this are silence
ABRUPT_DBFS = -40.0           # a first/last sample above this will click
ENVELOPE_BINS = 800
SPEC_COLS = 200
SPEC_ROWS = 64
SPEC_FLOOR_DB = -100.0

_FMT_PCM = 1
_FMT_FLOAT = 3
_FMT_EXTENSIBLE = 0xFFFE


class SoundFileError(ValueError):
    """A file the analysis can't read; the message says what to do."""


def _db(x: float) -> float | None:
    return round(20 * math.log10(x), 2) if x > 0 else None


def read_wav(data: bytes) -> tuple[np.ndarray, int, dict]:
    """(samples float32 [frames, channels] in -1..1, sample rate, format)
    from the bytes of a WAV file. Raises SoundFileError on anything else."""
    if len(data) < 12 or data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        raise SoundFileError("Not a WAV file: convert it to WAV (PCM or float)")
    pos, fmt, payload = 12, None, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            # A streamed file may leave the size at 0 or 0xFFFFFFFF.
            payload = data[pos + 8:] if size in (0, 0xFFFFFFFF) else body
            break
        pos += 8 + size + (size & 1)
    if fmt is None or len(fmt) < 16 or payload is None:
        raise SoundFileError("Damaged WAV file: no format or data chunk")
    tag, channels, rate, _, block, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == _FMT_EXTENSIBLE and len(fmt) >= 26:
        tag = struct.unpack("<H", fmt[24:26])[0]
    if channels < 1 or rate < 1 or block < 1:
        raise SoundFileError("Damaged WAV file: bad format chunk")
    width = block // channels
    frames = len(payload) // block
    raw = np.frombuffer(payload[:frames * block], dtype=np.uint8)
    if tag == _FMT_FLOAT and width in (4, 8):
        x = raw.view("<f4" if width == 4 else "<f8").astype(np.float32)
        kind = f"float{width * 8}"
    elif tag == _FMT_PCM and width == 1:
        x = (raw.astype(np.float32) - 128.0) / 128.0
        kind = "pcm8"
    elif tag == _FMT_PCM and width in (2, 4):
        x = raw.view("<i2" if width == 2 else "<i4").astype(np.float32) / float(2 ** (8 * width - 1))
        kind = f"pcm{width * 8}"
    elif tag == _FMT_PCM and width == 3:
        b = raw.reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        x = v.astype(np.float32) / float(1 << 23)
        kind = "pcm24"
    else:
        raise SoundFileError(
            f"Unsupported WAV encoding (format {tag}, {width * 8}-bit): "
            "convert it to 16/24-bit PCM or 32-bit float")
    x = x.reshape(-1, channels)
    return x, rate, {"sample_rate": rate, "channels": channels, "format": kind,
                     "bits": bits, "frames": frames}


def _edges(mono_abs: np.ndarray, rate: int) -> dict:
    thresh = 10 ** (SILENCE_DBFS / 20)
    loud = np.flatnonzero(mono_abs >= thresh)
    if loud.size == 0:
        lead = trail = len(mono_abs) / rate
    else:
        lead = float(loud[0]) / rate
        trail = float(len(mono_abs) - 1 - loud[-1]) / rate
    click = 10 ** (ABRUPT_DBFS / 20)
    return {
        "leading_silence_s": round(lead, 4),
        "trailing_silence_s": round(trail, 4),
        "abrupt_start": bool(len(mono_abs) and mono_abs[0] >= click),
        "abrupt_end": bool(len(mono_abs) and mono_abs[-1] >= click),
    }


def _spectrum(mono: np.ndarray, rate: int, usable_hz: float) -> dict:
    """Averaged power spectrum (Welch, Hann) and figures from it."""
    n = len(mono)
    nfft = 1 << max(8, min(15, int(math.log2(max(n, 256)))))
    if n < nfft:
        mono = np.pad(mono, (0, nfft - n))
        n = nfft
    hop = nfft // 2
    win = np.hanning(nfft).astype(np.float32)
    starts = range(0, n - nfft + 1, hop)
    # At most ~400 segments: plenty for an average, bounded time.
    step = max(1, len(starts) // 400)
    power = np.zeros(nfft // 2 + 1)
    count = 0
    for s in list(starts)[::step]:
        power += np.abs(np.fft.rfft(mono[s:s + nfft] * win)) ** 2
        count += 1
    power /= max(count, 1)
    freqs = np.fft.rfftfreq(nfft, 1 / rate)
    total = float(power[1:].sum())
    if total <= 0:
        return {"dominant_hz": None, "band_hz": None, "above_usable_frac": 0.0,
                "below_1k_frac": 0.0}
    peak_i = int(np.argmax(power[1:]) + 1)
    above = power >= power[peak_i] * 0.1          # within 10 dB of the peak
    idx = np.flatnonzero(above)
    return {
        "dominant_hz": round(float(freqs[peak_i]), 1),
        "band_hz": [round(float(freqs[idx[0]]), 1), round(float(freqs[idx[-1]]), 1)],
        "above_usable_frac": round(float(power[freqs > usable_hz].sum()) / total, 4),
        "below_1k_frac": round(float(power[(freqs > 0) & (freqs < 1000)].sum()) / total, 4),
    }


def _envelope(x: np.ndarray, bins: int = ENVELOPE_BINS) -> dict:
    """Per channel min/max per bin (rounded), for drawing the waveform."""
    n = len(x)
    bins = max(1, min(bins, n))
    edges = np.linspace(0, n, bins + 1).astype(int)
    out = []
    for c in range(x.shape[1]):
        col = x[:, c]
        mins = np.minimum.reduceat(col, edges[:-1])
        maxs = np.maximum.reduceat(col, edges[:-1])
        out.append({"min": np.round(mins, 4).tolist(), "max": np.round(maxs, 4).tolist()})
    return {"bins": bins, "channels": out}


def _spectrogram(mono: np.ndarray, rate: int, cols: int = SPEC_COLS,
                 rows: int = SPEC_ROWS) -> dict:
    """Coarse dB spectrogram: *cols* time columns × *rows* linear frequency
    rows from 0 to Nyquist, relative to full scale."""
    nfft = 1024 if rate > 96_000 else 512
    n = len(mono)
    if n < nfft:
        mono = np.pad(mono, (0, nfft - n))
        n = nfft
    cols = max(1, min(cols, (n - nfft) // max(1, nfft // 4) + 1))
    starts = np.linspace(0, n - nfft, cols).astype(int)
    win = np.hanning(nfft).astype(np.float32)
    norm = (win.sum() / 2) ** 2
    frames = np.stack([mono[s:s + nfft] * win for s in starts])
    p = np.abs(np.fft.rfft(frames, axis=1)) ** 2 / norm
    # Fold the nfft/2+1 bins into *rows* bands (max power per band).
    edges = np.linspace(0, p.shape[1], rows + 1).astype(int)
    bands = np.maximum.reduceat(p, edges[:-1], axis=1)
    db = 10 * np.log10(np.maximum(bands, 10 ** (SPEC_FLOOR_DB / 10)))
    return {"cols": cols, "rows": rows, "max_hz": rate / 2, "floor_db": SPEC_FLOOR_DB,
            "duration_s": round(n / rate, 6),
            # Row-major, time first: db[col][row], row 0 = lowest frequency.
            "db": np.round(db, 1).tolist()}


def analyse(x: np.ndarray, rate: int, *, output_rate: int = DEFAULT_OUTPUT_RATE,
            render: bool = True) -> dict:
    """Analysis of samples *x* [frames, channels] at *rate*. *output_rate*
    is what the sound module plays at (energy above its usable band is
    removed at playback). With *render*, includes the envelope and
    spectrogram for drawing."""
    if x.ndim == 1:
        x = x[:, None]
    frames, channels = x.shape
    duration = frames / rate if rate else 0.0
    usable_hz = min(rate / 2, output_rate * PASSBAND_FRACTION)
    out: dict = {"duration_s": round(duration, 6), "output_rate": output_rate,
                 "usable_hz": usable_hz}

    per = []
    for c in range(channels):
        col = x[:, c].astype(np.float64)
        peak = float(np.max(np.abs(col))) if frames else 0.0
        rms = float(np.sqrt(np.mean(col ** 2))) if frames else 0.0
        per.append({
            "peak_dbfs": _db(peak), "rms_dbfs": _db(rms),
            "clipped_samples": int(np.count_nonzero(np.abs(col) >= CLIP_LEVEL)),
            "dc_offset": round(float(col.mean()), 5) if frames else 0.0,
        })
    out["channels"] = per
    peaks = [p["peak_dbfs"] for p in per if p["peak_dbfs"] is not None]
    rmss = [p["rms_dbfs"] for p in per if p["rms_dbfs"] is not None]
    out["peak_dbfs"] = max(peaks) if peaks else None
    out["rms_dbfs"] = max(rmss) if rmss else None
    out["crest_db"] = (round(out["peak_dbfs"] - out["rms_dbfs"], 2)
                       if peaks and rmss else None)
    out["clipped_samples"] = sum(p["clipped_samples"] for p in per)

    if channels == 1:
        stereo = "mono"
    elif channels >= 2 and np.allclose(x[:, 0], x[:, 1], atol=1e-4):
        stereo = "identical"
    else:
        stereo = "different"
    out["stereo"] = stereo

    mono = x[:, :2].mean(axis=1).astype(np.float32) if frames else np.zeros(1, np.float32)
    out.update(_edges(np.abs(x).max(axis=1) if frames else np.zeros(1), rate))
    out["spectrum"] = _spectrum(mono, rate, usable_hz)
    out["warnings"] = warnings_for(out, rate)
    if render:
        out["envelope"] = _envelope(x[:, :2] if frames else np.zeros((1, 1), np.float32))
        out["spectrogram"] = _spectrogram(mono, rate)
    return out


def warnings_for(a: dict, rate: int) -> list[str]:
    w = []
    if a["peak_dbfs"] is None:
        w.append("Silent: every sample is zero")
        return w
    if a["clipped_samples"]:
        w.append(f"Clips: {a['clipped_samples']} sample(s) at full scale")
    frac = a["spectrum"]["above_usable_frac"]
    if frac >= 0.01:
        w.append(f"{frac * 100:.0f} % of the energy is above {a['usable_hz'] / 1000:.1f} kHz "
                 f"and won't play at {a['output_rate'] / 1000:g} kHz")
    if a["abrupt_start"]:
        w.append("Starts abruptly: will click (add an onset ramp)")
    if a["abrupt_end"]:
        w.append("Ends abruptly: will click (add an offset ramp)")
    if a["duration_s"] and a["leading_silence_s"] >= 0.05:
        w.append(f"{a['leading_silence_s'] * 1000:.0f} ms of silence before the sound starts "
                 "(it delays the sound against its scheduled time)")
    if any(abs(c["dc_offset"]) >= 0.01 for c in a["channels"]):
        w.append("DC offset: the waveform isn't centred on zero")
    if a["stereo"] == "different":
        w.append("Left and right differ: each output plays them as recorded")
    return w


def analyse_bytes(data: bytes, **kw) -> dict:
    """read_wav + analyse, with the format, size and SHA-256 added."""
    x, rate, fmt = read_wav(data)
    a = analyse(x, rate, **kw)
    a.update(fmt)
    a["size_bytes"] = len(data)
    a["sha256"] = hashlib.sha256(data).hexdigest()
    return a


def analyse_file(path: str, **kw) -> dict:
    with open(path, "rb") as f:
        return analyse_bytes(f.read(), **kw)
