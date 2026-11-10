"""Ethernet link state and error counters from sysfs, for the System → Network tab.

Used on both sides: each module reports its eth0 link in every heartbeat
(``ModuleHealthSnapshot.link``), and the controller reads its own. A module on
a 100 Mb link, at half duplex, or with climbing error/drop counters is the
wiring fault ``docs/HARDWARE_NOTES.md`` warns about for PTP; this makes it
visible without SSH.

``LinkStats.sample()`` keeps one counter sample per call (a heartbeat) and
reports how much each counter grew over the last hour, so a burst of errors
stands out from a lifetime total that was mostly before the last reboot.
"""

from __future__ import annotations

import os
import time
from collections import deque

SYS_NET = "/sys/class/net"
COUNTERS = ("rx_errors", "tx_errors", "rx_dropped", "tx_dropped",
            "rx_bytes", "tx_bytes")
WINDOW_S = 3600


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:  # missing file, or EINVAL reading speed on a down link
        return None


def _read_int(path: str) -> int | None:
    v = _read(path)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def read_link(iface: str = "eth0", root: str = SYS_NET) -> dict | None:
    """Point-in-time link state + cumulative counters, or None if no such iface."""
    base = os.path.join(root, iface)
    if not os.path.isdir(base):
        return None
    speed = _read_int(os.path.join(base, "speed"))
    out = {
        "iface": iface,
        "mac": _read(os.path.join(base, "address")),
        "operstate": _read(os.path.join(base, "operstate")),
        # -1 / unreadable when there's no carrier or for virtual links.
        "speed_mbps": speed if speed and speed > 0 else None,
        "duplex": _read(os.path.join(base, "duplex")),
        "carrier_changes": _read_int(os.path.join(base, "carrier_changes")),
        "mtu": _read_int(os.path.join(base, "mtu")),
    }
    for c in COUNTERS:
        out[c] = _read_int(os.path.join(base, "statistics", c))
    return out


class LinkStats:
    """Samples one interface and adds ``<counter>_1h`` growth figures.

    The window is measured from the oldest sample still inside the last hour,
    so it is shorter than an hour just after start-up; ``window_s`` says how
    long it actually covers. A counter that went backwards (driver reset)
    restarts the window rather than reporting a negative delta.
    """

    def __init__(self, iface: str = "eth0", root: str = SYS_NET,
                 window_s: float = WINDOW_S, clock=time.monotonic):
        self.iface = iface
        self.root = root
        self.window_s = window_s
        self._clock = clock
        self._samples: deque[tuple[float, dict]] = deque()

    def sample(self) -> dict | None:
        link = read_link(self.iface, self.root)
        if link is None:
            return None
        now = self._clock()
        tracked = ("carrier_changes",) + COUNTERS
        counters = {k: link.get(k) for k in tracked}
        if self._samples and any(
                counters[k] is not None and self._samples[-1][1].get(k) is not None
                and counters[k] < self._samples[-1][1][k] for k in tracked):
            self._samples.clear()
        self._samples.append((now, counters))
        while len(self._samples) > 1 and now - self._samples[0][0] > self.window_s:
            self._samples.popleft()
        t0, first = self._samples[0]
        for k in tracked:
            a, b = first.get(k), counters.get(k)
            link[f"{k}_1h"] = (b - a) if a is not None and b is not None else None
        link["window_s"] = round(now - t0, 1)
        return link
