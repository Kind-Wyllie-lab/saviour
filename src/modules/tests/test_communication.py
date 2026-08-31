"""
Tests for src/modules/communication.py — specifically the heartbeat-ack
watchdog and the reconnect path.

Communication.__init__ builds a real zmq.Context and two real sockets, which
we don't want in a unit test, so instances are built via __new__ and the
handful of attributes each test needs are set explicitly (same pattern as
test_network.py).

Regression focus: the ack watchdog used to spawn a thread that called
cleanup() -> command_socket.close() while the listener thread was blocked in
recv_string() on that same socket, which aborts the process with a libzmq
signaler assertion (src/signaler.cpp:238). The watchdog must now only raise a
flag that the listener thread consumes in-thread.
"""

import threading
from unittest.mock import MagicMock

import pytest

from src.modules.communication import Communication


def _make_comm(*, listener_running=True, threshold=4) -> Communication:
    comm = Communication.__new__(Communication)
    comm.logger = MagicMock()
    comm.config = MagicMock()
    comm.config.get.side_effect = lambda key, default=None: default
    comm.facade = MagicMock()
    comm.facade.get_module_id.return_value = "camera_test"
    comm.facade.get_module_name.return_value = "TestCam"

    comm.command_listener_running = listener_running
    comm.command_thread = None
    comm.controller_ip = "192.168.1.1"
    comm.controller_port = 5353

    comm.connection_attempts = 0
    comm.max_connection_attempts = 5
    comm.connection_delay = 5
    comm.last_connection_time = None

    comm._ack_lock = threading.Lock()
    comm.last_ack_time = None
    comm.consecutive_missed_acks = 0
    comm.has_received_ack = True
    comm._MISSED_ACK_THRESHOLD = threshold

    comm._reconnect_lock = threading.Lock()
    comm._send_lock = threading.Lock()
    comm._reconnect_requested = threading.Event()

    comm._monitor_socket = None
    comm._monitor_thread = None
    comm._monitor_running = False

    comm.context = MagicMock()
    comm.command_socket = MagicMock()
    comm.status_socket = MagicMock()
    return comm


# ---------------------------------------------------------------------------
# notify_heartbeat_sent / the watchdog
# ---------------------------------------------------------------------------

class TestAckWatchdog:
    def test_below_threshold_does_nothing(self):
        comm = _make_comm(threshold=4)
        for _ in range(3):
            comm.notify_heartbeat_sent()
        assert comm.consecutive_missed_acks == 3
        assert not comm._reconnect_requested.is_set()

    def test_threshold_sets_flag_not_a_teardown(self, monkeypatch):
        comm = _make_comm(threshold=4)
        # If the old behaviour ever came back, this would be a thread that
        # calls cleanup(); assert cleanup is never touched by the watchdog.
        monkeypatch.setattr(comm, "cleanup", MagicMock(side_effect=AssertionError))

        for _ in range(4):
            comm.notify_heartbeat_sent()

        assert comm._reconnect_requested.is_set()
        # counter is reset so we don't re-fire every subsequent heartbeat
        assert comm.consecutive_missed_acks == 0
        comm.command_socket.close.assert_not_called()

    def test_no_misses_counted_before_first_ack(self):
        comm = _make_comm(threshold=2)
        comm.has_received_ack = False
        for _ in range(5):
            comm.notify_heartbeat_sent()
        assert comm.consecutive_missed_acks == 0
        assert not comm._reconnect_requested.is_set()

    def test_ack_resets_counter_and_clears_nothing_else(self):
        comm = _make_comm(threshold=4)
        comm.notify_heartbeat_sent()
        comm.notify_heartbeat_sent()
        assert comm.consecutive_missed_acks == 2
        comm._on_heartbeat_ack()
        assert comm.consecutive_missed_acks == 0

    def test_falls_back_to_scheduler_when_listener_not_running(self, monkeypatch):
        comm = _make_comm(listener_running=False, threshold=2)
        sched = MagicMock()
        monkeypatch.setattr(comm, "_schedule_reconnection", sched)
        for _ in range(2):
            comm.notify_heartbeat_sent()
        sched.assert_called_once()
        assert not comm._reconnect_requested.is_set()

    def test_threshold_is_config_driven(self):
        cfg = MagicMock()
        cfg.get.side_effect = lambda k, d=None: (
            7 if k == "communication.missed_ack_threshold" else d
        )
        comm = Communication(config=cfg)
        try:
            assert comm._MISSED_ACK_THRESHOLD == 7
        finally:
            comm.context.destroy(linger=0)

    def test_threshold_defaults_to_four(self):
        cfg = MagicMock()
        cfg.get.side_effect = lambda k, d=None: d
        comm = Communication(config=cfg)
        try:
            assert comm._MISSED_ACK_THRESHOLD == 4
        finally:
            comm.context.destroy(linger=0)


# ---------------------------------------------------------------------------
# _reconnect_from_listener
# ---------------------------------------------------------------------------

