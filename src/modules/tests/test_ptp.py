"""
Tests for src/modules/ptp.py.

Same shape as src/controller/tests/test_ptp.py: __init__ requires root and
does live system probes (which ptp4l/phc2sys, /sys/class/net/<iface>,
ethtool, systemctl), so every test constructs via PTP.__new__(PTP). This
module additionally parses ptp4l logs (the controller side only has
phc2sys, since it's the grandmaster) and has a distinct
get_offset_statistics() aggregator.
"""

import time
from unittest.mock import MagicMock, patch

from src.modules.ptp import PTP, PTPRole


def _buffer_entry(ptp4l_offset_ns) -> dict:
    return {
        "ptp4l_offset_ns": ptp4l_offset_ns, "ptp4l_freq": 0,
        "phc2sys_offset_ns": 0, "phc2sys_freq": 0,
    }


def _make_ptp(**attrs) -> PTP:
    ptp = PTP.__new__(PTP)
    ptp.logger = MagicMock()
    ptp.role = PTPRole.SLAVE
    ptp.interface = "eth0"
    ptp.ptp4l_service = "ptp4l"
    ptp.phc2sys_service = "phc2sys"
    ptp.running = False
    ptp.status = "not running"
    ptp.last_sync_time = None
    ptp.last_offset = None
    ptp.last_freq = None
    ptp.ptp_buffer = []
    ptp.max_buffer_size = 1000
    ptp.latest_ptp4l_offset = None
    ptp.latest_ptp4l_freq = None
    ptp.latest_phc2sys_offset_ns = None
    ptp.latest_phc2sys_freq = None
    for key, value in attrs.items():
        setattr(ptp, key, value)
    return ptp


# ---------------------------------------------------------------------------
# _parse_ptp4l_line
# ---------------------------------------------------------------------------

class TestParsePtp4lLine:
    def test_parses_master_offset(self):
        ptp = _make_ptp()
        line = "ptp4l[1.0]: master offset -320 s2 freq +100 path delay 500"
        ptp._parse_ptp4l_line(line)
        assert ptp.latest_ptp4l_offset == -320.0
        assert ptp.status == "synchronized"
        assert ptp.last_sync_time is not None

    def test_parses_s2_freq(self):
        ptp = _make_ptp()
        line = "ptp4l[1.0]: master offset 10 s2 freq -456 path delay 500"
        ptp._parse_ptp4l_line(line)
        assert ptp.latest_ptp4l_freq == -456
        assert ptp.last_freq == -456

    def test_port_state_listening(self):
        ptp = _make_ptp()
        ptp._parse_ptp4l_line("ptp4l[1.0]: port 1: port state changed, now LISTENING")
        assert ptp.status == "listening"

    def test_port_state_slave(self):
        ptp = _make_ptp()
        ptp._parse_ptp4l_line("ptp4l[1.0]: port 1: port state changed, now SLAVE")
        assert ptp.status == "slave"

    def test_port_state_master(self):
        ptp = _make_ptp()
        ptp._parse_ptp4l_line("ptp4l[1.0]: port 1: port state changed, now MASTER")
        assert ptp.status == "master"

    def test_fault_sets_error_status(self):
        ptp = _make_ptp(status="synchronized")
        ptp._parse_ptp4l_line("ptp4l[1.0]: port 1: FAULT_DETECTED (FT_UNSPECIFIED)")
        assert ptp.status == "error"

    def test_blank_line_is_a_no_op(self):
        ptp = _make_ptp()
        ptp._parse_ptp4l_line("   ")
        assert ptp.ptp_buffer == []

    def test_line_without_a_number_does_not_raise(self):
        ptp = _make_ptp()
        ptp._parse_ptp4l_line("ptp4l[1.0]: master offset unavailable")
        assert ptp.latest_ptp4l_offset is None


class TestParsePhc2sysLine:
    def test_parses_phc_offset(self):
        ptp = _make_ptp()
        line = "phc2sys[1.0]: phc offset -120 s2 freq +50 delay 500"
        ptp._parse_phc2sys_line(line)
        assert ptp.latest_phc2sys_offset_ns == -120.0
        assert ptp.status == "synchronized"

    def test_error_line_sets_error_status(self):
        ptp = _make_ptp(status="synchronized")
        line = "phc2sys[1.0]: could not create clock: Error opening device"
        ptp._parse_phc2sys_line(line)
        assert ptp.status == "error"


