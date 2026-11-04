"""Tests for src/controller/storage_forecast.py (Storage page fill forecast)."""

import pytest

from src.controller.storage_forecast import GiB, fill_forecast

NOW = 1_800_000_000.0
TOTAL = 10_000 * GiB
STEP = 300  # one sample per 5 min, as _nas_monitor_loop takes them


def _series(days, gb_per_day, free_now=4_000 * GiB, total=TOTAL, end=NOW):
    """Samples ending at `end`, free space falling linearly at gb_per_day."""
    n = int(days * 86400 / STEP) + 1
    rate = gb_per_day * GiB / 86400
    return [(end - (n - 1 - i) * STEP,
             int(free_now + (n - 1 - i) * STEP * rate), total)
            for i in range(n)]


def test_steady_fill_gives_rate_and_dates():
    f = fill_forecast(_series(7, 100), NOW, min_free_pct=5)
    assert f["state"] == "filling"
    assert f["gb_per_day"] == pytest.approx(100, rel=1e-3)
    assert f["window_days"] == 7.0
    assert f["days_to_full"] == pytest.approx(40, abs=0.1)
    assert f["full_at"] == pytest.approx(NOW + 40 * 86400, abs=3600)
    # 5% of 10 000 GB = 500 GB floor -> (4000 - 500) / 100 = 35 days
    assert f["days_to_min"] == pytest.approx(35, abs=0.1)


def test_deletion_jump_does_not_hide_the_fill():
    """A clean-up that frees 2 TB mid-window would make a least-squares
    slope read as 'free space increasing'; drops-only keeps the real rate."""
    s = _series(7, 100)
    mid = len(s) // 2
    s = [(t, f + (2_000 * GiB if i >= mid else 0), tot)
         for i, (t, f, tot) in enumerate(s)]
    f = fill_forecast(s, NOW)
    assert f["state"] == "filling"
    assert f["gb_per_day"] == pytest.approx(100, rel=1e-2)


def test_window_is_last_seven_days():
    old_fast = _series(20, 500, end=NOW - 8 * 86400, free_now=6_000 * GiB)
    recent = _series(7, 50)
    f = fill_forecast(old_fast + recent, NOW)
    assert f["gb_per_day"] == pytest.approx(50, rel=1e-2)


def test_share_resize_restarts_the_window():
    before = _series(5, 300, end=NOW - 2 * 86400, total=5_000 * GiB,
                     free_now=1_000 * GiB)
    after = _series(2, 80)
    f = fill_forecast(before + after, NOW)
    assert f["gb_per_day"] == pytest.approx(80, rel=1e-2)
    assert f["window_days"] == 2.0


def test_not_enough_history():
    assert fill_forecast([], NOW)["state"] == "insufficient"
    assert fill_forecast(_series(0.1, 100), NOW)["state"] == "insufficient"


def test_idle_share_is_not_filling():
    f = fill_forecast(_series(7, 0), NOW, min_free_pct=5)
    assert f["state"] == "not_filling"
    assert f["gb_per_day"] == 0
    assert f["full_at"] is None and f["min_at"] is None


def test_already_below_floor_is_zero_days():
    f = fill_forecast(_series(7, 100, free_now=300 * GiB), NOW, min_free_pct=5)
    assert f["days_to_min"] == 0
    assert f["min_at"] == pytest.approx(NOW, abs=1)
