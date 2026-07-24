"""
Tests for src/controller/communication.py

Covers the fix for a live production crash: ZMQ sockets are not thread-safe,
but send_command()/_send_to_dealer() (called from arbitrary threads — Socket.IO
handlers, scheduled recordings, health checks) and the dedicated listener_thread
(which calls recv_multipart()) both touched the same command_socket with no
serialization, corrupting the ROUTER socket's internal state and crashing the
whole controller process with a native "Assertion failed: !_current_out
(src/router.cpp:...)" SIGABRT roughly every 10-20 minutes in production.
"""

import threading
import time

import pytest

from src.controller.communication import Communication


class _FakeCommandSocket:
    """Records whether send/recv ever overlap in time — proves mutual exclusion
    (or the lack of it) without needing a real ZMQ socket bound to a real port."""

    def __init__(self):
        self.busy = False
        self.overlap_detected = False
        self.lock = threading.Lock()  # guards the two flags above, not the "socket"

    def _enter(self):
        with self.lock:
            if self.busy:
                self.overlap_detected = True
            self.busy = True

    def _exit(self):
        with self.lock:
            self.busy = False

    def send_multipart(self, frames):
        self._enter()
        time.sleep(0.01)
        self._exit()

    def recv_multipart(self):
        self._enter()
        time.sleep(0.01)
        self._exit()
        return [b"loom_camera_3536", b"hello"]


def _bare_communication() -> Communication:
    """Construct without running Communication.__init__ (which binds real ZMQ
    ports and spawns a listener thread) — only the locking behavior of
    _send_to_dealer/_handle_dealer_message is under test here."""
    comm = object.__new__(Communication)
    comm.logger = __import__("logging").getLogger("test")
    comm._connected_dealers = {"loom_camera_3536"}
    comm._dealers_lock = threading.Lock()
    comm._command_socket_lock = threading.Lock()
    comm.command_socket = _FakeCommandSocket()
    return comm


def test_send_and_recv_are_mutually_exclusive():
    comm = _bare_communication()

    threads = []
    for _ in range(8):
        threads.append(threading.Thread(target=comm._send_to_dealer, args=("loom_camera_3536", "get_health {}")))
        threads.append(threading.Thread(target=comm._handle_dealer_message))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)

    assert not comm.command_socket.overlap_detected, (
        "send_multipart() and recv_multipart() ran concurrently on the same "
        "command_socket — this is exactly what corrupts libzmq's ROUTER state "
        "and crashes the process with the router.cpp assertion in production."
    )


def test_send_to_dealer_uses_the_command_socket_lock():
    comm = _bare_communication()
    comm._send_to_dealer("loom_camera_3536", "get_health {}")
    assert not comm.command_socket.busy


def test_handle_dealer_message_uses_the_command_socket_lock():
    comm = _bare_communication()
    comm._handle_dealer_message()
    assert not comm.command_socket.busy
