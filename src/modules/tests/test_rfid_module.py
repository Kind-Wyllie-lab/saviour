"""
Tests for src/modules/variants/rfid/rfid_module.py.

RFIDModule.__init__ builds a real Config, RS485Bus and MJPEGStreamServer, so
every test constructs via RFIDModule.__new__ (same pattern as
test_camera_base.py) and sets only the attributes the method under test
touches. The cv2 render is smoke-tested (returns JPEG bytes) rather than
pixel-inspected, matching how the other module tests treat their renderers.
"""

import collections
import threading
import time
from unittest.mock import MagicMock

from src.modules.variants.rfid.rfid_module import Ping, PresenceTracker, RFIDModule


def _make_rfid(**attrs) -> RFIDModule:
    m = RFIDModule.__new__(RFIDModule)
    m.logger = MagicMock()
    m._pings = collections.deque(maxlen=1000)
    m._pings_lock = threading.Lock()
    m._total_reads = 0
    m._last_ping = None
    m.is_recording = False
    m._csv_handle = None
    m.current_rfid_filename = None
    m._visit_csv_handle = None
    m.current_visit_filename = None
    m._presence = None
    m._presence_lock = threading.Lock()
    m._sweep_timer = None
    m.is_streaming = False
    m.bus = MagicMock()
    m.bus.is_connected = False
    m.bus.units = {}
    m.bus.port = ""
    m.communication = MagicMock()
    m.communication.controller_ip = None
    m.facade = MagicMock()
    m.config = MagicMock()
    m.config.get.side_effect = lambda k, d=None: d
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _cfg(mapping: dict):
    """MagicMock config.get side_effect from a plain {key: value} mapping."""
    return lambda k, d=None: mapping.get(k, d)


class TestRecordPing:
    def test_appends_and_counts(self):
        m = _make_rfid()
        p = Ping(ts=1.0, unit=1, tag="ABCD", type_name="Trovan Unique")
        m._record_ping(p)
        assert list(m._pings) == [p]
        assert m._total_reads == 1
        assert m._last_ping is p

    def test_sends_status_when_controller_present(self):
        m = _make_rfid()
        m.communication.controller_ip = "10.0.0.1"
        m._record_ping(Ping(ts=1.0, unit=2, tag="F00D", type_name="x"))
        payload = m.communication.send_status.call_args[0][0]
        assert payload["type"] == "rfid_read"
        assert payload["transponder_id"] == "F00D"
        assert payload["total_reads"] == 1

    def test_writes_csv_row_only_while_recording(self, tmp_path):
        m = _make_rfid()
        f = tmp_path / "reads.csv"
        m._csv_handle = open(f, "w", buffering=1)
        m.facade.get_utc_time.return_value = "2026-01-01T00:00:01"

        m._record_ping(Ping(ts=1.5, unit=3, tag="DEAD", type_name="t"))  # not recording
        m.is_recording = True
        m._record_ping(Ping(ts=2.5, unit=3, tag="BEEF", type_name="t"))  # recording
        m._csv_handle.close()

        lines = f.read_text().strip().splitlines()
        assert len(lines) == 1
        assert lines[0] == "2500000000,2026-01-01T00:00:01,3,BEEF,t"

    def test_deque_is_bounded(self):
        m = _make_rfid()
        m._pings = collections.deque(maxlen=5)
        for i in range(20):
            m._record_ping(Ping(ts=float(i), unit=1, tag=f"T{i}", type_name="t"))
        assert len(m._pings) == 5
        assert m._total_reads == 20


class TestCheckRfid:
    def test_connected_bus(self):
        m = _make_rfid()
        m.bus.is_connected = True
        m.bus.port = "/dev/ttyUSB0"
        m.bus.units = {1: object(), 2: object()}
        ok, msg = m._check_rfid()
        assert ok is True
        assert "/dev/ttyUSB0" in msg and "2 unit" in msg

    def test_no_reader_not_ready(self):
        m = _make_rfid()
        ok, msg = m._check_rfid()
        assert ok is False
        assert "no RFID reader" in msg


