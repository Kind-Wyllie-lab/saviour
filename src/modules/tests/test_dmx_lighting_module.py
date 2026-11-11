"""DmxLightingModule behaviour against the mock widget, without the Module
stack (built via __new__ + _init_state, with a fake config and facade)."""

import csv
import json
import logging
import os
from datetime import UTC, datetime

import pytest

from src.modules.variants.dmx_lighting import dmx_lighting_module as dlm

DEFAULTS = os.path.join(os.path.dirname(dlm.__file__), "dmx_lighting_config.json")
LOCKED = {"ptp4l_offset_ns_min": -200, "ptp4l_offset_ns_max": 300,
          "phc2sys_offset_ns_min": -50, "phc2sys_offset_ns_max": 80}
UNLOCKED = dict.fromkeys(LOCKED)


def noon(h=12, m=0):
    return datetime(2026, 11, 11, h, m, tzinfo=UTC).timestamp()


class FakeConfig:
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        cur = self.data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


class FakeFacade:
    def __init__(self, tmp_path):
        self.ptp = dict(LOCKED)
        self.prefix = str(tmp_path / "rec" / "sess_dmx")
        self.session_files, self.staged = [], []
        self.segment = 0

    def get_ptp_recent_offset_range(self, _w):
        return self.ptp

    def get_utc_time(self, _t):
        return "20261111-120000"

    def get_segment_start_time(self):
        return 0

    def get_filename_prefix(self):
        return self.prefix

    def get_segment_id(self):
        return self.segment

    def add_session_file(self, f):
        self.session_files.append(f)

    def stage_file_for_export(self, f):
        self.staged.append(f)


def section(tmp_path, **over):
    with open(DEFAULTS) as f:
        sec = json.load(f)["dmx_lighting"]
    sec.update({
        "timezone": "UTC",
        "compartments": [{"box": "A1", "visible": 1, "ir": 2},
                         {"box": "A2", "visible": 3, "ir": 4}],
        "mock": {"enabled": True},
        "_state_dir": str(tmp_path / "lighting"),
    })
    sec["schedule"]["curve"] = "linear"
    sec.update(over)
    return sec


