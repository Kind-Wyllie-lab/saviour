"""
Tests for src/controller/health.py.

Covers the module health state machine (online/suspected/offline
transitions, heartbeat debounce, force-offline guard), the getters/summary
aggregation, and the probe sequence (subprocess ping + TCP check mocked).
The monitor_health() background loop itself is out of scope -- its
per-cycle branching is exercised indirectly through the state-transition
methods it calls (_enter_suspicion, _probe_module, _confirm_module_offline,
_mark_module_online), which are tested directly here.
"""

import time
from collections import deque
from unittest.mock import MagicMock, patch

import pytest

from src.controller.health import Health


def _make_config(**overrides) -> MagicMock:
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: overrides.get(key, default)
    return cfg


def _make_health(**config_overrides) -> tuple:
    health = Health(_make_config(**config_overrides))
    facade = MagicMock()
    facade.is_module_recording.return_value = False
    health.facade = facade
    return health, facade


def _seed_module(health: Health, module_id: str, **overrides) -> dict:
    """Insert a minimal module_health record directly, bypassing the real
    update_module_health() flow for tests that only care about a later
    state transition."""
    record = {
        "last_heartbeat": time.time(),
        "status": "online",
        "offline_since": None,
        "suspected_since": None,
        "probe_count": 0,
        "last_probe_time": None,
        "last_confirmed_online": time.time(),
        "pending_online_count": 0,
        "cpu_usage": None, "cpu_temp": None, "memory_usage": None,
        "disk_space": None, "ptp4l_offset_ns": None, "ptp4l_freq": None,
        "phc2sys_freq": None, "phc2sys_offset_ns": None,
        "last_ptp_restart": time.time(), "ptp_restarts": 1,
    }
    record.update(overrides)
    health.module_health[module_id] = record
    return record


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_suspicion_timeout_auto_adjusts_when_not_below_heartbeat_timeout(self):
        health, _facade = _make_health(**{
            "health.heartbeat_timeout": 60, "health.suspicion_timeout": 60,
        })
        assert health.suspicion_timeout == 40  # 2/3 of 60

    def test_suspicion_timeout_left_alone_when_already_sane(self):
        health, _facade = _make_health(**{
            "health.heartbeat_timeout": 90, "health.suspicion_timeout": 60,
        })
        assert health.suspicion_timeout == 60


# ---------------------------------------------------------------------------
# touch_heartbeat / remove_module / force_offline
# ---------------------------------------------------------------------------

class TestTouchHeartbeat:
    def test_updates_last_heartbeat_for_known_module(self):
        health, _facade = _make_health()
        _seed_module(health, "cam1", last_heartbeat=0)
        health.touch_heartbeat("cam1")
        assert health.module_health["cam1"]["last_heartbeat"] > 0

    def test_unknown_module_is_a_no_op(self):
        health, _facade = _make_health()
        health.touch_heartbeat("ghost")  # must not raise
        assert "ghost" not in health.module_health

    def test_force_offlined_module_ignores_heartbeat(self):
        health, facade = _make_health()
        _seed_module(health, "cam1", last_heartbeat=0, status="offline")
        health._force_offline_ids.add("cam1")
        health.touch_heartbeat("cam1")
        assert health.module_health["cam1"]["last_heartbeat"] == 0
        facade.on_status_change.assert_not_called()

    def test_offline_module_marked_online_on_proof_of_life(self):
        health, facade = _make_health()
        _seed_module(health, "cam1", status="offline")
        health.touch_heartbeat("cam1")
        assert health.module_health["cam1"]["status"] == "online"
        facade.on_status_change.assert_called_once_with("cam1", "online")


class TestRemoveModule:
    def test_removes_health_record_and_clears_force_offline_flag(self):
        health, _facade = _make_health()
        _seed_module(health, "cam1")
        health._force_offline_ids.add("cam1")
        health.remove_module("cam1")
        assert "cam1" not in health.module_health
        assert "cam1" not in health._force_offline_ids

    def test_unknown_module_is_a_no_op(self):
        health, _facade = _make_health()
        health.remove_module("ghost")  # must not raise


class TestForceOffline:
    def test_marks_offline_zeroes_heartbeat_and_sets_guard(self):
        health, facade = _make_health()
        _seed_module(health, "cam1", status="online")
        health.force_offline("cam1")
        assert health.module_health["cam1"]["status"] == "offline"
        assert health.module_health["cam1"]["last_heartbeat"] == 0
        assert "cam1" in health._force_offline_ids
        facade.on_status_change.assert_called_once_with("cam1", "offline")


