"""
Tests for src/modules/health.py.

Health.__init__ has no real side effects (just config + a monotonic
timestamp), so it's constructed directly with facade assigned afterward,
matching the real Module wiring (self.health.facade = self.facade). The
heartbeat loop is exercised for exactly one iteration by making the mocked
time.sleep flip heartbeats_active off, rather than letting the real loop
run.
"""

from unittest.mock import MagicMock, patch

from src.modules.health import Health


def _make_config(**overrides) -> MagicMock:
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: overrides.get(key, default)
    return cfg


def _make_health(**config_overrides) -> Health:
    health = Health(_make_config(**config_overrides))
    health.facade = MagicMock()
    return health


# ---------------------------------------------------------------------------
# get_health
# ---------------------------------------------------------------------------

class TestGetHealth:
    def test_assembles_snapshot_from_psutil_and_facade(self):
        health = _make_health()
        health.facade.get_ptp_status.return_value = {
            "ptp4l_offset_ns": 100, "ptp4l_freq": 200,
            "phc2sys_offset_ns": 300, "phc2sys_freq": 400,
        }
        health.facade.get_recording_status.return_value = True
        health.facade.get_saviour_version.return_value = "1.2.3"

        mem = MagicMock(percent=42.0, total=8 * 1024**3)
        disk = MagicMock(percent=55.0, used=50 * 1024**3, total=100 * 1024**3)

        with patch("src.modules.health.psutil.virtual_memory", return_value=mem), \
             patch("src.modules.health.psutil.disk_usage", return_value=disk), \
             patch("src.modules.health.psutil.cpu_percent", return_value=12.5), \
             patch.object(health, "get_cpu_temp", return_value=45.6):
            result = health.get_health()

        assert result["cpu_usage"] == 12.5
        assert result["cpu_temp"] == 45.6
        assert result["memory_usage"] == 42.0
        assert result["memory_total_gb"] == 8.0
        assert result["disk_space"] == 55.0
        assert result["disk_used_gb"] == 50.0
        assert result["disk_total_gb"] == 100.0
        assert result["ptp4l_offset_ns"] == 100
        assert result["phc2sys_freq"] == 400
        assert result["recording"] is True
        assert result["version"] == "1.2.3"
        assert "uptime" in result


class TestGetCpuTemp:
    def test_parses_vcgencmd_output(self):
        health = _make_health()
        with patch("src.modules.health.os.popen") as mock_popen:
            mock_popen.return_value.readline.return_value = "temp=45.6'C\n"
            assert health.get_cpu_temp() == 45.6

    def test_returns_none_on_unparseable_output(self):
        health = _make_health()
        with patch("src.modules.health.os.popen") as mock_popen:
            mock_popen.return_value.readline.return_value = "command not found\n"
            assert health.get_cpu_temp() is None


# ---------------------------------------------------------------------------
# start_heartbeats / stop_heartbeats / cleanup
# ---------------------------------------------------------------------------

class TestStartHeartbeats:
    def test_already_active_returns_false(self):
        health = _make_health()
        health.heartbeats_active = True
        with patch("src.modules.health.threading.Thread") as mock_thread:
            assert health.start_heartbeats() is False
        mock_thread.assert_not_called()

    def test_no_controller_ip_returns_false(self):
        health = _make_health()
        health.facade.get_controller_ip.return_value = None
        assert health.start_heartbeats() is False
        assert health.heartbeats_active is False

    def test_success_spawns_daemon_thread(self):
        health = _make_health()
        health.facade.get_controller_ip.return_value = "10.0.0.1"
        with patch("src.modules.health.threading.Thread") as mock_thread:
            result = health.start_heartbeats()

        assert result is True
        assert health.heartbeats_active is True
        kwargs = mock_thread.call_args.kwargs
        assert kwargs["target"] == health._heartbeat_loop
        assert kwargs["daemon"] is True
        mock_thread.return_value.start.assert_called_once()


class TestHeartbeatLoop:
    def _run_one_iteration(self, health):
        """Let the loop body run exactly once by having the mocked
        time.sleep (the last call each iteration) turn the flag off."""
        def _stop(*_a, **_k):
            health.heartbeats_active = False
        with patch("src.modules.health.time.sleep", side_effect=_stop):
            health._heartbeat_loop()

    def test_sends_heartbeat_with_type_field(self):
        health = _make_health(**{"module.heartbeat_interval": 0})
        health.heartbeats_active = True
        health.facade.get_controller_ip.return_value = "10.0.0.1"
        with patch.object(health, "get_health", return_value={"cpu_usage": 1.0}):
            self._run_one_iteration(health)

        sent = health.facade.send_status.call_args[0][0]
        assert sent["type"] == "heartbeat"
        assert sent["cpu_usage"] == 1.0
        health.facade.notify_heartbeat_sent.assert_called_once()

    def test_stops_when_controller_ip_disappears(self):
        health = _make_health(**{"module.heartbeat_interval": 0})
        health.heartbeats_active = True
        health.facade.get_controller_ip.return_value = None
        self._run_one_iteration(health)
        assert health.heartbeats_active is False
        health.facade.send_status.assert_not_called()

    def test_exception_sending_status_stops_heartbeats(self):
        health = _make_health(**{"module.heartbeat_interval": 0})
        health.heartbeats_active = True
        health.facade.get_controller_ip.return_value = "10.0.0.1"
        health.facade.send_status.side_effect = RuntimeError("comms down")
        with patch.object(health, "get_health", return_value={}):
            self._run_one_iteration(health)
        assert health.heartbeats_active is False


class TestStopHeartbeats:
    def test_no_thread_yet_just_clears_flag(self):
        health = _make_health()
        health.heartbeats_active = True
        health.stop_heartbeats()  # must not raise
        assert health.heartbeats_active is False

    def test_joins_alive_thread(self):
        health = _make_health()
        health.heartbeats_active = True
        thread = MagicMock()
        # Alive when first checked (enters the join branch), stopped by the
        # time the post-join check runs (no "did not stop cleanly" warning).
        thread.is_alive.side_effect = [True, False]
        health.heartbeat_thread = thread
        health.stop_heartbeats()
        thread.join.assert_called_once_with(timeout=1.0)

    def test_warns_if_thread_survives_join(self):
        health = _make_health()
        thread = MagicMock()
        thread.is_alive.return_value = True
        health.heartbeat_thread = thread
        health.stop_heartbeats()  # must not raise despite thread staying "alive"


class TestCleanup:
    def test_delegates_to_stop_heartbeats(self):
        health = _make_health()
        with patch.object(health, "stop_heartbeats") as mock_stop:
            health.cleanup()
        mock_stop.assert_called_once()
