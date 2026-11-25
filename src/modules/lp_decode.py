"""
SAVIOUR - Lightning Pose heatmap decoding (pure NumPy, no torch).

Turns the raw per-keypoint heatmaps a single-view Lightning Pose model's
compiled .hef emits into (x, y, confidence) per keypoint, matching Lightning
Pose v2.4.x exactly:

  HeatmapHead.forward          final spatial softmax, temperature 1
                               (cut out of the .hef by
                               tools/convert_lp_pose_to_hailo.py and done here)
  run_subpixelmaxima           upsample x2, downsample_factor times
                               (bicubic, align_corners=False, then kornia's
                               5x5 pyramid Gaussian with a zero border);
                               spatial softmax of heatmaps * 1000;
                               spatial expectation (pixel-index grid);
                               confidence = evaluate_heatmaps_at_location
                               (sum of the softmax over the 5x5 window at the
                               truncated pre-offset location);
                               subtract 0.5 / 1.5 / 2.5 for factor 1 / 2 / 3
                               (geometrically 3.5 for factor 3, so LP's own
                               keypoints are +1 input px at factor 3; matched
                               here on purpose. Factor 2, the default, is
                               unbiased; factor 1 keeps ~0.2 px sub-pixel bias)
  model_to_frame_batch         x * frame_w / input_w, y * frame_h / input_h
                               (LP resizes by stretching; no letterbox)

Used by hailo_camera on the Pi and by tools/convert_lp_pose_to_hailo.py's
quantisation check on the x86 box, so both decode identically.

The .hef contract (also enforced at model load, see check_output_shape):
  input   (H_in, W_in, 3) uint8, BGR channel order, plain resize of the frame;
          ImageNet normalisation folded into the .hef
  output  one layer, read as FLOAT32 (HailoRT dequantises 8- or 16-bit
          outputs), shape (Hm, Wm, K) channels-last, raw logits before any
          softmax; Hm = H_in / 2**downsample_factor, Wm likewise

Model metadata lives in a JSON sidecar next to the .hef (<name>.json); see
validate_meta for the schema.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

LP_MODEL_TYPE = "lp_heatmap"
# LP's grid-offset correction is only defined for these factors.
_GRID_OFFSET = {1: 0.5, 2: 1.5, 3: 2.5}
LP_TEMPERATURE = 1000.0          # HeatmapHead.temperature (hard-coded upstream)
_CONF_HALF_WINDOW = 2            # floor(sigma 1.25 * num_stds 2)
_BICUBIC_A = -0.75               # torch's bicubic coefficient
_PYRAMID_1D = np.array([1, 4, 6, 4, 1], dtype=np.float32) / 16.0
# The order hailo_camera hands frames to the model in (picamera2 "RGB888" is
# B, G, R in memory). The converter folds the RGB->BGR swap into the first conv.
FEED_CHANNEL_ORDER = "BGR"


class LPModelError(ValueError):
    """Sidecar metadata missing/invalid, or the .hef doesn't match it."""


# ---------------------------------------------------------------------------
# Metadata sidecar
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LPModelMeta:
    keypoint_names: tuple[str, ...]
    input_size: tuple[int, int]               # (height, width)
    downsample_factor: int
    skeleton: tuple[tuple[str, str], ...] = ()
    softmax_temperature: float = LP_TEMPERATURE
    input_channel_order: str = FEED_CHANNEL_ORDER
    # (width, height) of the frames the model was trained on, if known: the
    # camera's main stream must have the same aspect, or LP's stretch-resize
    # distorts differently from training.
    source_frame_size: tuple[int, int] | None = None
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def heatmap_stride(self) -> int:
        return 2 ** self.downsample_factor

    @property
    def heatmap_shape(self) -> tuple[int, int, int]:
        """Expected .hef output shape, (Hm, Wm, K)."""
        h, w = self.input_size
        s = self.heatmap_stride
        return h // s, w // s, len(self.keypoint_names)

    def csv_columns(self) -> list[str]:
        return [c for n in self.keypoint_names
                for c in (f"kp_{n}_x", f"kp_{n}_y", f"kp_{n}_conf")]

    def to_dict(self) -> dict:
        d = {
            "type": LP_MODEL_TYPE,
            "keypoint_names": list(self.keypoint_names),
            "skeleton": [list(e) for e in self.skeleton],
            "input_size": list(self.input_size),
            "downsample_factor": self.downsample_factor,
            "heatmap_stride": self.heatmap_stride,
            "softmax_temperature": self.softmax_temperature,
            "input_channel_order": self.input_channel_order,
        }
        if self.source_frame_size:
            d["source_frame_size"] = list(self.source_frame_size)
        d.update(self.extra)
        return d