# ---------------------------------------------------------------------------
# update_module_health
# ---------------------------------------------------------------------------

class TestUpdateModuleHealth:
    def test_new_module_creates_full_online_record(self):
        health, _facade = _make_health()
        ok = health.update_module_health("cam1", {"cpu_usage": 12.0})
        assert ok is True
        assert health.module_health["cam1"]["status"] == "online"
        assert health.module_health["cam1"]["ptp_restarts"] == 1

    def test_existing_module_updates_snapshot_fields(self):
        health, _facade = _make_health()
        health.update_module_health("cam1", {"cpu_usage": 10.0})
        health.update_module_health("cam1", {"cpu_usage": 55.0})
        assert health.module_health["cam1"]["cpu_usage"] == 55.0

    def test_recovering_module_needs_threshold_consecutive_heartbeats(self):
        health, facade = _make_health(**{"health.online_heartbeat_threshold": 2})
        _seed_module(health, "cam1", status="offline", pending_online_count=0)

        health.update_module_health("cam1", {})
        assert health.module_health["cam1"]["status"] == "offline"  # only 1/2 so far
        facade.on_status_change.assert_not_called()

        health.update_module_health("cam1", {})
        assert health.module_health["cam1"]["status"] == "online"  # 2/2
        facade.on_status_change.assert_called_once_with("cam1", "online")

    def test_exception_is_caught_and_returns_false(self):
        health, _facade = _make_health()
        with patch(
            "src.controller.health.ModuleHealthSnapshot.from_dict",
            side_effect=RuntimeError("bad payload"),
        ):
            assert health.update_module_health("cam1", {}) is False


