"""Camera exposure check: is the *subject* well exposed?

The old check warned whenever more than exposure_warn_pct of the frame was
clipped white (or crushed black). In a white arena with a dark rat that's the
right exposure: the background sits at 255 and the rat spreads over the rest
of the range. The warning told users to darken the image (low contrast,
aperture closed), which squeezed the rat into a narrow band of greys.

So clipping alone is no longer a problem. It is one only when nothing is
left outside it:

- over: >= warn_pct clipped white AND the darkest 1 % of the frame is still
  above mid-grey (no dark detail anywhere: the subject is washed out too).
- under: >= warn_pct crushed black AND the brightest 1 % is below mid-grey.
- low_contrast: the frame's 1st to 99th percentile spans fewer than
  LOW_CONTRAST_SPAN grey levels (everything squeezed into a narrow band).

``assess`` also returns a 64-bin histogram for the camera card.
"""

from __future__ import annotations

import numpy as np

MID_GREY = 128
LOW_CONTRAST_SPAN = 80   # grey levels between the 1st and 99th percentile
HIST_BINS = 64


def assess(luma: np.ndarray, *, hi: int = 250, lo: int = 5,
           warn_pct: float = 5.0) -> dict:
    """Exposure figures for an 8-bit luma (sub)sample.

    Returns {over_pct, under_pct, p1, p99, span, verdict, hist} with verdict
    one of "ok", "over", "under", "low_contrast"."""
    v = np.asarray(luma).ravel()
    n = v.size or 1
    over = 100.0 * int(np.count_nonzero(v >= hi)) / n
    under = 100.0 * int(np.count_nonzero(v <= lo)) / n
    if v.size:
        p1, p99 = (float(x) for x in np.percentile(v, (1, 99)))
    else:
        p1 = p99 = 0.0
    span = p99 - p1
    verdict = "ok"
    if over >= warn_pct and p1 > MID_GREY:
        verdict = "over"
    elif under >= warn_pct and p99 < MID_GREY:
        verdict = "under"
    elif v.size and span < LOW_CONTRAST_SPAN:
        verdict = "low_contrast"
    hist = np.bincount(np.clip(v.astype(np.int64), 0, 255) * HIST_BINS // 256,
                       minlength=HIST_BINS)[:HIST_BINS]
    return {"over_pct": over, "under_pct": under, "p1": p1, "p99": p99,
            "span": span, "verdict": verdict, "hist": hist.tolist()}


def verdict_of(samples: list[dict], warn_pct: float = 5.0) -> dict:
    """Roll several assess() results (oldest first) into one: mean clip
    figures, and a verdict only when every sample agrees (so a rat walking
    across a dark corner doesn't flicker a warning)."""
    if not samples:
        return {"over_pct": 0.0, "under_pct": 0.0, "verdict": "ok", "span": 0.0}
    k = len(samples)
    verdicts = {s["verdict"] for s in samples}
    return {
        "over_pct": sum(s["over_pct"] for s in samples) / k,
        "under_pct": sum(s["under_pct"] for s in samples) / k,
        "span": sum(s["span"] for s in samples) / k,
        "verdict": verdicts.pop() if len(verdicts) == 1 else "ok",
    }
