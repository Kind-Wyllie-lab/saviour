"""
Recording plans -- the scheduling primitives behind a "Habitat Session".

A Habitat Session is one `RecordingSession` that carries a list of
`RecordingPlan`s instead of a single start/stop policy: e.g. every camera
recording continuously, hour by hour, while the microphones only run in
nightly windows. Each plan drives its own subset of the session's
modules on its own clock; the session as a whole can be paused and
resumed.

This module is the pure part -- the plan dataclass, per-plan window
evaluation, validation, and the up-front data-volume projection. All the
stateful work (issuing start/stop to modules, persistence, the monitor
loop) lives in recording.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

STRATEGIES = ("continuous", "windows")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SECONDS_PER_DAY = 86_400


class PlanError(ValueError):
    """A malformed plan set -- surfaced to the client verbatim."""


@dataclass
class RecordingPlan:
    plan_id: str
    label: str
    modules: list = field(default_factory=list)
    # "continuous" -> always recording while the session is ACTIVE.
    # "windows"    -> recording only inside one of `windows` on `days`.
    strategy: str = "continuous"
    windows: list = field(default_factory=list)   # [{"start": "HH:MM", "end": "HH:MM"}]
    days: list = field(default_factory=list)       # [0..6] Mon..Sun; empty = every day
    segment_minutes: int | None = None            # None -> module/config default
    # runtime state (persisted with the session)
    recording: bool = False
    last_start_date: str | None = None            # anchor date of the last window entry

    @classmethod
    def from_dict(cls, d: dict) -> RecordingPlan:
        if not isinstance(d, dict):
            raise PlanError("each plan must be an object")
        return cls(
            plan_id=str(d.get("plan_id") or ""),
            label=str(d.get("label") or ""),
            modules=list(d.get("modules") or []),
            strategy=str(d.get("strategy") or "continuous"),
            windows=[dict(w) for w in (d.get("windows") or [])],
            days=[int(x) for x in (d.get("days") or [])],
            segment_minutes=(
                int(d["segment_minutes"])
                if d.get("segment_minutes") not in (None, "")
                else None
            ),
            recording=bool(d.get("recording", False)),
            last_start_date=d.get("last_start_date") or None,
        )


def parse_plans(raw: list) -> list[RecordingPlan]:
    if not isinstance(raw, list) or not raw:
        raise PlanError("a Habitat Session needs at least one plan")
    plans = [RecordingPlan.from_dict(p) for p in raw]
    validate_plans(plans)
    return plans


def validate_plans(plans: list[RecordingPlan]) -> None:
    seen_ids: set[str] = set()
    seen_modules: set[str] = set()
    for p in plans:
        if not p.plan_id or p.plan_id in seen_ids:
            raise PlanError("every plan needs a unique plan_id")
        seen_ids.add(p.plan_id)
        if not p.modules:
            raise PlanError(f"plan {p.plan_id!r} has no modules")
        clash = seen_modules & set(p.modules)
        if clash:
            raise PlanError(
                f"module(s) in more than one plan: {', '.join(sorted(clash))}"
            )
        seen_modules |= set(p.modules)
        if p.strategy not in STRATEGIES:
            raise PlanError(f"plan {p.plan_id!r}: strategy must be one of {STRATEGIES}")
        if any(not (0 <= int(x) <= 6) for x in p.days):
            raise PlanError(f"plan {p.plan_id!r}: days must be 0..6 (Mon..Sun)")
        if p.segment_minutes is not None and not 1 <= p.segment_minutes <= 1440:
            raise PlanError(f"plan {p.plan_id!r}: segment_minutes must be 1..1440")
        if p.strategy == "windows":
            if not p.windows:
                raise PlanError(f"plan {p.plan_id!r}: windows strategy needs a window")
            for w in p.windows:
                s, e = w.get("start", ""), w.get("end", "")
                if not (_HHMM.match(s) and _HHMM.match(e)):
                    raise PlanError(
                        f"plan {p.plan_id!r}: window times must be HH:MM ({s!r}-{e!r})"
                    )
                if s == e:
                    raise PlanError(f"plan {p.plan_id!r}: window start == end ({s})")


# --------------------------------------------------------------------------- #
# Per-plan window evaluation                                                  #
# --------------------------------------------------------------------------- #


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _window_anchor(w: dict, now: datetime) -> date | None:
    """The date the *current* occurrence of window `w` began, or None if
    `now` is outside it. A start > end window spans midnight."""
    start, end = _mins(w["start"]), _mins(w["end"])
    cur = now.hour * 60 + now.minute
    if start < end:
        return now.date() if start <= cur < end else None
    # crosses midnight
    if cur >= start:
        return now.date()
    if cur < end:
        return now.date() - timedelta(days=1)
    return None


def _day_allowed(days: list, d: date) -> bool:
    return not days or d.weekday() in days


def plan_should_record(plan: RecordingPlan, now: datetime) -> bool:
    """Should this plan's modules be recording at wall-clock `now`?"""
    if plan.strategy == "continuous":
        return True
    for w in plan.windows:
        anchor = _window_anchor(w, now)
        if anchor is not None and _day_allowed(plan.days, anchor):
            return True
    return False


