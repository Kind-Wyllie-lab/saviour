"""
Layout documents for the Post-Process compose editor
(plans/compose-layout-editor.md).

A layout says what the output video looks like: a canvas size and
background, and a list of tiles, each one data source (a camera, an
AudioMoth drawn as a spectrogram) placed at a rectangle in output pixels
with its own options. The same document drives the editor, the preview
and the render, so what the editor shows is what gets rendered.

    {
      "canvas": {"width": 1920, "height": 1080, "background": "#000000"},
      "tiles": [
        {"id": "t1", "kind": "camera", "source": "camera_d074",
         "rect": [0, 0, 1280, 720], "z": 0,
         "options": {"fit": "contain", "crop": null, "rotate": 0,
                     "label": true}},
        {"id": "t2", "kind": "spectrogram", "source": "mic/<device>",
         "rect": [0, 720, 1280, 180], "z": 0,
         "options": {"window_s": 20, "playhead": 0.5, "color": "intensity",
                     "fmin_hz": 15000, "fmax_hz": 96000, "gain": 2.5}}
      ],
      "audio_tracks": ["mic/<device>"]
    }

Camera sources are module folder names, mic sources are
`compose.list_mic_devices()` ids (`"<folder>/<device>"`). This module only
checks shape; whether a source exists in a given session is checked when a
preview or render resolves it. Pure (no OpenCV, no Flask) so it is cheap to
import and test.
"""

from __future__ import annotations

import copy
import re

from src.controller import audio_align

TILE_KINDS = ("camera", "spectrogram")
CAMERA_FITS = ("contain", "cover", "stretch")
ROTATIONS = (0, 90, 180, 270)
# Constants rather than UI limits, so a bigger rig (plans/
# habitat-post-processing.md) only has to raise them.
MAX_TILES = 16
MAX_AUDIO_TRACKS = 8
MIN_CANVAS = 64
MAX_CANVAS_W = 3840
# Taller than wide is allowed: the legacy `panel` audio mode stacks up to
# 300% of the video's height of spectrograms under it.
MAX_CANVAS_H = 4320
MIN_TILE = 8
SPECTROGRAM_WINDOW_RANGE_S = (0.5, 600.0)

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_SPEC_FIELDS = ("color", "fmin_hz", "fmax_hz", "fscale", "ascale", "gain")


class LayoutError(ValueError):
    """A malformed layout document -- surfaced to the client verbatim."""


def _even(value: float) -> int:
    return max(2, int(round(value / 2)) * 2)


def _safe_segment(text: str) -> bool:
    return bool(text) and "/" not in text and "\\" not in text and ".." not in text


def _int(value, what: str) -> int:
    if isinstance(value, bool):
        raise LayoutError(f"{what} must be a number")
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise LayoutError(f"{what} must be a number") from exc
    if f != f or f in (float("inf"), float("-inf")):
        raise LayoutError(f"{what} must be a number")
    return int(round(f))


def _float(value, what: str) -> float:
    if isinstance(value, bool):
        raise LayoutError(f"{what} must be a number")
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise LayoutError(f"{what} must be a number") from exc
    if f != f or f in (float("inf"), float("-inf")):
        raise LayoutError(f"{what} must be a number")
    return f


def hex_to_bgr(colour: str) -> tuple[int, int, int]:
    """`"#rrggbb"` -> an OpenCV (b, g, r) tuple."""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return b, g, r


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def validate_canvas(raw) -> dict:
    if not isinstance(raw, dict):
        raise LayoutError("canvas must be an object")
    width = _int(raw.get("width"), "canvas.width")
    height = _int(raw.get("height"), "canvas.height")
    if not MIN_CANVAS <= width <= MAX_CANVAS_W:
        raise LayoutError(f"canvas.width must be between {MIN_CANVAS} and {MAX_CANVAS_W}")
    if not MIN_CANVAS <= height <= MAX_CANVAS_H:
        raise LayoutError(f"canvas.height must be between {MIN_CANVAS} and {MAX_CANVAS_H}")
    if width % 2 or height % 2:
        raise LayoutError("canvas width and height must be even")
    background = str(raw.get("background") or "#000000")
    if not _HEX.match(background):
        raise LayoutError("canvas.background must be a #rrggbb colour")
    return {"width": width, "height": height, "background": background.lower()}


def _validate_mic_id(sid, what: str) -> str:
    folder, _, device = str(sid).partition("/")
    if not (_safe_segment(folder) and device and _safe_segment(device)):
        raise LayoutError(f"invalid {what} {sid!r}")
    return f"{folder}/{device}"


