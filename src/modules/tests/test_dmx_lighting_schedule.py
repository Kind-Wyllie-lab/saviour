"""Schedule maths for the DMX lighting module (variants/dmx_lighting/schedule.py)."""

import copy
import itertools
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.modules.variants.dmx_lighting import schedule as sm

UTC = ZoneInfo("UTC")
LONDON = ZoneInfo("Europe/London")


def cfg(**over):
    c = {
        "timezone": "UTC",
        "schedule": {"lights_on": "07:00", "lights_off": "19:00",
                     "sunrise_minutes": 30, "sunset_minutes": 30,
                     "curve": "linear", "day_level": 100, "night_level": 0},
        "ir": {"mode": "constant", "level": 80},
        "channel_bits": 8,
        "compartments": [{"box": "A1", "visible": 1, "ir": 2},
                         {"box": "A2", "visible": 3, "ir": 4, "trim": 50}],
        "slew_full_scale_s": 60,
        "max_override_minutes": 240,
    }
    for k, v in over.items():
        if k in ("schedule", "ir"):
            c[k].update(v)
        else:
            c[k] = v
    return c


def at(hhmm: str, tz=UTC, day=(2026, 11, 11)) -> float:
    h, m = map(int, hhmm.split(":"))
    return datetime(*day, h, m, tzinfo=tz).timestamp()


class TestDailyCycle:
    @pytest.mark.parametrize("t,phase,visible", [
        ("06:59", "night", 0), ("07:00", "sunrise", 0), ("07:15", "sunrise", 50),
        ("07:30", "day", 100), ("12:00", "day", 100), ("19:00", "sunset", 100),
        ("19:15", "sunset", 50), ("19:30", "night", 0), ("03:00", "night", 0),
    ])
    def test_linear(self, t, phase, visible):
        st = sm.schedule_at(at(t), cfg())
        assert st.phase == phase
        assert st.visible == pytest.approx(visible, abs=1e-6)

    def test_reversed_cycle_crosses_midnight(self):
        c = cfg(schedule={"lights_on": "19:00", "lights_off": "07:00"})
        assert sm.schedule_at(at("23:00"), c).phase == "day"
        assert sm.schedule_at(at("02:00"), c).phase == "day"
        assert sm.schedule_at(at("12:00"), c).phase == "night"
        st = sm.schedule_at(at("07:15"), c)
        assert st.phase == "sunset" and st.visible == pytest.approx(50)

    def test_zero_length_ramps_are_steps(self):
        c = cfg(schedule={"sunrise_minutes": 0, "sunset_minutes": 0})
        assert sm.schedule_at(at("06:59"), c).visible == 0
        assert sm.schedule_at(at("07:00"), c).visible == 100
        assert sm.schedule_at(at("19:00"), c).visible == 0

    @pytest.mark.parametrize("curve", sm.CURVES)
    def test_curves_hit_the_endpoints_and_rise_monotonically(self, curve):
        c = cfg(schedule={"curve": curve, "night_level": 5, "day_level": 80})
        assert sm.schedule_at(at("07:00"), c).visible == pytest.approx(5, abs=1e-6)
        assert sm.schedule_at(at("07:30"), c).visible == pytest.approx(80, abs=1e-6)
        vals = [sm.schedule_at(at("07:00") + k * 60, c).visible for k in range(31)]
        assert all(b >= a - 1e-9 for a, b in itertools.pairwise(vals))

    def test_perceptual_starts_gently(self):
        """Perceptual: the first minutes from dark stay dim in output terms
        (a linear ramp would already be at 3.3 % after a minute)."""
        c = cfg(schedule={"curve": "perceptual"})
        assert sm.schedule_at(at("07:01"), c).visible < 0.1
        mid = sm.schedule_at(at("07:15"), c).visible
        assert 10 < mid < 30          # L* 50 is ~18 % luminance

    def test_next_change(self):
        st = sm.schedule_at(at("12:00"), cfg())
        assert st.next_change == "sunset" and st.next_change_at == at("19:00")
        st = sm.schedule_at(at("19:10"), cfg())
        assert st.next_change == "night" and st.next_change_at == at("19:30")
        st = sm.schedule_at(at("22:00"), cfg())
        assert st.next_change == "sunrise"
        assert st.next_change_at == at("07:00", day=(2026, 11, 12))


