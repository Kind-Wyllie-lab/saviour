"""
Share fill-up forecast for the Storage page: how many GB/day the share is
filling at, and when it will be full (and when it will cross the
``recording.nas_min_free_pct`` floor that blocks new sessions) if that rate
holds.

Input is the controller's free-space history, one ``(epoch_s, free_bytes,
total_bytes)`` sample per NAS check (default every 5 min, kept 30 days).

The rate counts only *drops* in free space between consecutive samples. A
least-squares slope of free space (the page's old method) reads one session
deletion or archive clean-up as "free space increasing" for days afterwards;
summing drops ignores the jump up and keeps measuring what recording writes.
Divided by calendar time (not time spent recording), so a rig that records a
few hours a day gets its real average.
"""

from __future__ import annotations

from itertools import pairwise

GiB = 1024 ** 3

DEFAULT_WINDOW_S = 7 * 86400
# Less history than this gives no forecast: a few hours of a habitat's
# day/night cycle says little about a week.
MIN_SPAN_S = 6 * 3600
MIN_SAMPLES = 4
# Below this the share isn't meaningfully filling (rounding / log noise).
MIN_RATE_GB_PER_DAY = 0.05


def fill_forecast(samples, now: float, *, min_free_pct: float | None = None,
                  window_s: float = DEFAULT_WINDOW_S) -> dict:
    """Forecast from ``samples`` (oldest first). Always returns a dict with a
    ``state``:

    * ``insufficient`` -- under ``MIN_SPAN_S`` / ``MIN_SAMPLES`` of history,
    * ``not_filling`` -- rate below ``MIN_RATE_GB_PER_DAY``,
    * ``filling`` -- with ``days_to_full`` / ``full_at`` and, given
      ``min_free_pct``, ``days_to_min`` / ``min_at`` (0 if already below).

    GB are GiB, matching ``free_gb`` elsewhere on the page.
    """
    pts = [(float(t), int(f), int(tot)) for t, f, tot in samples
           if tot and t >= now - window_s]
    # Only the run of samples on the current share size: a resized share or
    # a switched export destination makes earlier free-space values
    # incomparable.
    if pts:
        total = pts[-1][2]
        i = len(pts)
        while i > 0 and pts[i - 1][2] == total:
            i -= 1
        pts = pts[i:]

    out = {"state": "insufficient", "window_days": None, "gb_per_day": None,
           "days_to_full": None, "full_at": None,
           "days_to_min": None, "min_at": None}
    if len(pts) < MIN_SAMPLES:
        return out
    span_s = pts[-1][0] - pts[0][0]
    if span_s < MIN_SPAN_S:
        return out

    written = sum(max(0, a[1] - b[1]) for a, b in pairwise(pts))
    gb_per_day = written / GiB / (span_s / 86400)
    out["window_days"] = round(span_s / 86400, 1)
    out["gb_per_day"] = round(gb_per_day, 2)
    if gb_per_day < MIN_RATE_GB_PER_DAY:
        out["state"] = "not_filling"
        return out

    out["state"] = "filling"
    _, free, total = pts[-1]
    last_t = pts[-1][0]
    bytes_per_s = gb_per_day * GiB / 86400
    to_full_s = free / bytes_per_s
    out["days_to_full"] = round(to_full_s / 86400, 1)
    out["full_at"] = round(last_t + to_full_s)
    if min_free_pct is not None:
        to_min_s = max(0.0, (free - total * min_free_pct / 100) / bytes_per_s)
        out["days_to_min"] = round(to_min_s / 86400, 1)
        out["min_at"] = round(last_t + to_min_s)
    return out
