"""
systemd watchdog keep-alive for saviour.service (WatchdogSec=, set by
saviour-config's configure_service).

Why (desk fleet, 2026-10-02): a camera module froze solid -- picamera2 called
libcamera's Camera.stop() while holding the GIL, and libcamera was waiting on
a framesync client blocked in recvfrom() for a sync server that had gone
offline. Every Python thread (heartbeats, command listener, logging) waited
on the GIL, the controller dropped the module, and systemd saw a live
process so nothing restarted it. Nothing in-process can recover from that.

This thread sends WATCHDOG=1 every WatchdogSec/3. A frozen interpreter stops
it, so systemd kills the service (WatchdogSignal, SIGABRT by default) and
Restart=always brings it back; faulthandler dumps every thread's stack to
the journal first. `alive()` lets a role withhold pings when a critical loop
has stalled without freezing the whole process.

No dependency: sd_notify is one datagram to $NOTIFY_SOCKET.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import socket
import threading
from collections.abc import Callable

from src.shared.supervised import supervise

_log = logging.getLogger(__name__)


def watchdog_interval_s() -> float | None:
    """WatchdogSec as seconds if systemd enabled it for this process."""
    usec = os.environ.get("WATCHDOG_USEC")
    if not usec:
        return None
    pid = os.environ.get("WATCHDOG_PID")
    if pid and pid.isdigit() and int(pid) != os.getpid():
        return None
    try:
        value = int(usec) / 1e6
    except ValueError:
        return None
    return value if value > 0 else None


def notify(message: str) -> bool:
    """Send an sd_notify message; False if not running under systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):            # abstract namespace
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(message.encode(), addr)
        return True
    except OSError as e:
        _log.warning(f"sd_notify({message!r}) failed: {e}")
        return False


def start(alive: Callable[[], tuple[bool, str | None]] | None = None,
          logger: logging.Logger | None = None) -> threading.Thread | None:
    """Start the keep-alive thread if systemd's watchdog is on (no-op
    otherwise, e.g. when run by hand or under pytest).

    `alive()` returns (ok, reason). While it returns False the pings stop
    and systemd restarts the service after WatchdogSec."""
    log = logger or _log
    interval = watchdog_interval_s()
    if interval is None:
        return None
    # Thread stacks to stderr (the journal) on the watchdog's SIGABRT --
    # faulthandler's handler runs without the GIL, so it works even when
    # the interpreter is frozen.
    faulthandler.enable()
    period = max(1.0, interval / 3)
    log.info(f"systemd watchdog on: WatchdogSec={interval:.0f}s, "
             f"keep-alive every {period:.0f}s")

    def _loop(stop_event: threading.Event) -> None:
        withheld: str | None = None
        while not stop_event.is_set():
            ok, reason = alive() if alive is not None else (True, None)
            if ok:
                if withheld:
                    log.warning(f"watchdog keep-alive resumed ({withheld} recovered)")
                    withheld = None
                notify("WATCHDOG=1")
            elif withheld != reason:
                withheld = reason
                log.error(f"withholding systemd watchdog keep-alive: {reason} -- "
                          f"systemd will restart the service if it persists")
            stop_event.wait(period)

    return supervise("systemd.watchdog", _loop, stop_event=threading.Event(),
                     logger=log)
