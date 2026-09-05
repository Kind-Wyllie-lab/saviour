#!/usr/bin/env python3
"""Generate the colour-map swatch PNGs the AudioMoth config card's Monitor
tab shows next to the "Spectrogram colour" picker.

The real render is done on the microphone module with OpenCV's
``cv2.applyColorMap`` (see ``_SPEC_COLORMAPS`` in
``src/modules/variants/microphone/microphone_module.py``). Rendering here
uses matplotlib colour maps of the same name -- OpenCV's built-in maps are
LUT reproductions of these same reference palettes, so the match is exact
for every entry except ``grayscale`` (OpenCV: passthrough grey; matplotlib
equivalent: ``gray``). Output is committed, so this only needs re-running
if ``_SPEC_COLORMAPS`` changes.

    python3 tools/gen_microphone_colormap_swatches.py

Writes:
    src/controller/frontend/src/assets/mic-colormap-swatches/<name>.png
"""

from __future__ import annotations

import os

import numpy as np
from matplotlib import colormaps
from PIL import Image

# microphone_module._SPEC_COLORMAPS key -> matplotlib colour map name.
COLORMAPS: dict[str, str] = {
    "inferno": "inferno",
    "magma": "magma",
    "plasma": "plasma",
    "viridis": "viridis",
    "turbo": "turbo",
    "jet": "jet",
    "hot": "hot",
    "bone": "bone",
    "ocean": "ocean",
    "grayscale": "gray",
}

OUT_DIR = os.path.join(
    os.path.dirname(__file__),
    "..", "src", "controller", "frontend", "src", "assets", "mic-colormap-swatches",
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
    for name, mpl_name in COLORMAPS.items():
        cmap = colormaps[mpl_name]
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