_KNOWN_KEYS = {"type", "keypoint_names", "skeleton", "input_size",
               "downsample_factor", "heatmap_stride", "softmax_temperature",
               "input_channel_order", "source_frame_size"}


def _pos_int_pair(v, name) -> tuple[int, int]:
    if (not isinstance(v, (list, tuple)) or len(v) != 2
            or not all(isinstance(x, int) and not isinstance(x, bool) and x > 0
                       for x in v)):
        raise LPModelError(f"{name} must be two positive integers, got {v!r}")
    return int(v[0]), int(v[1])


def validate_meta(d: dict) -> LPModelMeta:
    """Parse and check a sidecar dict. Raises LPModelError naming the field."""
    if not isinstance(d, dict):
        raise LPModelError("model metadata must be a JSON object")
    if d.get("type") != LP_MODEL_TYPE:
        raise LPModelError(f"type must be {LP_MODEL_TYPE!r}, got {d.get('type')!r}")

    names = d.get("keypoint_names")
    if (not isinstance(names, list) or not names
            or not all(isinstance(n, str) and n.strip() for n in names)):
        raise LPModelError("keypoint_names must be a non-empty list of names")
    if len(set(names)) != len(names):
        raise LPModelError("keypoint_names has duplicates")
    bad = [n for n in names if not all(c.isalnum() or c in "_-." for c in n)]
    if bad:
        raise LPModelError(f"keypoint names may use only letters, digits, _ - . : {bad}")

    skeleton = d.get("skeleton") or []
    if not isinstance(skeleton, list):
        raise LPModelError("skeleton must be a list of [name, name] pairs")
    edges = []
    for e in skeleton:
        if (not isinstance(e, (list, tuple)) or len(e) != 2
                or e[0] not in names or e[1] not in names):
            raise LPModelError(f"skeleton edge {e!r} must name two keypoints")
        edges.append((e[0], e[1]))

    input_size = _pos_int_pair(d.get("input_size"), "input_size [height, width]")

    df = d.get("downsample_factor")
    if df not in _GRID_OFFSET or isinstance(df, bool):
        raise LPModelError(f"downsample_factor must be 1, 2 or 3, got {df!r}")
    stride = 2 ** df
    if "heatmap_stride" in d and d["heatmap_stride"] != stride:
        raise LPModelError(f"heatmap_stride {d['heatmap_stride']!r} does not "
                           f"match downsample_factor {df} (stride {stride})")
    if input_size[0] % stride or input_size[1] % stride:
        raise LPModelError(f"input_size {list(input_size)} is not a multiple "
                           f"of the heatmap stride {stride}")

    temp = d.get("softmax_temperature", LP_TEMPERATURE)
    if (not isinstance(temp, (int, float)) or isinstance(temp, bool)
            or not math.isfinite(temp) or temp <= 0):
        raise LPModelError(f"softmax_temperature must be > 0, got {temp!r}")

    order = d.get("input_channel_order")
    if order != FEED_CHANNEL_ORDER:
        raise LPModelError(
            f"input_channel_order must be {FEED_CHANNEL_ORDER!r} (hailo_camera "
            f"feeds BGR frames; build the .hef with tools/convert_lp_pose_to_hailo.py), "
            f"got {order!r}")

    src = d.get("source_frame_size")
    src = _pos_int_pair(src, "source_frame_size [width, height]") if src else None

    return LPModelMeta(
        keypoint_names=tuple(names), input_size=input_size,
        downsample_factor=int(df), skeleton=tuple(edges),
        softmax_temperature=float(temp), input_channel_order=order,
        source_frame_size=src,
        extra={k: v for k, v in d.items() if k not in _KNOWN_KEYS},
    )