class TestRecordingHooks:
    def test_start_opens_csv_with_header(self, tmp_path):
        m = _make_rfid()
        target = tmp_path / "sess_0_ts.csv"
        m._get_rfid_filename = lambda: str(target)
        assert m._start_new_recording() is True
        assert m.is_recording is True
        m.facade.add_session_file.assert_called_once_with(str(target))
        m._csv_handle.close()
        header = target.read_text().splitlines()[0]
        assert header.startswith("timestamp_ns,timestamp_utc")

    def test_stop_closes_and_stages(self, tmp_path):
        m = _make_rfid()
        target = tmp_path / "sess_0_ts.csv"
        m._get_rfid_filename = lambda: str(target)
        m._start_new_recording()
        assert m._stop_recording() is True
        assert m.is_recording is False
        assert m._csv_handle is None
        m.facade.stage_file_for_export.assert_called_with(str(target))

    def test_segment_rotation_stages_previous_and_opens_next(self, tmp_path):
        m = _make_rfid()
        names = iter([str(tmp_path / "s_0.csv"), str(tmp_path / "s_1.csv")])
        m._get_rfid_filename = lambda: next(names)
        m._start_new_recording()
        first = m.current_rfid_filename
        assert m._start_next_recording_segment() is True
        m.facade.stage_file_for_export.assert_called_with(first)
        assert m.current_rfid_filename != first
        m._csv_handle.close()


class TestRender:
    def test_empty_buffer_returns_jpeg(self):
        m = _make_rfid()
        out = m._render_monitor_frame()
        assert isinstance(out, bytes) and out[:2] == b"\xff\xd8"  # JPEG SOI

    def test_with_pings_returns_jpeg(self):
        m = _make_rfid()
        now = time.time()
        for i in range(6):
            m._pings.append(Ping(ts=now - i, unit=1 + (i % 2),
                                 tag=f"0000AAA{i}", type_name="t"))
        m._last_ping = m._pings[-1]
        out = m._render_monitor_frame()
        assert isinstance(out, bytes) and out[:2] == b"\xff\xd8"


class TestCommands:
    def test_scan_requires_connection(self):
        m = _make_rfid()
        m.bus.is_connected = False
        assert m.rfid_scan()["result"] == "error"
        m.bus.is_connected = True
        assert m.rfid_scan()["result"] == "success"
        m.bus.scan_bus.assert_called_once()


class TestConfigureSpecial:
    def test_rfid_key_change_recycles_bus(self):
        m = _make_rfid()
        m._teardown_bus = MagicMock()
        m._bring_up_bus = MagicMock()
        m.configure_module_special(["rfid.baud"])
        m._teardown_bus.assert_called_once()
        m._bring_up_bus.assert_called_once()

    def test_unrelated_key_change_is_noop(self):
        m = _make_rfid()
        m._teardown_bus = MagicMock()
        m._bring_up_bus = MagicMock()
        m.configure_module_special(["monitoring.history_secs"])
        m._teardown_bus.assert_not_called()

    def test_presence_key_change_does_not_recycle_bus(self):
        m = _make_rfid()
        m._teardown_bus = MagicMock()
        m._bring_up_bus = MagicMock()
        m.configure_module_special(["rfid.presence.gap_timeout_s"])
        m._teardown_bus.assert_not_called()
        m._bring_up_bus.assert_not_called()

    def test_presence_enable_builds_tracker(self):
        m = _make_rfid()
        m.config.get.side_effect = _cfg({"rfid.presence.enabled": True})
        m._teardown_bus = MagicMock()
        m._bring_up_bus = MagicMock()
        m.configure_module_special(["rfid.presence.enabled"])
        assert isinstance(m._presence, PresenceTracker)


