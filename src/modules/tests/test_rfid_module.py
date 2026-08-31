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

from src.modules.variants.rfid.rfid_module import Ping, RFIDModule


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