class TestAddBufferEntry:
    def test_appends_all_four_latest_values(self):
        ptp = _make_ptp(
            latest_ptp4l_freq=1, latest_ptp4l_offset=2,
            latest_phc2sys_freq=3, latest_phc2sys_offset_ns=4,
        )
        ptp._add_buffer_entry(100.0)
        assert ptp.ptp_buffer == [{
            "timestamp": 100.0, "ptp4l_freq": 1, "ptp4l_offset_ns": 2,
            "phc2sys_freq": 3, "phc2sys_offset_ns": 4,
        }]

    def test_trims_oldest_entry_past_max_buffer_size(self):
        ptp = _make_ptp(max_buffer_size=2)
        ptp._add_buffer_entry(1.0)
        ptp._add_buffer_entry(2.0)
        ptp._add_buffer_entry(3.0)
        assert len(ptp.ptp_buffer) == 2
        assert ptp.ptp_buffer[0]["timestamp"] == 2.0


class TestGetPtpBuffer:
    def test_returns_copy_and_supports_max_entries(self):
        ptp = _make_ptp()
        ptp.ptp_buffer = [{"timestamp": float(i)} for i in range(4)]
        assert ptp.get_ptp_buffer() is not ptp.ptp_buffer
        assert ptp.get_ptp_buffer(max_entries=2) == [
            {"timestamp": 2.0}, {"timestamp": 3.0}
        ]


class TestGetOffsetStatistics:
    def test_empty_buffer_returns_all_none(self):
        ptp = _make_ptp()
        stats = ptp.get_offset_statistics()
        assert stats["ptp4l_offset_ns"] == {
            "count": 0, "mean": None, "std_dev": None, "min": None, "max": None
        }

    def test_computes_mean_min_max_and_std_dev(self):
        ptp = _make_ptp()
        ptp.ptp_buffer = [_buffer_entry(10), _buffer_entry(20), _buffer_entry(30)]
        stats = ptp.get_offset_statistics()["ptp4l_offset_ns"]
        assert stats["count"] == 3
        assert stats["mean"] == 20.0
        assert stats["min"] == 10
        assert stats["max"] == 30
        assert round(stats["std_dev"], 3) == round((200 / 3) ** 0.5, 3)

    def test_none_entries_are_excluded_from_stats(self):
        ptp = _make_ptp()
        ptp.ptp_buffer = [_buffer_entry(None), _buffer_entry(10)]
        stats = ptp.get_offset_statistics()["ptp4l_offset_ns"]
        assert stats["count"] == 1
        assert stats["mean"] == 10.0


class TestIsSynchronized:
    def test_true_within_timeout(self):
        ptp = _make_ptp(last_sync_time=time.time())
        assert ptp.is_synchronized(timeout=5) is True

    def test_false_when_never_synced(self):
        ptp = _make_ptp(last_sync_time=None)
        assert ptp.is_synchronized() is False


class TestGetPtpTime:
    def test_returns_current_time(self):
        ptp = _make_ptp()
        assert abs(ptp.get_ptp_time() - time.time()) < 1.0


# ---------------------------------------------------------------------------
# subprocess-mocked
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_aggregates_service_status_and_latest_values(self):
        ptp = _make_ptp(status="synchronized", latest_ptp4l_offset=5)
        with patch.object(ptp, "_get_service_status", side_effect=["active", "active"]):
            result = ptp.get_status()
        assert result["role"] == "slave"
        assert result["ptp4l_service"] == "active"
        assert result["ptp4l_offset_ns"] == 5


class TestGetServiceLogs:
    def test_both_services_when_unspecified(self):
        ptp = _make_ptp()
        with patch.object(ptp, "_get_service_logs", side_effect=["a", "b"]):
            assert ptp.get_service_logs() == {"ptp4l": "a", "phc2sys": "b"}

    def test_specific_service(self):
        ptp = _make_ptp()
        with patch.object(ptp, "_get_service_logs", return_value="x") as mock_logs:
            assert ptp.get_service_logs(service_name="ptp4l", lines=3) == "x"
        mock_logs.assert_called_once_with("ptp4l", 3)


class TestRestart:
    def test_success_stops_waits_then_starts(self):
        ptp = _make_ptp()
        with patch.object(ptp, "stop") as mock_stop, \
             patch.object(ptp, "start") as mock_start, \
             patch("src.modules.ptp.time.sleep") as mock_sleep:
            result = ptp.restart()

        mock_stop.assert_called_once()
        mock_sleep.assert_called_once_with(2)
        mock_start.assert_called_once()
        assert result == {"status": "success", "message": "PTP services restarted"}

    def test_exception_is_caught_and_reported(self):
        ptp = _make_ptp()
        with patch.object(ptp, "stop", side_effect=RuntimeError("systemctl wedged")), \
             patch("src.modules.ptp.time.sleep"):
            result = ptp.restart()
        assert result["status"] == "error"
        assert "systemctl wedged" in result["message"]