def load_meta(path: str) -> LPModelMeta:
    try:
        with open(path) as f:
            d = json.load(f)
    except FileNotFoundError:
        raise LPModelError(f"model metadata not found: {path}") from None
    except (OSError, json.JSONDecodeError) as e:
        raise LPModelError(f"model metadata unreadable ({path}): {e}") from None
    return validate_meta(d)


def check_input_shape(meta: LPModelMeta, shape) -> None:
    """The .hef's input vstream shape must be (H_in, W_in, 3) (optionally
    with a leading 1) matching meta.input_size."""
    s = tuple(int(x) for x in shape)
    if len(s) == 4 and s[0] == 1:
        s = s[1:]
    want = (*meta.input_size, 3)
    if s != want:
        raise LPModelError(f"HEF input shape {tuple(shape)} != metadata "
                           f"input_size {list(meta.input_size)} x 3 channels {want}")


def check_output_shape(meta: LPModelMeta, shape) -> None:
    """The .hef's single output must be (Hm, Wm, K), channels-last (a leading
    batch dim of 1 is accepted). Fails loudly rather than guessing a layout."""
    s = tuple(int(x) for x in shape)
    if len(s) == 4 and s[0] == 1:
        s = s[1:]
    want = meta.heatmap_shape
    if s == want:
        return
    hint = ""
    if len(s) == 3 and s == (want[2], want[0], want[1]):
        hint = " (looks channels-first; HailoRT should give channels-last)"
    raise LPModelError(f"HEF output shape {tuple(shape)} != expected (Hm, Wm, K) "
                       f"{want} from input_size {list(meta.input_size)}, stride "
                       f"{meta.heatmap_stride}, {len(meta.keypoint_names)} keypoints{hint}")


# ---------------------------------------------------------------------------
# Input preparation
# ---------------------------------------------------------------------------

def prepare_input(frame_bgr: np.ndarray, input_size: tuple[int, int]) -> np.ndarray:
    """Stretch-resize a BGR uint8 frame to the model input (H_in, W_in, 3),
    with cv2.INTER_LINEAR as LP's own Model.predict_frame does. No colour
    swap: the .hef takes BGR (see FEED_CHANNEL_ORDER)."""
    import cv2
    h, w = input_size
    if frame_bgr.shape[:2] == (h, w):
        return np.ascontiguousarray(frame_bgr)
    return cv2.resize(frame_bgr, (w, h), interpolation=cv2.INTER_LINEAR)


# ---------------------------------------------------------------------------
# LP's post-processing, ported
# ---------------------------------------------------------------------------