class TestIR:
    def test_constant(self):
        assert sm.schedule_at(at("12:00"), cfg()).ir == 80
        assert sm.schedule_at(at("02:00"), cfg()).ir == 80

    def test_night_only_mirrors_visible(self):
        c = cfg(ir={"mode": "night_only"})
        assert sm.schedule_at(at("12:00"), c).ir == pytest.approx(0)
        assert sm.schedule_at(at("02:00"), c).ir == pytest.approx(80)
        assert sm.schedule_at(at("07:15"), c).ir == pytest.approx(40)

    def test_off(self):
        assert sm.schedule_at(at("02:00"), cfg(ir={"mode": "off"})).ir == 0


class TestTimeZones:
    def test_dst_spring_forward_keeps_wall_clock(self):
        """29 Mar 2026: clocks go 01:00 -> 02:00 GMT->BST. 07:00 is still
        07:00 local (06:00 UTC)."""
        c = cfg(timezone="Europe/London")
        on = datetime(2026, 3, 29, 7, 0, tzinfo=LONDON).timestamp()
        assert sm.schedule_at(on - 60, c).phase == "night"
        assert sm.schedule_at(on, c).phase == "sunrise"
        assert datetime.fromtimestamp(on, UTC).hour == 6

    def test_dst_fall_back_night_is_an_hour_longer(self):
        c = cfg(timezone="Europe/London")
        st = sm.schedule_at(datetime(2026, 10, 24, 20, 0, tzinfo=LONDON).timestamp(), c)
        off_at = datetime(2026, 10, 24, 19, 30, tzinfo=LONDON).timestamp()
        assert st.next_change == "sunrise"
        night_s = st.next_change_at - off_at
        assert night_s == pytest.approx((11.5 + 1) * 3600)

    def test_utc_does_not_shift(self):
        c = cfg(timezone="UTC")
        on = datetime(2026, 3, 29, 7, 0, tzinfo=UTC).timestamp()
        assert sm.schedule_at(on, c).phase == "sunrise"


class TestOverrides:
    def _ov(self, **kw):
        base = {"id": "x", "kind": "override", "boxes": None, "visible": 30.0,
                "ir": None, "started_at": 0.0, "until": 1e12}
        base.update(kw)
        return sm.Override(**base)

    def test_override_applies_to_named_boxes_only(self):
        c = cfg()
        now = at("12:00")
        st = sm.schedule_at(now, c)
        lv = sm.compartment_levels(now, c, st, [self._ov(boxes=["A2"])])
        assert lv["A1"]["visible"] == 100 and lv["A1"]["source_visible"] == "schedule"
        assert lv["A2"]["visible"] == 30 and lv["A2"]["source_visible"] == "override"
        assert lv["A2"]["ir"] == 80          # IR left on schedule

    def test_expired_override_is_ignored(self):
        now = at("12:00")
        st = sm.schedule_at(now, cfg())
        lv = sm.compartment_levels(now, cfg(), st, [self._ov(until=now - 1)])
        assert lv["A1"]["visible"] == 100

    def test_test_strip_flashes(self):
        now = at("02:00")
        c = cfg()
        st = sm.schedule_at(now, c)
        ov = self._ov(kind="test", boxes=["A1"], visible=None, role="visible",
                      started_at=now)
        on = sm.compartment_levels(now + 0.1, c, st, [ov])["A1"]["visible"]
        off = sm.compartment_levels(now + 0.6, c, st, [ov])["A1"]["visible"]
        assert (on, off) == (100.0, 0.0)

    def test_round_trip(self):
        ov = self._ov(boxes=["A1"], note="cleaning")
        assert sm.Override.from_dict(ov.to_dict(now=5.0)) == ov


