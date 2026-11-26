"""Band-limited resampling in NumPy (no scipy on the modules).

Windowed-sinc interpolation (Kaiser window): every output sample is a
weighted sum of the input samples around its position, with the sinc's
cutoff at the lower of the two Nyquist frequencies (times *rolloff*), so
downsampling removes what the new rate can't hold instead of folding it
back as aliases. Replaces `np.interp` in the sound module's load_sound,
which aliased a 192 kHz file's ultrasound into the audible band
(plans/sound-library.md).

Cost is frames_out × 2·half_width/cutoff multiply-adds per channel, with the
window weights computed once per phase of the rational ratio.
"""

from __future__ import annotations

import math

import numpy as np

_BETA = 8.6          # Kaiser window: ~-86 dB sidelobes
_BLOCK = 4096        # output samples per vectorised block


def _kernel(d: np.ndarray, fc: float, hw: int) -> np.ndarray:
    u = np.clip(d / hw, -1.0, 1.0)
    w = fc * np.sinc(fc * d) * (np.i0(_BETA * np.sqrt(1.0 - u * u)) / np.i0(_BETA))
    return np.where(np.abs(d) < hw, w, 0.0)


def resample(x: np.ndarray, src_rate: int, dst_rate: int, *,
             half_width: int = 16, rolloff: float = 0.92) -> np.ndarray:
    """*x* [frames] or [frames, channels] at *src_rate* → *dst_rate*.
    *half_width*: sinc zero crossings each side (filter sharpness);
    *rolloff*: passband edge as a fraction of the lower Nyquist."""
    src_rate, dst_rate = int(src_rate), int(dst_rate)
    if src_rate == dst_rate or len(x) == 0:
        return x
    one_d = x.ndim == 1
    data = (x[:, None] if one_d else x).astype(np.float32, copy=False)
    n_in, channels = data.shape
    g = math.gcd(src_rate, dst_rate)
    up, down = dst_rate // g, src_rate // g     # output k sits at input k*down/up
    fc = rolloff * min(1.0, up / down)          # cutoff, fraction of input Nyquist
    hw = int(math.ceil(half_width / fc))        # input samples each side
    offsets = np.arange(-hw + 1, hw + 1)
    n_out = max(1, round(n_in * up / down))
    # The fractional position repeats every *up* outputs: one weight row per
    # phase (44.1 -> 48 kHz: 160 rows), unless the ratio is awkward.
    table = None
    if up <= 4096:
        frac = np.arange(up)[:, None] / up
        table = _kernel(frac - offsets[None, :], fc, hw).astype(np.float32)
    out = np.empty((n_out, channels), dtype=np.float32)
    for start in range(0, n_out, _BLOCK):
        k = np.arange(start, min(start + _BLOCK, n_out), dtype=np.int64)
        base = (k * down) // up
        idx = base[:, None] + offsets[None, :]
        if table is not None:
            w = table[(k * down) % up]
        else:
            w = _kernel((k * down / up)[:, None] - idx, fc, hw).astype(np.float32)
        valid = (idx >= 0) & (idx < n_in)
        w = np.where(valid, w, np.float32(0))
        xs = data[np.clip(idx, 0, n_in - 1)]
        out[start:start + len(k)] = np.einsum("ij,ijc->ic", w, xs)
    return out[:, 0] if one_d else out