def _validate_camera_options(raw: dict) -> dict:
    fit = str(raw.get("fit", "contain")).lower()
    if fit not in CAMERA_FITS:
        raise LayoutError(f"camera fit must be one of {', '.join(CAMERA_FITS)}")
    rotate = _int(raw.get("rotate", 0), "camera rotate")
    if rotate not in ROTATIONS:
        raise LayoutError("camera rotate must be 0, 90, 180 or 270")
    crop = raw.get("crop")
    if crop is not None:
        if not isinstance(crop, (list, tuple)) or len(crop) != 4:
            raise LayoutError("camera crop must be [x, y, w, h] fractions or null")
        cx, cy, cw, ch = (_float(v, "camera crop") for v in crop)
        if not (0 <= cx < 1 and 0 <= cy < 1 and 0 < cw <= 1 and 0 < ch <= 1
                and cx + cw <= 1.0001 and cy + ch <= 1.0001):
            raise LayoutError("camera crop must lie inside the frame (fractions 0-1)")
        crop = [round(cx, 5), round(cy, 5), round(min(cw, 1 - cx), 5),
                round(min(ch, 1 - cy), 5)]
    return {"fit": fit, "crop": crop, "rotate": rotate,
            "label": bool(raw.get("label", True))}


def _validate_spectrogram_options(raw: dict) -> dict:
    spec = {k: raw[k] for k in _SPEC_FIELDS if raw.get(k) is not None}
    try:
        opts = audio_align.SpectrogramOpts(**spec)
    except (TypeError, ValueError) as exc:
        raise LayoutError(str(exc)) from exc
    window_s = raw.get("window_s")
    if window_s is not None:
        window_s = _float(window_s, "spectrogram window_s")
        lo, hi = SPECTROGRAM_WINDOW_RANGE_S
        if not lo <= window_s <= hi:
            raise LayoutError(f"spectrogram window_s must be between {lo:g} and {hi:g} s")
    playhead = _float(raw.get("playhead", 0.5), "spectrogram playhead")
    if not 0 <= playhead <= 1:
        raise LayoutError("spectrogram playhead must be between 0 and 1")
    return {
        "color": opts.color, "fmin_hz": opts.fmin_hz, "fmax_hz": opts.fmax_hz,
        "fscale": opts.fscale, "ascale": opts.ascale, "gain": opts.gain,
        "window_s": window_s, "playhead": playhead,
        "label": bool(raw.get("label", False)),
    }


def _validate_tile(raw, canvas: dict, index: int) -> dict:
    if not isinstance(raw, dict):
        raise LayoutError("each tile must be an object")
    kind = str(raw.get("kind", "")).lower()
    if kind not in TILE_KINDS:
        raise LayoutError(f"tile kind must be one of {', '.join(TILE_KINDS)}")
    tile_id = str(raw.get("id") or f"t{index + 1}")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", tile_id):
        raise LayoutError(f"invalid tile id {tile_id!r}")
    source = str(raw.get("source") or "")
    if kind == "camera":
        if not _safe_segment(source):
            raise LayoutError(f"invalid camera source {source!r}")
    else:
        source = _validate_mic_id(source, "spectrogram source")
    rect = raw.get("rect")
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        raise LayoutError(f"tile {tile_id}: rect must be [x, y, w, h]")
    x, y, w, h = (_int(v, f"tile {tile_id} rect") for v in rect)
    if w < MIN_TILE or h < MIN_TILE:
        raise LayoutError(f"tile {tile_id} is smaller than {MIN_TILE} px")
    if x < 0 or y < 0 or x + w > canvas["width"] or y + h > canvas["height"]:
        raise LayoutError(f"tile {tile_id} lies outside the canvas")
    options = raw.get("options") or {}
    if not isinstance(options, dict):
        raise LayoutError(f"tile {tile_id}: options must be an object")
    options = (_validate_camera_options(options) if kind == "camera"
               else _validate_spectrogram_options(options))
    return {"id": tile_id, "kind": kind, "source": source,
            "rect": [x, y, w, h], "z": _int(raw.get("z", 0), "tile z"),
            "options": options}


def validate_layout(raw) -> dict:
    """A normalised copy of a layout document, or LayoutError. Fills in
    option defaults, so a stored document always carries every field."""
    if not isinstance(raw, dict):
        raise LayoutError("layout must be an object")
    canvas = validate_canvas(raw.get("canvas"))
    tiles_raw = raw.get("tiles")
    if not isinstance(tiles_raw, list) or not tiles_raw:
        raise LayoutError("layout needs at least one tile")
    if len(tiles_raw) > MAX_TILES:
        raise LayoutError(f"a layout has at most {MAX_TILES} tiles")
    tiles = [_validate_tile(t, canvas, i) for i, t in enumerate(tiles_raw)]
    ids = [t["id"] for t in tiles]
    if len(set(ids)) != len(ids):
        raise LayoutError("tile ids must be unique")
    if not any(t["kind"] == "camera" for t in tiles):
        # The output's time window is the cameras' overlap; with no camera
        # there's nothing to define it (audio-only renders: not yet).
        raise LayoutError("a layout needs at least one camera tile")
    tracks_raw = raw.get("audio_tracks") or []
    if not isinstance(tracks_raw, list) or len(tracks_raw) > MAX_AUDIO_TRACKS:
        raise LayoutError(f"audio_tracks must be a list of at most {MAX_AUDIO_TRACKS}")
    tracks = []
    for sid in tracks_raw:
        sid = _validate_mic_id(sid, "audio track")
        if sid not in tracks:
            tracks.append(sid)
    return {"canvas": canvas, "tiles": tiles, "audio_tracks": tracks}