@pytest.fixture(autouse=True)
def no_timedatectl(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("timedatectl")
    monkeypatch.setattr(dlm.subprocess, "run", boom)


def make(tmp_path, **over):
    m = dlm.DmxLightingModule.__new__(dlm.DmxLightingModule)
    m._init_state()
    m.config = FakeConfig({"dmx_lighting": section(tmp_path, **over)})
    m.logger = logging.getLogger("test_dmx")
    m.facade = FakeFacade(tmp_path)
    m.communication = None
    assert m._apply_config(initial=True), m._config_error
    return m


def frame(m):
    return m._mock_widget.last_frame


class TestClockGate:
    def test_waits_without_sending_but_finds_the_widget(self, tmp_path):
        m = make(tmp_path)
        m.facade.ptp = dict(UNLOCKED)
        m._tick(noon(), 0.1)
        assert m._mode == "waiting_for_clock"
        assert m.hardware_fault is None            # widget found and talking
        assert frame(m) is None                    # but nothing sent
        st = m.lighting_status()
        assert st["clock"]["trusted"] is False and st["clock"]["waiting_s"] is not None

    def test_ptp_lock_trusts_and_latches(self, tmp_path):
        m = make(tmp_path)
        m._tick(noon(), 0.1)
        assert m._mode == "day"
        assert list(frame(m)[:4]) == [255, 255, 255, 255]    # day 100 %, IR 100 %
        m.facade.ptp = dict(UNLOCKED)              # controller goes away
        m._tick(noon() + 1, 0.1)
        assert m._mode == "day"

    def test_not_required(self, tmp_path):
        m = make(tmp_path, require_synced_clock=False)
        m.facade.ptp = dict(UNLOCKED)
        m._tick(noon(), 0.1)
        assert m._mode == "day" and frame(m) is not None


class TestOutput:
    def test_keepalive_only_resends_after_interval(self, tmp_path):
        m = make(tmp_path)
        m._tick(noon(), 0.1)
        n = len(m._mock_widget.frames)
        m._tick(noon() + 0.1, 0.1)
        assert len(m._mock_widget.frames) == n
        m._widget.last_send_monotonic -= 5
        m._tick(noon() + 0.2, 0.1)
        assert len(m._mock_widget.frames) == n + 1

    def test_restart_fades_from_saved_levels(self, tmp_path):
        m = make(tmp_path)
        m._slew.current.update({"A1.visible": 0.0, "A2.visible": 0.0})
        m._tick(noon(), 0.1)
        assert m._levels["A1"]["visible"] == pytest.approx(100 * 0.1 / 60)
        assert m._mode == "day"

    def test_unplug_then_replug(self, tmp_path):
        m = make(tmp_path)
        m._tick(noon(), 0.1)
        m._mock_widget.unplugged = True
        m._widget.last_send_monotonic -= 5          # keepalive due
        m._tick(noon() + 1, 0.1)
        assert m._mode == "fault" and "disconnected" in m.hardware_fault
        m._tick(noon() + 2, 0.1)                     # reopen fails, backs off
        assert m._widget is None and m.hardware_fault
        m._mock_widget.unplugged = False
        m._next_open_at = 0
        m._tick(noon() + 3, 0.1)
        assert m.hardware_fault is None and m._mode == "day"

    def test_events_logged(self, tmp_path):
        m = make(tmp_path)
        m._tick(noon(), 0.1)
        files = list((tmp_path / "lighting_mock").glob("events_*.csv"))
        assert len(files) == 1
        kinds = [r["event"] for r in csv.DictReader(open(files[0]))]
        assert "clock_trusted" in kinds and "widget_connected" in kinds and "mode" in kinds


class TestOverrides:
    def test_override_applies_persists_and_expires(self, tmp_path):
        m = make(tmp_path)
        m._tick(noon(), 0.1)
        r = m.set_override(boxes=["A1"], visible=20, minutes=5, note="cleaning")
        assert r["result"] == "success"
        m._tick(noon() + 0.1, 0.1)
        assert frame(m)[0] == round(0.2 * 255) and frame(m)[2] == 255
        assert m._mode == "override"

        # survives a restart
        m2 = make(tmp_path)
        m2._load_state()
        assert [ov.id for ov in m2._overrides] == [r["override"]["id"]]

        # and expires
        later = r["override"]["until"] + 1       # overrides run on real time
        m._tick(later, 0.1)
        assert not m._overrides
        assert m._mode == dlm.schedule_at(later, m._cfg).phase

    @pytest.mark.parametrize("kwargs,needle", [
        ({"visible": 50, "minutes": 241}, "at most 240"),
        ({"visible": 50, "minutes": 0}, "more than 0"),
        ({"boxes": ["Z9"], "visible": 50}, "unknown"),
        ({"minutes": 5}, "visible and/or IR"),
        ({"visible": 150, "minutes": 5}, "0-100"),
    ])
    def test_rejects(self, tmp_path, kwargs, needle):
        r = make(tmp_path).set_override(**kwargs)
        assert r["result"] == "error" and needle in r["error"]

    def test_blackout_leaves_ir(self, tmp_path):
        m = make(tmp_path)
        m.blackout(minutes=10)
        m._tick(noon(), 0.1)
        assert list(frame(m)[:4]) == [0, 255, 0, 255]
        assert m._mode == "blackout"

    def test_clear_override(self, tmp_path):
        m = make(tmp_path)
        a = m.blackout(minutes=10)["override"]["id"]
        m.set_override(visible=10, minutes=10)
        assert m.clear_override(a)["cleared"] == [a]
        assert len(m._overrides) == 1
        assert m.clear_override("nope")["result"] == "error"
        m.clear_override()
        assert m._overrides == []

    def test_strip_test(self, tmp_path):
        m = make(tmp_path)
        assert m.test_strip(box="A2", role="ir", seconds=10)["result"] == "success"
        assert m.test_strip(box="A2", role="uv")["result"] == "error"
        assert m.test_strip(box="A2", seconds=500)["result"] == "error"
        assert len([o for o in m._overrides if o.kind == "test"]) == 1


class TestConfig:
    def test_set_config_rejects_invalid(self, tmp_path, monkeypatch):
        m = make(tmp_path)
        called = []
        monkeypatch.setattr(dlm.Module, "set_config",
                            lambda self, c, persist=True: called.append(c) or {"result": "success"})
        bad = {"dmx_lighting": {"compartments": [{"box": "A1", "visible": 1, "ir": 1}]}}
        r = m.set_config(bad)
        assert r["result"].startswith("invalid lighting config") and not called
        good = {"dmx_lighting": {"schedule": {"lights_on": "06:00"}}}
        assert m.set_config(good)["result"] == "success" and called

    def test_invalid_active_config_keeps_last_good(self, tmp_path):
        m = make(tmp_path)
        m.config.data["dmx_lighting"]["channel_bits"] = 12
        assert m._apply_config() is False
        assert m._cfg["channel_bits"] == 8 and m._config_error
        assert m.lighting_status()["config_error"]


class TestSessionLog:
    def test_csv_rows_and_export(self, tmp_path):
        m = make(tmp_path)
        m._tick(noon(7, 0), 0.1)
        assert m._start_new_recording() is True
        m._last_session_row = 0                     # session rows ran on real time
        for k in range(1, 6):
            m._tick(noon(7, 0) + k * 0.5, 0.5)      # sunrise: a row a second
        assert m._stop_recording() is True
        assert m.facade.staged == m.facade.session_files
        rows = list(csv.DictReader(open(m.facade.staged[0])))
        assert rows[0]["event"] == "session_start" and rows[-1]["event"] == "session_stop"
        assert "A1_visible" in rows[0] and "A2_ir" in rows[0]
        data_rows = [r for r in rows if not r["event"]]
        assert 2 <= len(data_rows) <= 3
        assert all(r["phase"] == "sunrise" for r in data_rows)

    def test_status_block_shape(self, tmp_path):
        m = make(tmp_path)
        m._tick(noon(), 0.1)
        st = m.lighting_status()
        assert st["mode"] == "day" and st["schedule"]["next_change"] == "sunset"
        assert st["boxes"]["A1"] == {"visible": 100, "ir": 100,
                                     "source_visible": "schedule", "source_ir": "schedule"}
        assert st["widget"]["connected"] and st["widget"]["serial"] == "12345678"
        assert st["mock"] is True
        json.dumps(st)
