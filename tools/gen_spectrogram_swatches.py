#!/usr/bin/env python3
"""Generate the spectrogram colour-map swatch PNGs the Post-Process compose
panel shows next to the "Colour" picker.

The real render is done by ffmpeg's ``showspectrum`` filter and is exact;
these swatches only need to convey *how the colours look* and *how a plot
might look*, so they're rendered here with matplotlib colour maps chosen to
resemble each ffmpeg palette, applied to a small synthetic spectrogram
field. Output is committed, so this only needs re-running if the colour
list (``audio_align.SPEC_COLORS``) changes.

    python3 tools/gen_spectrogram_swatches.py

Writes:
    src/controller/frontend/src/assets/spectrogram-swatches/<name>.png
"""

from __future__ import annotations

import os

import numpy as np
from matplotlib import colormaps
from matplotlib.colors import LinearSegmentedColormap
from PIL import Image

# audio_align.SPEC_COLORS -> the closest matplotlib colour map. ffmpeg's
# palettes aren't all in matplotlib; where there's no equivalent the intent
# (hue progression) is matched, not the exact values.
FFMPEG_TO_MPL: dict[str, str] = {
    "intensity": "_intensity",   # custom, defined below
    "rainbow": "rainbow",
    "moreland": "coolwarm",
    "nebulae": "cubehelix",
    "fire": "hot",
    "fiery": "gist_heat",
    "fruit": "Spectral_r",
    "cool": "cool",
    "magma": "magma",
    "green": "Greens",
    "viridis": "viridis",
    "plasma": "plasma",
    "cividis": "cividis",
    "terrain": "terrain",
    "channel": "hsv",
}

_INTENSITY = LinearSegmentedColormap.from_list(
    "_intensity",
    ["#000000", "#00a000", "#e0e000", "#e00000", "#ffffff"],
)

OUT_DIR = os.path.join(
    os.path.dirname(__file__),
    "..", "src", "controller", "frontend", "src", "assets", "spectrogram-swatches",
)
W, H = 200, 56


def synthetic_field() -> np.ndarray:
    """A little spectrogram-shaped intensity field: a few frequency ridges
    that sweep across time, plus broadband floor noise."""
    t = np.linspace(0.0, 1.0, W)
    fq = np.linspace(0.0, 1.0, H)[:, None]
    field = np.zeros((H, W))
    for f0, f1, amp, width in [
        (0.18, 0.62, 1.00, 0.020),
        (0.55, 0.28, 0.75, 0.026),
        (0.80, 0.88, 0.55, 0.018),
        (0.35, 0.40, 0.45, 0.05),
    ]:
        center = f0 + (f1 - f0) * t
        field += amp * np.exp(-((fq - center) ** 2) / (2 * width**2))
    field += 0.06 * np.random.default_rng(0).random((H, W))
    field /= field.max()
    return field**0.72  # spread the mids so more of the map is exercised


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    field = synthetic_field()
    for name, mpl_name in FFMPEG_TO_MPL.items():
        cmap = _INTENSITY if mpl_name == "_intensity" else colormaps[mpl_name]
        rgb = cmap(field)[..., :3]
        img = (rgb[::-1] * 255).astype(np.uint8)  # low freq at the bottom
        path = os.path.join(OUT_DIR, f"{name}.png")
        # Palettise -- a smooth gradient needs nowhere near 24-bit colour and
        # this roughly halves the committed size.
        Image.fromarray(img).convert(
            "P", palette=Image.Palette.ADAPTIVE, colors=128
        ).save(path, optimize=True)
        print(f"wrote {os.path.relpath(path)}")


if __name__ == "__main__":
    main()