class TestPresenceTracker:
    def _tracker(self, **kw):
        kw.setdefault("gap_timeout_s", 2.0)
        kw.setdefault("min_pings", 2)
        kw.setdefault("min_dwell_s", 0.0)
        return PresenceTracker(**kw)

    def _ping(self, ts, unit=1, tag="AAAA", type_name="t"):
        return Ping(ts=ts, unit=unit, tag=tag, type_name=type_name)

    def test_enter_after_min_pings(self):
        pt = self._tracker(min_pings=3)
        assert pt.observe(self._ping(0.0)) == (None, None)
        assert pt.observe(self._ping(0.3)) == (None, None)
        exited, entered = pt.observe(self._ping(0.6))
        assert exited is None
        assert entered is not None and entered.count == 3

    def test_single_blip_never_enters(self):
        pt = self._tracker(min_pings=2)
        assert pt.observe(self._ping(0.0)) == (None, None)
        # 3s later, same tag -> old (unannounced) visit gone, fresh one starts
        exited, entered = pt.observe(self._ping(3.0))
        assert exited is None and entered is None
        assert pt.sweep(6.0) == []  # nothing announced, nothing to close

    def test_min_dwell_gate(self):
        pt = self._tracker(min_pings=1, min_dwell_s=1.0)
        assert pt.observe(self._ping(0.0)) == (None, None)      # dwell 0
        _, entered = pt.observe(self._ping(1.2))                # dwell 1.2 -> in
        assert entered is not None

    def test_sweep_closes_idle_visit(self):
        pt = self._tracker(min_pings=1)
        _, entered = pt.observe(self._ping(10.0))
        assert entered is not None
        assert pt.sweep(11.0) == []                # within gap
        closed = pt.sweep(13.0)                    # 3s idle > 2s gap
        assert len(closed) == 1 and closed[0].count == 1

    def test_mid_observe_timeout_emits_exit(self):
        pt = self._tracker(min_pings=1)
        pt.observe(self._ping(0.0))                # announced immediately
        exited, entered = pt.observe(self._ping(5.0))  # long gap, same tag
        assert exited is not None and exited.enter_ts == 0.0
        assert entered is not None and entered.enter_ts == 5.0

    def test_separate_units_are_separate_visits(self):
        pt = self._tracker(min_pings=1)
        _, e1 = pt.observe(self._ping(0.0, unit=1))
        _, e2 = pt.observe(self._ping(0.1, unit=2))
        assert e1 is not None and e2 is not None
        assert len(pt.sweep(5.0)) == 2

    def test_close_all_returns_only_announced(self):
        pt = self._tracker(min_pings=2)
        pt.observe(self._ping(0.0, tag="AAAA"))                 # unannounced
        pt.observe(self._ping(0.1, tag="BBBB"))
        pt.observe(self._ping(0.2, tag="BBBB"))                 # BBBB announced
        closed = pt.close_all()
        assert [v.tag for v in closed] == ["BBBB"]


class TestVisitRecording:
    def _recording_module(self, tmp_path, record="both"):
        m = _make_rfid()
        m.config.get.side_effect = _cfg({
            "rfid.presence.enabled": True,
            "rfid.presence.min_pings": 1,
            "rfid.presence.gap_timeout_s": 2.0,
            "rfid.presence.record": record,
        })
        m._rebuild_presence()
        raw = iter(str(tmp_path / f"raw_{i}.csv") for i in range(9))
        vis = iter(str(tmp_path / f"vis_{i}.csv") for i in range(9))
        m._get_rfid_filename = lambda: next(raw)
        m._get_visit_filename = lambda: next(vis)
        m.facade.get_utc_time.return_value = "2026-01-01T00:00:00"
        return m

    def test_both_mode_opens_raw_and_visit_csv(self, tmp_path):
        m = self._recording_module(tmp_path, record="both")
        m._start_new_recording()
        assert m._csv_handle is not None and m._visit_csv_handle is not None
        assert m.facade.add_session_file.call_count == 2

    def test_visits_mode_skips_raw_csv(self, tmp_path):
        m = self._recording_module(tmp_path, record="visits")
        m._start_new_recording()
        assert m._csv_handle is None and m._visit_csv_handle is not None

    def test_visit_row_written_on_close(self, tmp_path):
        m = self._recording_module(tmp_path, record="visits")
        m._start_new_recording()
        vf = m.current_visit_filename
        m._record_ping(Ping(ts=100.0, unit=1, tag="DEAD", type_name="Trovan"))
        m._record_ping(Ping(ts=100.5, unit=1, tag="DEAD", type_name="Trovan"))
        m._flush_open_visits("recording_stopped")
        m._close_visit_csv()
        rows = [r for r in open(vf).read().splitlines() if r]
        assert rows[0].startswith("enter_ts_ns,exit_ts_ns")
        assert rows[1].split(",")[5] == "DEAD"           # transponder_id
        assert rows[1].split(",")[7] == "2"              # ping_count
        assert rows[1].split(",")[-1] == "recording_stopped"

    def test_segment_rotation_flushes_and_reopens(self, tmp_path):
        m = self._recording_module(tmp_path, record="both")
        m._start_new_recording()
        first_visit = m.current_visit_filename
        m._record_ping(Ping(ts=1.0, unit=1, tag="BEEF", type_name="t"))
        m._start_next_recording_segment()
        m.facade.stage_file_for_export.assert_any_call(first_visit)
        assert m.current_visit_filename != first_visit
        assert m._visit_csv_handle is not None
