"""
Tests for src/controller/recording_plans.py -- pure plan logic:
validation, per-plan window evaluation (with a fixed fake clock), and the
data-volume projection.
"""

from datetime import datetime

import pytest

from src.controller.recording_plans import (
    PlanError,
    RecordingPlan,
    current_anchor,
    estimate_campaign_volume,
    parse_plans,
    plan_duty_fraction,
    plan_should_record,
    plan_window_action,
)

MON, TUE, SUN = 0, 1, 6


def _at(day: int, hh: int, mm: int = 0) -> datetime:
    # 2026-06-01 is a Monday; add `day` to land on the wanted weekday.
    return datetime(2026, 6, 1 + day, hh, mm)


# --------------------------------------------------------------------------- #
# validation                                                                 #
# --------------------------------------------------------------------------- #


def test_parse_plans_ok():
    plans = parse_plans([
        {"plan_id": "cams", "label": "Cameras", "modules": ["c1", "c2"]},
        {"plan_id": "mics", "label": "Night audio", "modules": ["m1"],
         "strategy": "windows", "windows": [{"start": "20:00", "end": "06:00"}],
         "days": [MON, TUE]},
    ])
    assert [p.plan_id for p in plans] == ["cams", "mics"]
    assert plans[0].strategy == "continuous"


@pytest.mark.parametrize("bad", [
    [],
    [{"plan_id": "", "modules": ["a"]}],
    [{"plan_id": "x", "modules": []}],
    [{"plan_id": "a", "modules": ["m"]}, {"plan_id": "a", "modules": ["n"]}],
    [{"plan_id": "a", "modules": ["m"]}, {"plan_id": "b", "modules": ["m"]}],
    [{"plan_id": "a", "modules": ["m"], "strategy": "sometimes"}],
    [{"plan_id": "a", "modules": ["m"], "strategy": "windows", "windows": []}],
    [{"plan_id": "a", "modules": ["m"], "strategy": "windows",
      "windows": [{"start": "25:00", "end": "06:00"}]}],
    [{"plan_id": "a", "modules": ["m"], "strategy": "windows",
      "windows": [{"start": "06:00", "end": "06:00"}]}],
    [{"plan_id": "a", "modules": ["m"], "days": [9]}],
    [{"plan_id": "a", "modules": ["m"], "segment_minutes": 0}],
])
def test_parse_plans_rejects(bad):
    with pytest.raises(PlanError):
        parse_plans(bad)


# --------------------------------------------------------------------------- #
# window evaluation                                                           #
# --------------------------------------------------------------------------- #


def _plan(**kw):
    kw.setdefault("plan_id", "p")
    kw.setdefault("label", "p")
    kw.setdefault("modules", ["m"])
    return RecordingPlan(**kw)


def test_continuous_always_records_and_never_stops_itself():
    p = _plan(strategy="continuous")
    assert plan_should_record(p, _at(TUE, 3)) is True
    assert plan_window_action(p, _at(TUE, 3)) == "start"
    p.recording = True
    assert plan_window_action(p, _at(TUE, 3)) is None


def test_same_day_window():
    p = _plan(strategy="windows", windows=[{"start": "08:00", "end": "17:00"}])
    assert plan_window_action(p, _at(MON, 7, 59)) is None
    assert plan_window_action(p, _at(MON, 8, 0)) == "start"
    p.recording = True
    assert plan_window_action(p, _at(MON, 16, 59)) is None
    assert plan_window_action(p, _at(MON, 17, 0)) == "stop"


def test_window_crossing_midnight():
    p = _plan(strategy="windows", windows=[{"start": "20:00", "end": "06:00"}])
    assert plan_window_action(p, _at(MON, 19, 59)) is None
    assert plan_window_action(p, _at(MON, 20, 0)) == "start"
    p.recording = True
    assert plan_window_action(p, _at(TUE, 2, 0)) is None      # still the Mon-night run
    assert plan_window_action(p, _at(TUE, 6, 0)) == "stop"


def test_multiple_windows_per_day():
    p = _plan(strategy="windows", windows=[
        {"start": "20:00", "end": "23:59"},
        {"start": "04:00", "end": "06:00"},
    ])
    assert plan_window_action(p, _at(MON, 21, 0)) == "start"
    p.recording = True
    assert plan_window_action(p, _at(MON, 23, 59)) == "stop"   # gap between windows
    p.recording = False
    assert plan_window_action(p, _at(TUE, 4, 30)) == "start"


def test_days_filter():
    p = _plan(strategy="windows", days=[MON],
              windows=[{"start": "08:00", "end": "17:00"}])
    assert plan_window_action(p, _at(MON, 9)) == "start"
    assert plan_window_action(p, _at(TUE, 9)) is None


def test_days_filter_anchors_crossing_window_to_start_day():
    # window Mon 22:00 -> Tue 06:00 is allowed because it *began* on Monday
    p = _plan(strategy="windows", days=[MON],
              windows=[{"start": "22:00", "end": "06:00"}])
    assert plan_window_action(p, _at(MON, 23)) == "start"
    p.recording = True
    assert plan_should_record(p, _at(TUE, 2)) is True
    # a window that would begin Tuesday is not allowed
    p.recording = False
    p.last_start_date = None
    assert plan_window_action(p, _at(TUE, 23)) is None


def test_once_per_window_guard():
    p = _plan(strategy="windows", windows=[{"start": "08:00", "end": "17:00"}])
    assert plan_window_action(p, _at(MON, 9)) == "start"
    p.recording = True
    p.last_start_date = current_anchor(p, _at(MON, 9))
    # operator stops it mid-window -> not auto-restarted the same day
    p.recording = False
    assert plan_window_action(p, _at(MON, 12)) is None
    # next day it starts again
    assert plan_window_action(p, _at(TUE, 9)) == "start"


# --------------------------------------------------------------------------- #
# volume projection                                                          #
# --------------------------------------------------------------------------- #


def test_duty_fraction():
    assert plan_duty_fraction(_plan(strategy="continuous")) == 1.0
    night = _plan(strategy="windows", windows=[{"start": "20:00", "end": "06:00"}])
    assert plan_duty_fraction(night) == pytest.approx(10 / 24)
    night_weekdays = _plan(strategy="windows", days=[0, 1, 2, 3, 4],
                           windows=[{"start": "20:00", "end": "06:00"}])
    assert plan_duty_fraction(night_weekdays) == pytest.approx(10 / 24 * 5 / 7)


def test_estimate_campaign_volume():
    plans = parse_plans([
        {"plan_id": "cams", "label": "Cameras", "modules": ["c1", "c2"]},
        {"plan_id": "mics", "label": "Audio", "modules": ["m1"],
         "strategy": "windows", "windows": [{"start": "00:00", "end": "12:00"}]},
    ])
    rates = {"c1": 1_000_000, "c2": 1_000_000, "m1": 400_000}
    est = estimate_campaign_volume(plans, rates, expected_minutes=24 * 60)
    cams, mics = est["plans"]
    assert cams["projected_bytes"] == 2_000_000 * 86_400        # 2 cams, 24h, 100%
    assert mics["projected_bytes"] == 400_000 * 86_400 * 0.5    # 12h/day duty
    assert est["projected_bytes_total"] == (
        cams["projected_bytes"] + mics["projected_bytes"]
    )
