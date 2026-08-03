"""
Tests for src/controller/ptp.py.

PTP.__init__ requires root (os.geteuid() check) and does several real
system probes (which ptp4l/phc2sys, /sys/class/net/<iface>, ethtool,
systemctl status) before anything else runs, so every test here constructs
via PTP.__new__(PTP) (bypassing __init__ entirely) and sets only the
attributes the method under test reads -- same pattern as
CameraBase.__new__ in test_camera_base.py.

Covers the phc2sys log-line parser (the file's real "PTP log parsing"
logic per CLAUDE.md), the offset ring buffer, and the subprocess-mocked
status/NTP getters. start()/stop()/_monitor()/sync_to_network_time() are
out of scope -- systemd orchestration with sleeps and no distinct
branching logic beyond what's already covered via the getters.
"""

import time
from unittest.mock import MagicMock, patch

from src.controller.ptp import PTP, PTPRole


def _make_ptp(**attrs) -> PTP:
    ptp = PTP.__new__(PTP)
    ptp.logger = MagicMock()
    ptp.role = PTPRole.MASTER
    ptp.interface = "eth0"
    ptp.ptp4l_service = "ptp4l"
    ptp.phc2sys_service = "phc2sys"
    ptp.running = False
    ptp.status = "not running"
    ptp.last_sync_time = None
    ptp.last_offset = None
    ptp.last_freq = None
    ptp.ptp_buffer = []
    ptp.max_buffer_size = 100
    ptp.latest_ptp4l_offset = None
    ptp.latest_ptp4l_freq = None
    ptp.latest_phc2sys_offset_ns = None
    ptp.latest_phc2sys_freq = None
    for key, value in attrs.items():
        setattr(ptp, key, value)
    return ptp


# ---------------------------------------------------------------------------
# _parse_phc2sys_line -- the actual log-parsing logic
# ---------------------------------------------------------------------------

class TestParsePhc2sysLine:
    def test_parses_sys_offset(self):
        ptp = _make_ptp()
        line = "phc2sys[123.456]: sys offset -450 s2 freq +1234 delay 500"
        ptp._parse_phc2sys_line(line)
        assert ptp.latest_phc2sys_offset_ns == -450.0
        assert ptp.last_offset == -450.0
        assert ptp.status == "synchronized"
        assert ptp.last_sync_time is not None

    def test_parses_s2_freq(self):
        ptp = _make_ptp()
        line = "phc2sys[123.456]: sys offset 10 s2 freq -5678 delay 500"
        ptp._parse_phc2sys_line(line)
        assert ptp.latest_phc2sys_freq == -5678

    def test_adds_one_buffer_entry_for_a_line_with_both_offset_and_freq(self):
        """Both branches fire on the same line; the freq branch's own
        'add if no recent entry' check must not double up the offset
        branch's unconditional _add_buffer_entry call."""
        ptp = _make_ptp()
        ptp._parse_phc2sys_line("phc2sys[1.0]: sys offset 10 s2 freq 20 delay 500")
        assert len(ptp.ptp_buffer) == 1

    def test_freq_only_line_adds_buffer_entry_when_none_recent(self):
        ptp = _make_ptp()
        ptp._parse_phc2sys_line("phc2sys[1.0]: s2 freq 20 delay 500")
        assert len(ptp.ptp_buffer) == 1

    def test_freq_only_line_skips_buffer_entry_when_one_was_just_added(self):
        ptp = _make_ptp()
        ptp.ptp_buffer.append(
            {"timestamp": time.time(), "phc2sys_freq": 1, "phc2sys_offset_ns": 1}
        )
        ptp._parse_phc2sys_line("phc2sys[1.0]: s2 freq 20 delay 500")
        assert len(ptp.ptp_buffer) == 1  # not appended again

    def test_error_line_sets_error_status(self):
        ptp = _make_ptp(status="synchronized")
        line = "phc2sys[1.0]: could not create clock: Error opening device"
        ptp._parse_phc2sys_line(line)
        assert ptp.status == "error"

    def test_blank_line_is_a_no_op(self):
        ptp = _make_ptp()
        ptp._parse_phc2sys_line("   ")
        assert ptp.ptp_buffer == []

    def test_line_without_a_number_does_not_raise(self):
        ptp = _make_ptp()
        ptp._parse_phc2sys_line("phc2sys[1.0]: sys offset unavailable")
        assert ptp.latest_phc2sys_offset_ns is None


class TestAddBufferEntry:
    def test_appends_current_latest_values(self):
        ptp = _make_ptp(latest_phc2sys_freq=42, latest_phc2sys_offset_ns=-7)
        ptp._add_buffer_entry(123.0)
        assert ptp.ptp_buffer == [
            {"timestamp": 123.0, "phc2sys_freq": 42, "phc2sys_offset_ns": -7}
        ]

    def test_trims_oldest_entry_past_max_buffer_size(self):
        ptp = _make_ptp(max_buffer_size=3)
        for i in range(3):
            ptp._add_buffer_entry(float(i))
        ptp._add_buffer_entry(99.0)
        assert len(ptp.ptp_buffer) == 3
        assert ptp.ptp_buffer[0]["timestamp"] == 1.0  # entry 0.0 was dropped
        assert ptp.ptp_buffer[-1]["timestamp"] == 99.0


