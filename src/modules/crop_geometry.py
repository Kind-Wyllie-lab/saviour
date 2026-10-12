"""
Pure geometry for the camera crop / digital zoom (libcamera ScalerCrop).

No picamera2 or hardware here, so it's unit-testable anywhere. Rectangles are
(x, y, width, height) tuples in **sensor** pixel coordinates (the space
ScalerCrop and a sensor mode's `crop_limits` use) unless a name says
"normalised", which means fractions 0..1 of a mode's `crop_limits`.

The invariant everything here serves: the ScalerCrop sent to libcamera
always has the *output's* aspect ratio. The ISP scales the ScalerCrop
rectangle to the output size, so any aspect mismatch stretches the image.
Found 2026-10-09 on an IMX477 (4:3 sensor mode, 16:9 output): the old code
treated the mode's full 4:3 area as "uncropped", but libcamera's default for
a 16:9 output is a centred 16:9 band -- so a whole-frame crop squashed the
picture, and "clear crop" restored the squashed 4:3 area instead of the
original view (plans/field-install-feedback-2026-10.md, items 3 and 5).
"""

from __future__ import annotations

import math

Rect = tuple[int, int, int, int]

# Aspect presets offered by the crop editor (width / height). "free" = any.
ASPECT_PRESETS = {
    "free": None,
    "1:1": 1.0,
    "4:3": 4 / 3,
    "16:9": 16 / 9,
    "3:4": 3 / 4,
    "9:16": 9 / 16,
}

# Output dimensions are rounded to these multiples: picamera2 otherwise
# "adjusts" the requested main-stream size itself, and the H.264 encoder
# needs even dimensions.
WIDTH_ALIGN = 32
HEIGHT_ALIGN = 2
MIN_OUTPUT_DIM = 64
# Smallest crop the editor may save, as a fraction of the field of view.
MIN_NORMALISED = 0.02


def _even(v: float) -> int:
    return max(2, int(round(v)) // 2 * 2)


def fit_aspect(rect: Rect, aspect: float, limits: Rect) -> Rect:
    """The largest rectangle of `aspect` (w/h) centred on `rect` that lies
    inside `rect` -- i.e. trim the over-long side ("cover", so the drawn
    region fills the output with nothing added) -- then clamped inside
    `limits`. Used whenever a crop's aspect doesn't match the output."""
    x, y, w, h = rect
    if w <= 0 or h <= 0 or aspect <= 0:
        return default_crop(limits, aspect)
    if w / h > aspect:
        nw, nh = h * aspect, h
    else:
        nw, nh = w, w / aspect
    cx, cy = x + w / 2, y + h / 2
    return clamp_rect((round(cx - nw / 2), round(cy - nh / 2),
                       _even(nw), _even(nh)), limits)


def default_crop(limits: Rect, aspect: float) -> Rect:
    """What libcamera shows with no ScalerCrop set: the largest centred
    rectangle of the output's aspect inside the sensor mode's crop_limits.
    This -- not the full crop_limits -- is the "uncropped" view whenever the
    mode's aspect differs from the output's."""
    lx, ly, lw, lh = limits
    if lw / lh > aspect:
        w, h = lh * aspect, lh
    else:
        w, h = lw, lw / aspect
    w, h = _even(min(w, lw)), _even(min(h, lh))
    return (lx + (lw - w) // 2, ly + (lh - h) // 2, w, h)


def clamp_rect(rect: Rect, limits: Rect) -> Rect:
    """Shift (then, if still too big, shrink) `rect` to lie inside `limits`."""
    lx, ly, lw, lh = limits
    x, y, w, h = rect
    w, h = max(2, min(w, lw)), max(2, min(h, lh))
    x = min(max(x, lx), lx + lw - w)
    y = min(max(y, ly), ly + lh - h)
    return (int(x), int(y), int(w), int(h))


def normalised_to_sensor(norm: dict, limits: Rect) -> Rect:
    """A crop stored as fractions of `limits` -> sensor pixels."""
    lx, ly, lw, lh = limits
    x = lx + float(norm["x"]) * lw
    y = ly + float(norm["y"]) * lh
    w = float(norm["width"]) * lw
    h = float(norm["height"]) * lh
    return clamp_rect((round(x), round(y), _even(w), _even(h)), limits)


def legacy_to_normalised(crop: dict, limits: Rect) -> dict:
    """Convert a pre-2026-10 crop (pixel coordinates on the editor's
    snapshot, plus preview_width/height) to the normalised form. That
    snapshot showed the *default* view for the output size of the day,
    i.e. `default_crop(limits, preview aspect)`, so map through that band
    with one scale factor (the band and the preview share an aspect)."""
    pw = float(crop.get("preview_width") or 0)
    ph = float(crop.get("preview_height") or 0)
    if pw <= 0 or ph <= 0:
        raise ValueError("legacy crop has no preview size")
    bx, by, bw, bh = default_crop(limits, pw / ph)
    scale = bw / pw
    lx, ly, lw, lh = limits
    return {
        "x": (bx + float(crop["x"]) * scale - lx) / lw,
        "y": (by + float(crop["y"]) * scale - ly) / lh,
        "width": float(crop["width"]) * scale / lw,
        "height": float(crop["height"]) * scale / lh,
    }


def validate_normalised(norm: dict) -> dict:
    """Clean and range-check an editor rectangle; raises ValueError."""
    try:
        x, y = float(norm["x"]), float(norm["y"])
        w, h = float(norm["width"]), float(norm["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("crop needs numeric x, y, width, height") from exc
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        raise ValueError("crop values must be finite")
    if w < MIN_NORMALISED or h < MIN_NORMALISED:
        raise ValueError("crop is too small")
    eps = 1e-6
    if x < -eps or y < -eps or x + w > 1 + eps or y + h > 1 + eps:
        raise ValueError("crop must lie inside the field of view")
    x, y = max(0.0, x), max(0.0, y)
    return {"x": x, "y": y, "width": min(w, 1 - x), "height": min(h, 1 - y)}


def output_size_for_crop(sensor_rect: Rect, limits: Rect, mode_size: tuple[int, int],
                         base_size: tuple[int, int]) -> tuple[int, int]:
    """Recorded resolution for a crop (option (b) in the plan): the crop's
    own aspect ratio, about the same pixel count as the uncropped output
    (`base_size`), but never more pixels than the sensor mode actually
    reads for that region (no pointless upscaling) and never larger than
    the mode. Width aligned to WIDTH_ALIGN, height even."""
    _sx, _sy, sw, sh = sensor_rect
    _lx, _ly, lw, lh = limits
    mode_w, mode_h = mode_size
    aspect = sw / sh
    native_px = (sw * mode_w / lw) * (sh * mode_h / lh)
    target_px = min(base_size[0] * base_size[1], native_px)
    h = math.sqrt(target_px / aspect)
    w = h * aspect
    # Fit inside the mode, keeping the aspect.
    shrink = min(1.0, mode_w / w, mode_h / h)
    w, h = w * shrink, h * shrink
    out_w = max(MIN_OUTPUT_DIM, int(w) // WIDTH_ALIGN * WIDTH_ALIGN)
    out_h = max(MIN_OUTPUT_DIM, int(round(out_w / aspect)) // HEIGHT_ALIGN * HEIGHT_ALIGN)
    return out_w, min(out_h, mode_h // HEIGHT_ALIGN * HEIGHT_ALIGN)