def spatial_softmax(x: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    """Softmax over the last two axes of a (K, H, W) array (kornia
    spatial_softmax2d, with the temperature multiplied in as LP does)."""
    k, h, w = x.shape
    flat = x.reshape(k, h * w).astype(np.float32, copy=False) * np.float32(temperature)
    flat = flat - flat.max(axis=1, keepdims=True)
    np.exp(flat, out=flat)
    flat /= flat.sum(axis=1, keepdims=True)
    return flat.reshape(k, h, w)


@lru_cache(maxsize=16)
def _bicubic2x_taps(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Indices (4, 2n) and weights (4, 2n) for torch's bicubic x2,
    align_corners=False: src = (dst + 0.5) / 2 - 0.5, taps floor(src)-1 ..
    floor(src)+2 clamped to the edge (torch's upsample_get_value_bounded)."""
    a = _BICUBIC_A
    dst = np.arange(2 * n, dtype=np.float64)
    src = (dst + 0.5) / 2.0 - 0.5
    i0 = np.floor(src)
    t = src - i0

    def near(x):   # |x| <= 1
        return ((a + 2) * x - (a + 3)) * x * x + 1

    def far(x):    # 1 < |x| < 2
        return ((a * x - 5 * a) * x + 8 * a) * x - 4 * a

    weights = np.stack([far(t + 1), near(t), near(1 - t), far(2 - t)])
    idx = np.stack([i0 - 1, i0, i0 + 1, i0 + 2]).astype(np.int64)
    np.clip(idx, 0, n - 1, out=idx)
    return idx, weights.astype(np.float32)


def _bicubic2x_axis(x: np.ndarray, axis: int) -> np.ndarray:
    idx, w = _bicubic2x_taps(x.shape[axis])
    shape = [1] * x.ndim
    shape[axis] = -1
    out = np.take(x, idx[0], axis=axis) * w[0].reshape(shape)
    for k in range(1, 4):
        out += np.take(x, idx[k], axis=axis) * w[k].reshape(shape)
    return out


def _gauss5_axis(x: np.ndarray, axis: int) -> np.ndarray:
    """5-tap [1,4,6,4,1]/16 along `axis` with a zero border (separable half
    of kornia's pyramid kernel; zero padding keeps the separation exact)."""
    pad = [(0, 0)] * x.ndim
    pad[axis] = (2, 2)
    xp = np.pad(x, pad)
    n = x.shape[axis]
    out = None
    for k, c in enumerate(_PYRAMID_1D):
        sl = [slice(None)] * x.ndim
        sl[axis] = slice(k, k + n)
        term = xp[tuple(sl)] * c
        out = term if out is None else out + term
    return out


def upsample2x(h: np.ndarray) -> np.ndarray:
    """LP's heads.heatmap.upsample on a (K, H, W) array: bicubic to (2H, 2W),
    then the 5x5 pyramid Gaussian with a constant (zero) border."""
    up = _bicubic2x_axis(_bicubic2x_axis(h, 2), 1)
    return _gauss5_axis(_gauss5_axis(up, 2), 1)


def _window_conf(soft: np.ndarray, locs: np.ndarray) -> np.ndarray:
    """evaluate_heatmaps_at_location: sum of `soft` over the 5x5 window
    centred on the truncated (x, y) location, zero outside the map."""
    k, h, w = soft.shape
    p = _CONF_HALF_WINDOW
    padded = np.pad(soft, ((0, 0), (p, p), (p, p)))
    xi = np.trunc(locs[:, 0]).astype(np.int64) + p
    yi = np.trunc(locs[:, 1]).astype(np.int64) + p
    conf = np.empty(k, dtype=np.float32)
    for c in range(k):
        y0, x0 = yi[c] - p, xi[c] - p
        if y0 < 0 or x0 < 0 or yi[c] + p >= h + 2 * p or xi[c] + p >= w + 2 * p:
            # LP indexes out of range here only for NaN/garbage locations.
            conf[c] = np.nan
            continue
        conf[c] = padded[c, y0:yi[c] + p + 1, x0:xi[c] + p + 1].sum()
    return conf


def subpixel_maxima(heatmaps: np.ndarray, downsample_factor: int,
                    temperature: float = LP_TEMPERATURE
                    ) -> tuple[np.ndarray, np.ndarray]:
    """LP's run_subpixelmaxima on (K, Hm, Wm) heatmaps that have already been
    through the head's final softmax. Returns (preds (K, 2) as x, y in
    model-input pixels, confidences (K,))."""
    if downsample_factor not in _GRID_OFFSET:
        raise LPModelError(f"downsample_factor must be 1, 2 or 3, got {downsample_factor}")
    h = heatmaps.astype(np.float32, copy=False)
    for _ in range(downsample_factor):
        h = upsample2x(h)
    soft = spatial_softmax(h, temperature)
    k, hh, ww = soft.shape
    xs = np.arange(ww, dtype=np.float32)
    ys = np.arange(hh, dtype=np.float32)
    px = (soft.sum(axis=1) * xs).sum(axis=1)
    py = (soft.sum(axis=2) * ys).sum(axis=1)
    preds = np.stack([px, py], axis=1)
    conf = _window_conf(soft, preds)
    return preds - np.float32(_GRID_OFFSET[downsample_factor]), conf


def raw_to_khw(raw: np.ndarray, meta: LPModelMeta) -> np.ndarray:
    """(Hm, Wm, K) or (1, Hm, Wm, K) FLOAT32 .hef output -> (K, Hm, Wm)."""
    return np.ascontiguousarray(np.moveaxis(raw_to_hwk(raw, meta), -1, 0))


def decode_heatmaps(heatmaps_khw: np.ndarray, meta: LPModelMeta,
                    out_size: tuple[int, int] | None = None) -> np.ndarray:
    """REFERENCE decode (pure NumPy, slow: ~50 ms for 11 x 64x64 on a Pi 5).
    Raw logits (K, Hm, Wm) -> (K, 3) x, y, conf in `out_size` (width,
    height) pixels, default model-input pixels. Applies the head's final
    softmax first, as LP's forward() does. The live path is decode()."""
    soft = spatial_softmax(heatmaps_khw, 1.0)
    preds, conf = subpixel_maxima(soft, meta.downsample_factor,
                                  meta.softmax_temperature)
    return np.concatenate([_to_out(preds, meta, out_size), conf[:, None]], axis=1)


def _to_out(preds: np.ndarray, meta: LPModelMeta,
            out_size: tuple[int, int] | None) -> np.ndarray:
    if out_size is None:
        return preds
    in_h, in_w = meta.input_size
    return preds * np.array([out_size[0] / in_w, out_size[1] / in_h],
                            dtype=np.float32)


# ---------------------------------------------------------------------------
# Live decode (OpenCV, channels-last, the layout HailoRT returns)
# ---------------------------------------------------------------------------
# cv2.resize INTER_CUBIC is torch's bicubic with align_corners=False (A=-0.75,
# half-pixel centres, edge-clamped taps) and sepFilter2D with BORDER_CONSTANT
# is kornia's filter2d with a zero border: they match the NumPy reference
# above to float32 rounding (tests/test_lp_decode.py). The final softmax is
# never normalised over the full map: the expectation divides by the total.

DECODE_METHODS = ("exact", "windowed")


def raw_to_hwk(raw: np.ndarray, meta: LPModelMeta) -> np.ndarray:
    a = np.asarray(raw, dtype=np.float32)
    check_output_shape(meta, a.shape)
    if a.ndim == 4:
        a = a[0]
    return np.ascontiguousarray(a)


def _softmax_hwk(x: np.ndarray) -> np.ndarray:
    import cv2
    h, w, k = x.shape
    f = x.reshape(h * w, k)
    e = cv2.exp(f - f.max(axis=0))
    e /= e.sum(axis=0)
    return e.reshape(h, w, k)


def _upsample_hwk(s: np.ndarray, times: int) -> np.ndarray:
    import cv2
    for _ in range(times):
        s = cv2.resize(s, (s.shape[1] * 2, s.shape[0] * 2),
                       interpolation=cv2.INTER_CUBIC)
        s = cv2.sepFilter2D(s, -1, _PYRAMID_1D, _PYRAMID_1D,
                            borderType=cv2.BORDER_CONSTANT)
    if s.ndim == 2:            # cv2 drops a trailing channel axis of size 1
        s = s[:, :, None]
    return s


def _conf_hwk(e: np.ndarray, total: np.ndarray, px: np.ndarray,
              py: np.ndarray, x_off: int = 0, y_off: int = 0) -> np.ndarray:
    """evaluate_heatmaps_at_location on unnormalised exp values `e` (H, W, K)
    whose per-channel sums are `total`; px/py are absolute pre-offset
    locations, (x_off, y_off) where e's [0, 0] sits in the full map."""
    h, w, k = e.shape
    p = _CONF_HALF_WINDOW
    conf = np.empty(k, dtype=np.float32)
    for c in range(k):
        if not (math.isfinite(px[c]) and math.isfinite(py[c])):
            conf[c] = np.nan
            continue
        xi = int(px[c]) - x_off      # int() truncates, as torch's int64 cast
        yi = int(py[c]) - y_off
        y0, y1 = max(0, yi - p), min(h, yi + p + 1)
        x0, x1 = max(0, xi - p), min(w, xi + p + 1)
        conf[c] = (e[y0:y1, x0:x1, c].sum() / total[c]) if y1 > y0 and x1 > x0 else 0.0
    return conf


def _decode_exact(raw_hwk: np.ndarray, meta: LPModelMeta) -> np.ndarray:
    import cv2
    s = _upsample_hwk(_softmax_hwk(raw_hwk), meta.downsample_factor)
    h, w, k = s.shape
    f = s.reshape(h * w, k)
    t = np.float32(meta.softmax_temperature)
    g = np.multiply(f, t)
    g -= cv2.reduce(f, 0, cv2.REDUCE_MAX) * t
    e = cv2.exp(g)
    e3 = e.reshape(h, w, k)
    col = e3.sum(axis=0)                                  # (W, K)
    total = col.sum(axis=0)
    px = (np.arange(w, dtype=np.float32) @ col) / total
    py = ((np.arange(h, dtype=np.float32) @ e.reshape(h, w * k))
          .reshape(w, k).sum(axis=0)) / total
    conf = _conf_hwk(e3, total, px, py)
    off = np.float32(_GRID_OFFSET[meta.downsample_factor])
    return np.stack([px - off, py - off, conf], axis=1)


# Coarse-pixel margin the upsampling chain needs around a window so the
# kept region is unaffected by the crop's artificial edges: 2 bicubic taps
# plus the 5-tap Gaussian (2 fine px) at each level, rounded up.
_WINDOW_MARGIN = 5


def _decode_windowed(raw_hwk: np.ndarray, meta: LPModelMeta,
                     radius: int) -> np.ndarray:
    """Approximation: per keypoint, upsample only a (2*radius+1)^2-coarse-px
    window around the coarse argmax and take the x1000 softmax/expectation
    there. Matches the exact decode to well under 0.1 px for a confident,
    unimodal peak (the x1000 softmax leaves ~no mass elsewhere); it can
    differ for weak or multi-peaked heatmaps, where LP's full-map
    expectation is pulled toward the other mass."""
    s = _softmax_hwk(raw_hwk)
    hm, wm, k = s.shape
    f = 2 ** meta.downsample_factor
    t = np.float32(meta.softmax_temperature)
    out = np.empty((k, 3), dtype=np.float32)
    flat_arg = s.reshape(hm * wm, k).argmax(axis=0)
    for c in range(k):
        cy, cx = divmod(int(flat_arg[c]), wm)
        # crop with margin, clipped to the map (a clipped edge is the real
        # edge, where cv2's clamp/zero-border behaviour is the exact one)
        y0, y1 = max(0, cy - radius - _WINDOW_MARGIN), min(hm, cy + radius + _WINDOW_MARGIN + 1)
        x0, x1 = max(0, cx - radius - _WINDOW_MARGIN), min(wm, cx + radius + _WINDOW_MARGIN + 1)
        up = _upsample_hwk(np.ascontiguousarray(s[y0:y1, x0:x1, c]), meta.downsample_factor)[:, :, 0]
        # kept region (fine px, relative to the crop): drop the margin except
        # where the crop edge is the map edge
        ky0 = 0 if y0 == 0 else (min(cy - radius, cy) - y0) * f
        kx0 = 0 if x0 == 0 else (min(cx - radius, cx) - x0) * f
        ky1 = up.shape[0] if y1 == hm else (cy + radius + 1 - y0) * f
        kx1 = up.shape[1] if x1 == wm else (cx + radius + 1 - x0) * f
        win = up[ky0:ky1, kx0:kx1] * t
        win = np.exp(win - win.max())
        total = win.sum()
        gy0, gx0 = y0 * f + ky0, x0 * f + kx0           # window origin, full fine grid
        px = gx0 + (win.sum(axis=0) @ np.arange(win.shape[1], dtype=np.float32)) / total
        py = gy0 + (win.sum(axis=1) @ np.arange(win.shape[0], dtype=np.float32)) / total
        conf = _conf_hwk(win[:, :, None], np.array([total], np.float32),
                         np.array([px], np.float32), np.array([py], np.float32),
                         gx0, gy0)[0]
        out[c] = (px, py, conf)
    out[:, :2] -= np.float32(_GRID_OFFSET[meta.downsample_factor])
    return out


def decode(raw: np.ndarray, meta: LPModelMeta,
           out_size: tuple[int, int] | None = None,
           method: str = "exact", window_radius: int = 8) -> np.ndarray:
    """The .hef output as HailoRT returns it ((Hm, Wm, K) float32) -> (K, 3)
    x, y, conf with x/y in `out_size` (width, height) pixels (default
    model-input pixels). method "exact" reproduces LP's run_subpixelmaxima;
    "windowed" is the faster local approximation (see _decode_windowed)."""
    hwk = raw_to_hwk(raw, meta)
    if method == "windowed":
        res = _decode_windowed(hwk, meta, window_radius)
    elif method == "exact":
        res = _decode_exact(hwk, meta)
    else:
        raise ValueError(f"decode method must be one of {DECODE_METHODS}, got {method!r}")
    res[:, :2] = _to_out(res[:, :2], meta, out_size)
    return res