class TestCheckPtpOffsets:
    def test_no_op_when_values_not_yet_populated(self):
        ptp = _make_ptp()
        ptp._check_ptp_offsets()  # must not raise
        ptp.logger.warning.assert_not_called()

    def test_warns_when_offset_exceeds_threshold(self):
        ptp = _make_ptp(latest_phc2sys_offset_ns=6000, latest_phc2sys_freq=0)
        ptp._check_ptp_offsets()
        ptp.logger.warning.assert_called_once()

    def test_no_warning_within_threshold(self):
        ptp = _make_ptp(latest_phc2sys_offset_ns=100, latest_phc2sys_freq=100)
        ptp._check_ptp_offsets()
        ptp.logger.warning.assert_not_called()


class TestGetPtpBuffer:
    def test_returns_a_copy_of_the_full_buffer(self):
        ptp = _make_ptp()
        ptp.ptp_buffer = [{"timestamp": 1.0}, {"timestamp": 2.0}]
        result = ptp.get_ptp_buffer()
        assert result == ptp.ptp_buffer
        assert result is not ptp.ptp_buffer  # copy, not the same list object

    def test_max_entries_slices_from_the_end(self):
        ptp = _make_ptp()
        ptp.ptp_buffer = [{"timestamp": float(i)} for i in range(5)]
        result = ptp.get_ptp_buffer(max_entries=2)
        assert result == [{"timestamp": 3.0}, {"timestamp": 4.0}]


class TestIsSynchronizing:
    def test_true_within_timeout_of_last_sync(self):
        ptp = _make_ptp(last_sync_time=time.time())
        assert ptp.is_synchronizing(timeout=5) is True

    def test_false_when_last_sync_too_old(self):
        ptp = _make_ptp(last_sync_time=time.time() - 100)
        assert ptp.is_synchronizing(timeout=5) is False

    def test_false_when_never_synced(self):
        ptp = _make_ptp(last_sync_time=None)
        assert ptp.is_synchronizing() is False


# ---------------------------------------------------------------------------
# subprocess-mocked getters
# ---------------------------------------------------------------------------

class TestGetServiceStatus:
    def test_returns_stripped_stdout(self):
        ptp = _make_ptp()
        with patch(
            "src.controller.ptp.subprocess.run",
            return_value=MagicMock(stdout="active\n"),
        ):
            assert ptp._get_service_status("ptp4l") == "active"


class TestGetNtpStatus:
    def test_parses_enabled_synchronized_and_system_time(self):
        ptp = _make_ptp()
        responses = [
            MagicMock(stdout="NTP=yes\n"),
            MagicMock(stdout="NTPSynchronized=yes\n"),
            MagicMock(stdout="TimeUSec=1700000000000000\n"),
        ]
        with patch("src.controller.ptp.subprocess.run", side_effect=responses):
            status = ptp.get_ntp_status()

        assert status["ntp_enabled"] is True
        assert status["ntp_synchronized"] is True
        assert status["system_time"] == 1700000000000000 / 1_000_000
        assert status["role"] == "master"

    def test_disabled_and_unparseable_time_falls_back_gracefully(self):
        ptp = _make_ptp()
        responses = [
            MagicMock(stdout="NTP=no\n"),
            MagicMock(stdout="NTPSynchronized=no\n"),
            MagicMock(stdout="TimeUSec=\n"),
        ]
        with patch("src.controller.ptp.subprocess.run", side_effect=responses):
            status = ptp.get_ntp_status()

        assert status["ntp_enabled"] is False
        assert status["ntp_synchronized"] is False
        assert status["system_time"] is None


class TestGetStatus:
    def test_aggregates_service_and_ntp_status(self):
        ptp = _make_ptp(status="synchronized", last_offset=10.0)
        with patch.object(
            ptp, "_get_service_status", side_effect=["active", "active"]
        ), patch.object(ptp, "get_ntp_status", return_value={"ntp_enabled": True}):
            result = ptp.get_status()

        assert result["role"] == "master"
        assert result["ptp4l_service"] == "active"
        assert result["phc2sys_service"] == "active"
        assert result["ntp_status"] == {"ntp_enabled": True}


class TestGetServiceLogs:
    def test_no_service_name_returns_both_services(self):
        ptp = _make_ptp()
        with patch.object(
            ptp, "_get_service_logs", side_effect=["ptp4l log", "phc2sys log"]
        ):
            logs = ptp.get_service_logs()
        assert logs == {"ptp4l": "ptp4l log", "phc2sys": "phc2sys log"}

    def test_specific_service_name_returns_just_that_log(self):
        ptp = _make_ptp()
        with patch.object(
            ptp, "_get_service_logs", return_value="just ptp4l"
        ) as mock_logs:
            result = ptp.get_service_logs(service_name="ptp4l", lines=5)
        mock_logs.assert_called_once_with("ptp4l", 5)
        assert result == "just ptp4l"