class TestPtpHistory:
    """_record_ptp_sample (called from update_module_health) and
    export_ptp_history_csv -- the fleet-wide PTP-sync-over-time tracking
    added so an operator can plot a long unattended run's PTP quality."""

    def test_heartbeat_records_a_ptp_history_sample(self):
        health, _facade = _make_health()
        health.update_module_health("cam1", {"ptp4l_offset_ns": 120.0})
        history = health.module_health_history["cam1"]
        assert len(history) == 1
        assert history[0]["ptp4l_offset_ns"] == 120.0

    def test_multiple_heartbeats_accumulate_samples_in_order(self):
        health, _facade = _make_health()
        health.update_module_health("cam1", {"ptp4l_offset_ns": 100.0})
        health.update_module_health("cam1", {"ptp4l_offset_ns": 200.0})
        health.update_module_health("cam1", {"ptp4l_offset_ns": 300.0})
        history = health.module_health_history["cam1"]
        assert [s["ptp4l_offset_ns"] for s in history] == [100.0, 200.0, 300.0]

    def test_samples_older_than_retention_are_pruned(self):
        health, _facade = _make_health()
        now = time.time()
        # Seed one old sample directly (older than retention), then trigger
        # a real heartbeat -- the old one should be pruned on that append.
        old_ts = now - health._PTP_HISTORY_RETENTION_S - 10
        health.module_health_history["cam1"] = deque([
            {"timestamp": old_ts, "ptp4l_offset_ns": 1.0},
        ])
        _seed_module(health, "cam1")
        health.update_module_health("cam1", {"ptp4l_offset_ns": 999.0})
        history = health.module_health_history["cam1"]
        assert len(history) == 1
        assert history[0]["ptp4l_offset_ns"] == 999.0

    def test_export_csv_header_and_rows(self):
        health, _facade = _make_health()
        health.update_module_health("cam1", {
            "ptp4l_offset_ns": 50.0,
            "ptp4l_offset_ns_min": 10.0, "ptp4l_offset_ns_max": 90.0,
            "phc2sys_offset_ns": 5.0,
            "phc2sys_offset_ns_min": 1.0, "phc2sys_offset_ns_max": 9.0,
            "ptp4l_freq": -1234, "phc2sys_freq": -5678,
        })
        csv_text = "".join(health.export_ptp_history_csv())
        lines = csv_text.strip().splitlines()
        assert lines[0] == (
            "module_id,timestamp_utc,timestamp_epoch,"
            "ptp4l_offset_ns,ptp4l_offset_ns_min,ptp4l_offset_ns_max,"
            "phc2sys_offset_ns,phc2sys_offset_ns_min,phc2sys_offset_ns_max,"
            "ptp4l_freq,phc2sys_freq"
        )
        assert len(lines) == 2
        row = lines[1].split(",")
        assert row[0] == "cam1"
        assert row[3:] == [
            "50.0", "10.0", "90.0", "5.0", "1.0", "9.0", "-1234", "-5678",
        ]

    def test_export_csv_with_no_modules_is_just_the_header(self):
        health, _facade = _make_health()
        csv_text = "".join(health.export_ptp_history_csv())
        assert len(csv_text.strip().splitlines()) == 1

    def test_default_hours_excludes_samples_older_than_24h(self):
        health, _facade = _make_health()
        now = time.time()
        health.module_health_history["cam1"] = deque([
            {"timestamp": now - 25 * 3600, "ptp4l_offset_ns": 1.0},  # outside default
            {"timestamp": now - 1 * 3600, "ptp4l_offset_ns": 2.0},   # inside it
        ])
        csv_text = "".join(health.export_ptp_history_csv())  # default hours=24.0
        rows = csv_text.strip().splitlines()[1:]
        assert len(rows) == 1
        assert rows[0].split(",")[3] == "2.0"

    def test_hours_none_returns_entire_retained_history(self):
        health, _facade = _make_health()
        now = time.time()
        health.module_health_history["cam1"] = deque([
            {"timestamp": now - 7 * 24 * 3600, "ptp4l_offset_ns": 1.0},
            {"timestamp": now - 1 * 3600, "ptp4l_offset_ns": 2.0},
        ])
        csv_text = "".join(health.export_ptp_history_csv(hours=None))
        rows = csv_text.strip().splitlines()[1:]
        assert len(rows) == 2

    def test_custom_hours_window(self):
        health, _facade = _make_health()
        now = time.time()
        health.module_health_history["cam1"] = deque([
            {"timestamp": now - 3 * 3600, "ptp4l_offset_ns": 1.0},  # outside 2h
            {"timestamp": now - 1 * 3600, "ptp4l_offset_ns": 2.0},  # inside it
        ])
        csv_text = "".join(health.export_ptp_history_csv(hours=2.0))
        rows = csv_text.strip().splitlines()[1:]
        assert len(rows) == 1
        assert rows[0].split(",")[3] == "2.0"

    def test_export_csv_is_a_generator_not_a_prebuilt_string(self):
        """Regression guard for the streaming design -- the whole point is
        that nothing builds the full CSV in memory before the caller
        (web.py's download route) can start sending bytes."""
        health, _facade = _make_health()
        result = health.export_ptp_history_csv()
        assert hasattr(result, "__next__")


class TestModuleDiscovery:
    def test_new_module_added_as_offline_pending_first_heartbeat(self):
        health, _facade = _make_health()
        module = MagicMock(id="cam1")
        health.module_discovery(module)
        assert health.module_health["cam1"]["status"] == "offline"
        assert health.module_health["cam1"]["last_heartbeat"] == 0

    def test_rediscovery_clears_force_offline_guard_without_resetting_record(self):
        health, _facade = _make_health()
        _seed_module(health, "cam1", status="online", cpu_usage=42.0)
        health._force_offline_ids.add("cam1")

        health.module_discovery(MagicMock(id="cam1"))

        assert "cam1" not in health._force_offline_ids
        assert health.module_health["cam1"]["cpu_usage"] == 42.0  # untouched


class TestModuleIdChanged:
    def test_moves_health_and_history_to_new_key(self):
        health, _facade = _make_health()
        _seed_module(health, "old_id")
        health.module_health_history["old_id"] = [{"a": 1}]

        health.module_id_changed("old_id", "new_id")

        assert "old_id" not in health.module_health
        assert "new_id" in health.module_health
        assert health.module_health_history["new_id"] == [{"a": 1}]


# ---------------------------------------------------------------------------
# Getters
# ---------------------------------------------------------------------------