# --------------------------------------------------------------------------- #
# Queries + geometry                                                          #
# --------------------------------------------------------------------------- #


def camera_sources(doc: dict) -> list[str]:
    """Distinct camera sources, in tile order (one decoder each, however
    many tiles show it)."""
    out: list[str] = []
    for t in doc["tiles"]:
        if t["kind"] == "camera" and t["source"] not in out:
            out.append(t["source"])
    return out


def mic_sources(doc: dict) -> list[str]:
    """Distinct AudioMoths the layout needs aligned: every spectrogram
    tile's source, then every audio track."""
    out: list[str] = []
    for t in doc["tiles"]:
        if t["kind"] == "spectrogram" and t["source"] not in out:
            out.append(t["source"])
    for sid in doc["audio_tracks"]:
        if sid not in out:
            out.append(sid)
    return out


def draw_order(tiles: list[dict]) -> list[dict]:
    """Tiles bottom to top: by z, then by position in the list."""
    return [t for _i, t in sorted(enumerate(tiles), key=lambda p: (p[1]["z"], p[0]))]


def fit_to_canvas(doc: dict, width: int, height: int) -> dict:
    """`doc` scaled uniformly to fit inside a `width` x `height` canvas and
    centred on it -- how a built-in template is applied to a canvas size
    the user picked. Rects stay inside the new canvas."""
    width, height = _even(width), _even(height)
    cw, ch = doc["canvas"]["width"], doc["canvas"]["height"]
    scale = min(width / cw, height / ch)
    ox = (width - cw * scale) / 2
    oy = (height - ch * scale) / 2
    out = copy.deepcopy(doc)
    out["canvas"]["width"], out["canvas"]["height"] = width, height
    for t in out["tiles"]:
        x, y, w, h = t["rect"]
        nx, ny = int(round(ox + x * scale)), int(round(oy + y * scale))
        nw = max(MIN_TILE, min(int(round(w * scale)), width - nx))
        nh = max(MIN_TILE, min(int(round(h * scale)), height - ny))
        t["rect"] = [nx, ny, nw, nh]
    return out


def scaled(doc: dict, max_width: int) -> tuple[dict, float]:
    """`doc` at most `max_width` wide (for a preview), and the scale used.
    Rect edges are scaled (not sizes), so tiles that touch still touch."""
    cw, ch = doc["canvas"]["width"], doc["canvas"]["height"]
    scale = min(1.0, max_width / cw)
    if scale >= 1.0:
        return copy.deepcopy(doc), 1.0
    out = copy.deepcopy(doc)
    out["canvas"]["width"] = max(2, int(round(cw * scale)))
    out["canvas"]["height"] = max(2, int(round(ch * scale)))
    for t in out["tiles"]:
        x, y, w, h = t["rect"]
        x0, y0 = int(round(x * scale)), int(round(y * scale))
        x1 = min(out["canvas"]["width"], int(round((x + w) * scale)))
        y1 = min(out["canvas"]["height"], int(round((y + h) * scale)))
        t["rect"] = [x0, y0, max(1, x1 - x0), max(1, y1 - y0)]
    return out, scale


def spectrogram_opts(tile: dict) -> audio_align.SpectrogramOpts:
    o = tile["options"]
    return audio_align.SpectrogramOpts(**{k: o[k] for k in _SPEC_FIELDS if k in o})


def spectrogram_strip(tile_w: int, window_s: float | None,
                      total_s: float) -> tuple[int, float]:
    """`(strip_width_px, strip_pps)` for one spectrogram tile: the whole
    window's spectrogram as one wide image the compositor pans across.
    `window_s` None = the legacy scroll speed (audio_align.PLAYHEAD_PPS,
    one strip pixel per tile pixel); otherwise enough pixels for the tile
    to show `window_s` seconds at full resolution. Capped by
    audio_align._MAX_STRIP_PX either way (a long window just gets coarser
    in time, never wrong)."""
    if window_s is None:
        return audio_align._strip_pixels_per_second(total_s)
    total_s = max(total_s, 0.1)
    want = total_s * tile_w / window_s
    width = max(2, int(min(want, audio_align._MAX_STRIP_PX)) // 2 * 2)
    return width, width / total_s


def window_px(tile_w: int, window_s: float | None, strip_pps: float) -> int:
    """How many strip pixels a tile shows at once (scaled to the tile's
    width when drawn)."""
    if window_s is None:
        return tile_w
    return max(2, int(round(window_s * strip_pps)))