def plan_window_action(plan: RecordingPlan, now: datetime) -> str | None:
    """`"start"`, `"stop"`, or `None` for one monitor tick.

    `"start"` is withheld if this plan already entered the current window
    once (`last_start_date` == the window's anchor date) and was then
    stopped -- so a manual stop inside a window isn't immediately undone.
    The caller sets `plan.last_start_date` to `current_anchor(plan, now)`
    when it acts on a `"start"`.
    """
    should = plan_should_record(plan, now)
    if should and not plan.recording:
        anchor = current_anchor(plan, now)
        if anchor is not None and plan.last_start_date == anchor:
            return None
        return "start"
    if plan.recording and not should:
        return "stop"
    return None


def current_anchor(plan: RecordingPlan, now: datetime) -> str | None:
    """ISO date the plan's active window began (continuous -> today)."""
    if plan.strategy == "continuous":
        return now.date().isoformat()
    for w in plan.windows:
        anchor = _window_anchor(w, now)
        if anchor is not None and _day_allowed(plan.days, anchor):
            return anchor.isoformat()
    return None


# --------------------------------------------------------------------------- #
# Up-front data-volume projection                                            #
# --------------------------------------------------------------------------- #


def _window_seconds_per_day(plan: RecordingPlan) -> int:
    total = 0
    for w in plan.windows:
        s, e = _mins(w["start"]), _mins(w["end"])
        span = (e - s) if s < e else (1440 - s + e)
        total += span * 60
    return min(total, _SECONDS_PER_DAY)


def plan_duty_fraction(plan: RecordingPlan) -> float:
    """Fraction of wall-clock time this plan spends recording."""
    if plan.strategy == "continuous":
        return 1.0
    day_frac = _window_seconds_per_day(plan) / _SECONDS_PER_DAY
    week_frac = (len(plan.days) / 7) if plan.days else 1.0
    return max(0.0, min(1.0, day_frac * week_frac))


def estimate_campaign_volume(
    plans: list[RecordingPlan],
    module_bytes_per_s: dict[str, float],
    expected_minutes: float,
) -> dict:
    """Projected bytes each plan (and the whole session) writes over
    `expected_minutes`, given each module's recording byte rate. Disk
    headroom is checked by the caller, which has the free-space figures."""
    seconds = max(0.0, expected_minutes) * 60
    per_plan = []
    total = 0.0
    for p in plans:
        rate = sum(float(module_bytes_per_s.get(m, 0.0)) for m in p.modules)
        duty = plan_duty_fraction(p)
        planned = rate * seconds * duty
        total += planned
        per_plan.append({
            "plan_id": p.plan_id,
            "label": p.label,
            "modules": list(p.modules),
            "bytes_per_s_recording": round(rate, 1),
            "duty_fraction": round(duty, 4),
            "projected_bytes": round(planned),
        })
    return {
        "expected_minutes": expected_minutes,
        "plans": per_plan,
        "projected_bytes_total": round(total),
    }