class TestGetters:
    def test_get_module_health_history_empty_for_unknown_module(self):
        health, _facade = _make_health()
        assert health.get_module_health_history("ghost") == []

    def test_get_module_health_history_respects_limit(self):
        health, _facade = _make_health()
        health.module_health_history["cam1"] = [{"n": i} for i in range(5)]
        assert health.get_module_health_history("cam1", limit=2) == [{"n": 3}, {"n": 4}]

    def test_get_module_health_specific_vs_all(self):
        health, _facade = _make_health()
        _seed_module(health, "cam1")
        assert health.get_module_health("cam1")["status"] == "online"
        assert health.get_module_health("ghost") == {}
        assert "cam1" in health.get_module_health()

    def test_get_offline_and_online_modules(self):
        health, _facade = _make_health()
        _seed_module(health, "cam1", status="online")
        _seed_module(health, "cam2", status="offline")
        assert health.get_online_modules() == ["cam1"]
        assert health.get_offline_modules() == ["cam2"]

    def test_get_health_summary_averages_online_module_metrics(self):
        health, _facade = _make_health()
        _seed_module(
            health, "cam1", status="online", cpu_usage=10.0, cpu_temp=40.0,
            memory_usage=1.0, ptp4l_offset_ns=1.0, ptp4l_freq=1.0,
        )
        _seed_module(
            health, "cam2", status="online", cpu_usage=20.0, cpu_temp=50.0,
            memory_usage=1.0, ptp4l_offset_ns=1.0, ptp4l_freq=1.0,
        )
        _seed_module(health, "cam3", status="offline", cpu_usage=999.0)

        summary = health.get_health_summary()

        assert summary["total_modules"] == 3
        assert summary["online_modules"] == 2
        assert summary["offline_modules"] == 1
        assert summary["average_metrics"]["avg_cpu_usage"] == 15.0
        assert summary["average_metrics"]["avg_cpu_temp"] == 45.0

    def test_get_health_summary_skips_none_metrics_from_a_freshly_online_module(self):
        """Every field on ModuleHealthSnapshot (src/shared/health.py) defaults
        to None, and a freshly-online module commonly hasn't reported e.g.
        ptp4l_freq yet. get_health_summary()'s averaging now filters out None
        values rather than feeding them to sum(), so a module still missing
        some metrics doesn't crash the whole summary — it's just excluded
        from that metric's average. Reachable live: web.py's
        'get_health_summary' Socket.IO handler calls straight through to
        this with no try/except."""
        health, _facade = _make_health()
        _seed_module(health, "cam1", status="online", cpu_usage=50, ptp4l_freq=None)

        summary = health.get_health_summary()

        assert summary["average_metrics"]["avg_cpu_usage"] == 50
        assert "avg_ptp4l_freq" not in summary["average_metrics"]

    def test_get_health_summary_with_no_modules_has_empty_averages(self):
        health, _facade = _make_health()
        summary = health.get_health_summary()
        assert summary["average_metrics"] == {}

    def test_get_ptp_sync_returns_max_absolute_offset(self):
        health, _facade = _make_health()
        _seed_module(health, "cam1", ptp4l_offset_ns=-30)
        _seed_module(health, "cam2", ptp4l_offset_ns=45)
        assert health.get_ptp_sync() == 45

    def test_get_ptp_sync_treats_perfect_zero_offset_as_real_data(self):
        """`if ptp_sync is None` (not a truthiness check) so a genuine 0ns
        offset — a fully-synced module — is treated as real data, not as
        missing data."""
        health, _facade = _make_health()
        _seed_module(health, "cam1", ptp4l_offset_ns=0)
        assert health.get_ptp_sync() == 0


class TestClearAllHealth:
    def test_clears_health_and_history(self):
        health, _facade = _make_health()
        _seed_module(health, "cam1")
        health.module_health_history["cam1"] = [{"a": 1}]
        health.clear_all_health()
        assert health.module_health == {}
        assert health.module_health_history == {}


# ---------------------------------------------------------------------------
# mark_module_offline / module_rediscovered / handle_communication_test_response
# ---------------------------------------------------------------------------

class TestMarkModuleOffline:
    def test_transitions_online_module_to_offline(self):
        health, facade = _make_health()
        _seed_module(health, "cam1", status="online")
        health.mark_module_offline("cam1", reason="ping failed")
        assert health.module_health["cam1"]["status"] == "offline"
        facade.on_status_change.assert_called_once_with("cam1", "offline")

    def test_already_offline_is_a_no_op(self):
        health, facade = _make_health()
        _seed_module(health, "cam1", status="offline")
        health.mark_module_offline("cam1")
        facade.on_status_change.assert_not_called()

    def test_unknown_module_only_logs(self):
        health, facade = _make_health()
        health.mark_module_offline("ghost")  # must not raise
        facade.on_status_change.assert_not_called()