class TestReconnectFromListener:
    def test_rebuilds_command_socket_only(self, monkeypatch):
        comm = _make_comm()
        monkeypatch.setattr(comm, "_start_dealer_monitor", MagicMock())
        old_cmd = comm.command_socket
        old_status = comm.status_socket
        new_sock = MagicMock()
        comm.context.socket.return_value = new_sock

        comm._reconnect_from_listener()

        old_cmd.close.assert_called_once()
        assert comm.command_socket is new_sock
        new_sock.connect.assert_called_once_with("tcp://192.168.1.1:5555")
        new_sock.send.assert_called_once_with(b"hello")
        # status socket and context are deliberately left intact
        assert comm.status_socket is old_status
        old_status.close.assert_not_called()
        comm.context.term.assert_not_called()
        comm.context.destroy.assert_not_called()

    def test_resets_missed_ack_counter_and_flag(self, monkeypatch):
        comm = _make_comm()
        monkeypatch.setattr(comm, "_start_dealer_monitor", MagicMock())
        comm.consecutive_missed_acks = 9
        comm._reconnect_requested.set()

        comm._reconnect_from_listener()

        assert comm.consecutive_missed_acks == 0
        assert not comm._reconnect_requested.is_set()

    def test_skips_when_reconnect_already_in_progress(self, monkeypatch):
        comm = _make_comm()
        start_mon = MagicMock()
        monkeypatch.setattr(comm, "_start_dealer_monitor", start_mon)
        comm._reconnect_lock.acquire()
        try:
            comm._reconnect_from_listener()
        finally:
            comm._reconnect_lock.release()
        start_mon.assert_not_called()
        comm.command_socket.close.assert_not_called()

    def test_skips_when_no_controller_ip(self, monkeypatch):
        comm = _make_comm()
        comm.controller_ip = None
        start_mon = MagicMock()
        monkeypatch.setattr(comm, "_start_dealer_monitor", start_mon)
        comm._reconnect_from_listener()
        start_mon.assert_not_called()

    def test_survives_close_raising(self, monkeypatch):
        comm = _make_comm()
        monkeypatch.setattr(comm, "_start_dealer_monitor", MagicMock())
        comm.command_socket.close.side_effect = RuntimeError("already closed")
        new_sock = MagicMock()
        comm.context.socket.return_value = new_sock
        comm._reconnect_from_listener()  # must not raise
        assert comm.command_socket is new_sock
        new_sock.send.assert_called_once_with(b"hello")

    def test_releases_lock_even_on_failure(self, monkeypatch):
        comm = _make_comm()
        monkeypatch.setattr(comm, "_start_dealer_monitor",
                            MagicMock(side_effect=RuntimeError("boom")))
        comm._reconnect_from_listener()
        assert comm._reconnect_lock.acquire(blocking=False)
        comm._reconnect_lock.release()


# ---------------------------------------------------------------------------
# listen_for_commands consumes the flag on its own thread
# ---------------------------------------------------------------------------

class TestListenerConsumesFlag:
    def test_listener_loop_calls_reconnect_when_flag_set(self, monkeypatch):
        comm = _make_comm()
        reconnect = MagicMock()
        monkeypatch.setattr(comm, "_reconnect_from_listener", reconnect)

        # Drive exactly one loop iteration: flag is set, so the loop should
        # call _reconnect_from_listener and then we stop it.
        comm._reconnect_requested.set()

        def fake_reconnect():
            comm.command_listener_running = False  # end the loop after this pass
        reconnect.side_effect = fake_reconnect

        comm.listen_for_commands()

        reconnect.assert_called_once()
        comm.command_socket.recv_string.assert_not_called()

    def test_connection_error_reconnects_in_thread(self, monkeypatch):
        comm = _make_comm()
        reconnect = MagicMock(side_effect=lambda: setattr(
            comm, "command_listener_running", False))
        monkeypatch.setattr(comm, "_reconnect_from_listener", reconnect)
        sched = MagicMock()
        monkeypatch.setattr(comm, "_schedule_reconnection", sched)
        monkeypatch.setattr("src.modules.communication.time.sleep", lambda *_: None)

        # recv raises a connection-level error while the listener is still
        # meant to be running -> the except branch should reconnect in-thread.
        comm.command_socket.recv_string.side_effect = RuntimeError("Connection refused")

        comm.listen_for_commands()

        reconnect.assert_called_once()
        sched.assert_not_called()  # old out-of-thread path is no longer used here


# ---------------------------------------------------------------------------
# cleanup coordinates with the listener reconnect
# ---------------------------------------------------------------------------

class TestCleanupCoordination:
    def test_cleanup_clears_pending_reconnect_and_takes_lock(self, monkeypatch):
        comm = _make_comm()
        comm._reconnect_requested.set()
        seen = {}

        def fake_body():
            seen["locked"] = comm._reconnect_lock.locked()

        monkeypatch.setattr(comm, "_cleanup_locked", fake_body)
        comm.cleanup()

        assert not comm._reconnect_requested.is_set()
        assert seen["locked"] is True
        # lock is released again afterwards
        assert comm._reconnect_lock.acquire(blocking=False)
        comm._reconnect_lock.release()

    def test_cleanup_body_runs_even_if_it_raises(self, monkeypatch):
        comm = _make_comm()
        monkeypatch.setattr(comm, "_cleanup_locked",
                            MagicMock(side_effect=RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            comm.cleanup()
        # lock must not be left held
        assert comm._reconnect_lock.acquire(blocking=False)
        comm._reconnect_lock.release()