class TestSlew:
    def test_limits_scheduled_changes(self):
        sl = sm.Slew(60, {"A1.visible": 0.0})
        assert sl.step("A1.visible", 100, dt=6) == pytest.approx(10)
        assert sl.step("A1.visible", 100, dt=60) == 100

    def test_bypass_and_unknown_start_jump(self):
        sl = sm.Slew(60)
        assert sl.step("A1.visible", 70, dt=0.1) == 70      # nothing known yet
        assert sl.step("A1.visible", 0, dt=0.1, bypass=True) == 0

    def test_clock_step_fades(self):
        """A clock that jumps from 03:00 to 12:00 fades to day over 60 s."""
        c = cfg()
        sl = sm.Slew(60)
        sl.step("A1.visible", sm.schedule_at(at("03:00"), c).visible, 0.1)
        level = sl.step("A1.visible", sm.schedule_at(at("12:00"), c).visible, 0.1)
        assert level == pytest.approx(100 * 0.1 / 60)


class TestFrame:
    def test_8bit_with_trim(self):
        c = cfg()
        frame = sm.build_frame({"A1": {"visible": 100, "ir": 50},
                               "A2": {"visible": 100, "ir": 0}}, c)
        assert len(frame) == 512
        assert list(frame[:4]) == [255, 128, 128, 0]      # A2 visible at 50 % trim
        assert not any(frame[4:])

    def test_16bit_coarse_fine(self):
        c = cfg(channel_bits=16, compartments=[{"box": "A1", "visible": 10, "ir": 12}])
        frame = sm.build_frame({"A1": {"visible": 50, "ir": 0.4}}, c)
        v = round(0.5 * 65535)
        assert (frame[9], frame[10]) == (v >> 8, v & 0xFF)
        ir = round(0.004 * 65535)
        assert (frame[11], frame[12]) == (ir >> 8, ir & 0xFF)


class TestValidation:
    def test_default_shape_is_valid(self):
        assert sm.validate(cfg()) == []

    @pytest.mark.parametrize("patch,needle", [
        ({"compartments": [{"box": "A1", "visible": 1}, {"box": "A1", "visible": 2}]},
         "listed twice"),
        ({"compartments": [{"box": "A1", "visible": 1, "ir": 1}]}, "already used"),
        ({"compartments": [{"box": "A1", "visible": 513}]}, "outside"),
        ({"channel_bits": 16, "compartments": [{"box": "A1", "visible": 512}]}, "outside"),
        ({"channel_bits": 16, "compartments": [{"box": "A1", "visible": 1, "ir": 2}]},
         "already used"),
        ({"compartments": [{"box": "", "visible": 1}]}, "empty"),
        ({"compartments": [{"box": "A1", "visible": "3"}]}, "whole number"),
        ({"timezone": "Mars/Olympus"}, "time zone"),
        ({"channel_bits": 12}, "8 or 16"),
    ])
    def test_rejects(self, patch, needle):
        c = cfg(**patch)
        errors = sm.validate(c)
        assert any(needle in e for e in errors), errors

    @pytest.mark.parametrize("sched,needle", [
        ({"lights_on": "7am"}, "HH:MM"),
        ({"lights_off": "07:00"}, "same time"),
        ({"lights_on": "07:00", "lights_off": "07:20", "sunrise_minutes": 30}, "light period"),
        ({"lights_on": "07:00", "lights_off": "06:50", "sunset_minutes": 30}, "dark period"),
        ({"day_level": 120}, "0-100"),
        ({"curve": "sigmoid"}, "Curve"),
    ])
    def test_rejects_schedule(self, sched, needle):
        c = cfg()
        c = copy.deepcopy(c)
        c["schedule"].update(sched)
        assert any(needle in e for e in sm.validate(c)), sm.validate(c)

    def test_channel_map(self):
        m = sm.channel_map([{"box": "A1", "visible": 1, "ir": 3}], bits=16)
        assert [(r["channel"], r["role"], r["part"]) for r in m] == [
            (1, "visible", "coarse"), (2, "visible", "fine"),
            (3, "ir", "coarse"), (4, "ir", "fine")]