class TestModuleRediscovered:
    def test_probes_offline_module(self):
        health, facade = _make_health()
        _seed_module(health, "cam1", status="offline")
        facade.get_module_ip.return_value = "10.0.0.5"
        with patch("src.controller.health.subprocess.run") as mock_run, \
             patch.object(health, "_check_tcp_port", return_value=False):
            mock_run.return_value = MagicMock(returncode=1)
            health.module_rediscovered("cam1")
        assert health.module_health["cam1"]["probe_count"] == 1

    def test_online_module_is_not_probed(self):
        health, facade = _make_health()
        _seed_module(health, "cam1", status="online")
        health.module_rediscovered("cam1")
        facade.get_module_ip.assert_not_called()


class TestHandleCommunicationTestResponse:
    def test_success_marks_online(self):
        health, _facade = _make_health()
        _seed_module(health, "cam1", status="offline")
        health.handle_communication_test_response("cam1", True)
        assert health.module_health["cam1"]["status"] == "online"

    def test_success_when_already_online_does_not_refire_callback(self):
        health, facade = _make_health()
        _seed_module(health, "cam1", status="online")
        health.handle_communication_test_response("cam1", True)
        facade.on_status_change.assert_not_called()

    def test_failure_marks_offline(self):
        health, _facade = _make_health()
        _seed_module(health, "cam1", status="online")
        health.handle_communication_test_response("cam1", False)
        assert health.module_health["cam1"]["status"] == "offline"

    def test_unknown_module_only_logs(self):
        health, _facade = _make_health()
        health.handle_communication_test_response("ghost", True)  # must not raise


# ---------------------------------------------------------------------------
# Probe sequence -- subprocess/socket mocked
# ---------------------------------------------------------------------------

