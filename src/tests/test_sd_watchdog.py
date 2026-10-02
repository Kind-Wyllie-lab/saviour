"""
Tests for src/shared/sd_watchdog.py (systemd watchdog keep-alive) and the
module-side keep-alive gate, Module._watchdog_alive.
"""

import os
import socket
import threading
import time
from unittest.mock import MagicMock

import pytest

from src.shared import sd_watchdog

HAS_UNIX_DGRAM = hasattr(socket, "AF_UNIX") and os.name == "posix"


@pytest.fixture
def notify_socket(tmp_path, monkeypatch):
    if not HAS_UNIX_DGRAM:
        pytest.skip("needs AF_UNIX datagram sockets")
    path = str(tmp_path / "notify.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(path)
    srv.settimeout(0.2)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    yield srv
    srv.close()


def _drain(srv, seconds):
    got, end = [], time.time() + seconds
    while time.time() < end:
        try:
            got.append(srv.recv(64).decode())
        except TimeoutError:
            pass
        except OSError:
            pass
    return got


def test_disabled_without_systemd_watchdog(monkeypatch):
    monkeypatch.delenv("WATCHDOG_USEC", raising=False)
    assert sd_watchdog.watchdog_interval_s() is None
    assert sd_watchdog.start() is None


def test_watchdog_for_another_pid_is_ignored(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "60000000")
    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid() + 1))
    assert sd_watchdog.watchdog_interval_s() is None


def test_interval_parsed(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "60000000")
    monkeypatch.delenv("WATCHDOG_PID", raising=False)
    assert sd_watchdog.watchdog_interval_s() == 60.0


def test_keepalive_sent_while_alive(notify_socket, monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "3000000")   # 3 s -> ping every 1 s
    monkeypatch.delenv("WATCHDOG_PID", raising=False)
    thread = sd_watchdog.start(logger=MagicMock())
    assert isinstance(thread, threading.Thread)
    try:
        got = _drain(notify_socket, 2.5)
    finally:
        thread.stop_event.set()
    assert got.count("WATCHDOG=1") >= 2


def test_keepalive_withheld_while_not_alive(notify_socket, monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "3000000")
    monkeypatch.delenv("WATCHDOG_PID", raising=False)
    state = {"ok": False}
    log = MagicMock()
    thread = sd_watchdog.start(
        lambda: (state["ok"], "heartbeat loop stalled"), logger=log)
    try:
        assert _drain(notify_socket, 2.2) == []
        assert log.error.call_count == 1      # logged once, not every period
        state["ok"] = True
        assert "WATCHDOG=1" in _drain(notify_socket, 1.5)
    finally:
        thread.stop_event.set()


# --------------------------------------------------------------------------- #
# Module._watchdog_alive                                                       #
# --------------------------------------------------------------------------- #

def _module():
    from src.modules.module import Module

    class _Bare(Module):
        def _start_new_recording(self): return True
        def _start_next_recording_segment(self): return True
        def _stop_recording(self): return True
        def configure_module_special(self, updated_keys): pass

    return object.__new__(_Bare)


def test_alive_during_init_before_loops_exist():
    m = _module()
    assert m._watchdog_alive() == (True, None)


def test_heartbeat_stall_withholds():
    m = _module()
    m.health = MagicMock(heartbeats_active=True,
                         heartbeat_progress_monotonic=time.monotonic() - 120)
    ok, reason = m._watchdog_alive()
    assert not ok and "heartbeat" in reason


def test_stopped_heartbeats_do_not_withhold():
    """Heartbeats stop on purpose when there's no controller IP."""
    m = _module()
    m.health = MagicMock(heartbeats_active=False,
                         heartbeat_progress_monotonic=time.monotonic() - 999)
    assert m._watchdog_alive()[0]


def test_command_stuck_on_listener_withholds():
    m = _module()
    m.health = MagicMock(heartbeats_active=True,
                         heartbeat_progress_monotonic=time.monotonic())
    m.communication = MagicMock(command_listener_running=True,
                                listener_progress_monotonic=time.monotonic() - 400,
                                last_command="set_config {...}")
    ok, reason = m._watchdog_alive()
    assert not ok and "set_config" in reason
    m.communication.listener_progress_monotonic = time.monotonic()
    assert m._watchdog_alive()[0]