class TestCheckTcpPort:
    def test_open_port_returns_true(self):
        health, _facade = _make_health()
        with patch("src.controller.health._socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock()
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            assert health._check_tcp_port("10.0.0.5") is True

    def test_closed_port_returns_false(self):
        health, _facade = _make_health()
        with patch(
            "src.controller.health._socket.create_connection",
            side_effect=OSError("refused"),
        ):
            assert health._check_tcp_port("10.0.0.5") is False


class TestProbeModule:
    def test_ping_success_sends_get_status(self):
        health, facade = _make_health()
        _seed_module(health, "cam1")
        facade.get_module_ip.return_value = "10.0.0.5"

        with patch("src.controller.health.subprocess.run") as mock_run, \
             patch.object(health, "_check_tcp_port", return_value=True):
            mock_run.return_value = MagicMock(returncode=0)
            result = health._probe_module("cam1")

        assert result == {"ping": True, "tcp_port": True, "status_cmd_sent": True}
        facade.send_command.assert_called_once_with("cam1", "get_status", {})

    def test_ping_failure_skips_get_status(self):
        health, facade = _make_health()
        _seed_module(health, "cam1")
        facade.get_module_ip.return_value = "10.0.0.5"

        with patch("src.controller.health.subprocess.run") as mock_run, \
             patch.object(health, "_check_tcp_port", return_value=False):
            mock_run.return_value = MagicMock(returncode=1)
            result = health._probe_module("cam1")

        assert result == {"ping": False, "tcp_port": False, "status_cmd_sent": False}
        facade.send_command.assert_not_called()

    def test_confirms_offline_after_max_attempts_with_no_response(self):
        health, facade = _make_health(**{"health.max_probe_attempts": 2})
        _seed_module(health, "cam1", probe_count=1, status="suspected")
        facade.get_module_ip.return_value = "10.0.0.5"

        with patch("src.controller.health.subprocess.run") as mock_run, \
             patch.object(health, "_check_tcp_port", return_value=False):
            mock_run.return_value = MagicMock(returncode=1)
            health._probe_module("cam1")  # 2nd attempt -- exhausts max_probe_attempts

        assert health.module_health["cam1"]["status"] == "offline"
        facade.on_status_change.assert_called_once_with("cam1", "offline")


# ---------------------------------------------------------------------------
# State transition internals
# ---------------------------------------------------------------------------

class TestConfirmModuleOffline:
    def test_fires_callback_and_sets_offline_since(self):
        health, facade = _make_health()
        _seed_module(health, "cam1", status="suspected")
        health._confirm_module_offline("cam1", 120.0)
        assert health.module_health["cam1"]["status"] == "offline"
        facade.on_status_change.assert_called_once_with("cam1", "offline")

    def test_does_not_refire_if_already_offline(self):
        health, facade = _make_health()
        _seed_module(health, "cam1", status="offline")
        health._confirm_module_offline("cam1", 120.0)
        facade.on_status_change.assert_not_called()


class TestMarkModuleOnlineInternal:
    def test_resets_offline_bookkeeping_and_fires_callback(self):
        health, facade = _make_health()
        _seed_module(
            health, "cam1", status="offline", offline_since=time.time() - 30,
            probe_count=2,
        )
        health._mark_module_online("cam1")
        record = health.module_health["cam1"]
        assert record["status"] == "online"
        assert record["offline_since"] is None
        assert record["probe_count"] == 0
        facade.on_status_change.assert_called_once_with("cam1", "online")


class TestEnterSuspicion:
    def test_transitions_to_suspected_and_probes(self):
        health, facade = _make_health()
        _seed_module(health, "cam1", status="online")
        facade.get_module_ip.return_value = "10.0.0.5"

        with patch("src.controller.health.subprocess.run") as mock_run, \
             patch.object(health, "_check_tcp_port", return_value=False):
            mock_run.return_value = MagicMock(returncode=1)
            health._enter_suspicion("cam1", 65.0)

        assert health.module_health["cam1"]["status"] == "suspected"
        assert health.module_health["cam1"]["probe_count"] == 1  # _probe_module ran


class TestCheckPtpHealth:
    # Defaults from health.py: 50us offset gate, 3 consecutive breaches before
    # acting, 600s post-restart grace, 900s healthy-window backoff reset.
    OVER = 60_000   # over the 50us restart threshold
    OK = 500        # comfortably within threshold

    def _run(self, health, n):
        for _ in range(n):
            health._check_ptp_health()

    def test_no_restart_when_within_threshold(self):
        health, facade = _make_health()
        _seed_module(
            health, "cam1",
            ptp4l_freq=-50_000, phc2sys_freq=-8_000, phc2sys_offset_ns=self.OK,
            ptp4l_offset_ns=self.OK, last_ptp_restart=0, ptp_restarts=1,
        )
        self._run(health, 5)
        facade.send_command.assert_not_called()

    def test_single_breach_does_not_restart(self):
        health, facade = _make_health()
        _seed_module(
            health, "cam1",
            ptp4l_freq=0, phc2sys_freq=0, phc2sys_offset_ns=0,
            ptp4l_offset_ns=self.OVER, last_ptp_restart=0, ptp_restarts=1,
        )
        health._check_ptp_health()
        facade.send_command.assert_not_called()
        assert health.module_health["cam1"]["ptp_breach_count"] == 1

    def test_restart_after_sustained_breach(self):
        health, facade = _make_health()
        _seed_module(
            health, "cam1",
            ptp4l_freq=0, phc2sys_freq=0, phc2sys_offset_ns=0,
            ptp4l_offset_ns=self.OVER, last_ptp_restart=0, ptp_restarts=1,
        )
        self._run(health, 3)
        facade.send_command.assert_called_once_with("cam1", "restart_ptp", {})
        assert health.module_health["cam1"]["ptp_restarts"] == 2
        assert health.module_health["cam1"]["ptp_breach_count"] == 0

    def test_grace_period_blocks_restart_right_after_a_previous_one(self):
        health, facade = _make_health()
        _seed_module(
            health, "cam1",
            ptp4l_freq=0, phc2sys_freq=0, phc2sys_offset_ns=0,
            ptp4l_offset_ns=self.OVER,
            last_ptp_restart=time.time(),  # inside the 600s grace window
            ptp_restarts=1,
        )
        self._run(health, 5)
        facade.send_command.assert_not_called()
        # breach counting doesn't even start while in grace
        assert health.module_health["cam1"].get("ptp_breach_count", 0) == 0

    def test_backoff_blocks_restart_within_window(self):
        health, facade = _make_health()
        _seed_module(
            health, "cam1",
            ptp4l_freq=0, phc2sys_freq=0, phc2sys_offset_ns=0,
            ptp4l_offset_ns=self.OVER,
            last_ptp_restart=time.time() - 700,  # past 600s grace...
            ptp_restarts=5,                      # ...but backoff is 2**5*60 = 1920s
        )
        self._run(health, 3)
        facade.send_command.assert_not_called()

    def test_one_bad_module_does_not_restart_a_healthy_one(self):
        """Regression: pre-2026-08-27 the function-scoped `reset_flag` meant a
        breach on cam_bad also triggered restart_ptp on cam_good."""
        health, facade = _make_health()
        _seed_module(
            health, "cam_bad",
            ptp4l_freq=0, phc2sys_freq=0, phc2sys_offset_ns=0,
            ptp4l_offset_ns=200_000, last_ptp_restart=0, ptp_restarts=1,
        )
        _seed_module(
            health, "cam_good",
            ptp4l_freq=-50_000, phc2sys_freq=-8_000, phc2sys_offset_ns=self.OK,
            ptp4l_offset_ns=self.OK, last_ptp_restart=0, ptp_restarts=1,
        )
        self._run(health, 3)
        facade.send_command.assert_called_once_with("cam_bad", "restart_ptp", {})

    def test_frequency_alone_does_not_trigger_restart(self):
        health, facade = _make_health()
        _seed_module(
            health, "cam1",
            ptp4l_freq=600_000, phc2sys_freq=600_000,  # over the warn line
            phc2sys_offset_ns=self.OK, ptp4l_offset_ns=self.OK,
            last_ptp_restart=0, ptp_restarts=1,
        )
        self._run(health, 5)
        facade.send_command.assert_not_called()

    def test_backoff_counter_resets_after_sustained_health(self):
        health, facade = _make_health()
        _seed_module(
            health, "cam1",
            ptp4l_freq=-50_000, phc2sys_freq=-8_000, phc2sys_offset_ns=self.OK,
            ptp4l_offset_ns=self.OK, last_ptp_restart=0, ptp_restarts=4,
            ptp_healthy_since=time.time() - 1000,  # healthy for > 900s
        )
        health._check_ptp_health()
        assert health.module_health["cam1"]["ptp_restarts"] == 1
        facade.send_command.assert_not_called()

    def test_breach_streak_clears_when_offset_recovers(self):
        health, facade = _make_health()
        rec = _seed_module(
            health, "cam1",
            ptp4l_freq=0, phc2sys_freq=0, phc2sys_offset_ns=0,
            ptp4l_offset_ns=self.OVER, last_ptp_restart=0, ptp_restarts=1,
        )
        self._run(health, 2)               # two breaches, not yet acted on
        assert rec["ptp_breach_count"] == 2
        rec["ptp4l_offset_ns"] = self.OK    # recovers before the 3rd
        health._check_ptp_health()
        assert rec["ptp_breach_count"] == 0
        facade.send_command.assert_not_called()

    def test_no_restart_while_recording_below_catastrophic(self):
        health, facade = _make_health()
        facade.is_module_recording.return_value = True
        _seed_module(
            health, "cam1",
            ptp4l_freq=0, phc2sys_freq=0, phc2sys_offset_ns=0,
            ptp4l_offset_ns=200_000,  # over the 50us gate, under the 1ms override
            last_ptp_restart=0, ptp_restarts=1,
        )
        self._run(health, 5)
        facade.send_command.assert_not_called()

    def test_restart_while_recording_when_offset_catastrophic(self):
        health, facade = _make_health()
        facade.is_module_recording.return_value = True
        _seed_module(
            health, "cam1",
            ptp4l_freq=0, phc2sys_freq=0, phc2sys_offset_ns=0,
            ptp4l_offset_ns=2_000_000,  # over the 1ms recording override
            last_ptp_restart=0, ptp_restarts=1,
        )
        self._run(health, 3)
        facade.send_command.assert_called_once_with("cam1", "restart_ptp", {})

    def test_recording_gate_can_be_disabled_by_config(self):
        health, facade = _make_health(
            **{"health.ptp_no_restart_while_recording": False}
        )
        facade.is_module_recording.return_value = True
        _seed_module(
            health, "cam1",
            ptp4l_freq=0, phc2sys_freq=0, phc2sys_offset_ns=0,
            ptp4l_offset_ns=self.OVER, last_ptp_restart=0, ptp_restarts=1,
        )
        self._run(health, 3)
        facade.send_command.assert_called_once_with("cam1", "restart_ptp", {})
