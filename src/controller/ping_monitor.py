"""Round-trip time to every host on the rig network, for the System → Network tab.

A supervised thread pings each target once every ``interval_s`` (in parallel,
``ping -c1 -W1``) and keeps an hour of results per IP. Targets come from a
callable, re-read every round, so new DHCP leases and modules are picked up
without a restart. ~20 hosts × 1 ICMP echo per 10 s is negligible next to the
video traffic, and running always (not only while the tab is open) means the
history is there when someone opens it.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

from src.shared.supervised import supervise

_TIME_RE = re.compile(r"time[=<]([\d.]+)\s*ms")

DEFAULT_INTERVAL_S = 10.0
HISTORY_S = 3600
MAX_WORKERS = 16
STATS_WINDOW_S = 300


def ping_once(ip: str, timeout_s: int = 1, run=subprocess.run) -> float | None:
    """One ICMP echo; RTT in ms, or None on no reply."""
    try:
        res = run(["ping", "-n", "-c", "1", "-W", str(timeout_s), ip],
                  capture_output=True, text=True, timeout=timeout_s + 2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    m = _TIME_RE.search(res.stdout)
    return float(m.group(1)) if m else None


class PingMonitor:
    def __init__(self, targets: Callable[[], Iterable[str]], *,
                 interval_s: float = DEFAULT_INTERVAL_S,
                 history_s: float = HISTORY_S,
                 pinger: Callable[[str], float | None] = ping_once,
                 clock: Callable[[], float] = time.time):
        self.logger = logging.getLogger(__name__)
        self._targets = targets
        self.interval_s = interval_s
        self.history_s = history_s
        self._ping = pinger
        self._clock = clock
        self._lock = threading.Lock()
        self._history: dict[str, deque[tuple[float, float | None]]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = supervise("ping-monitor", self._loop,
                                 stop_event=self._stop, logger=self.logger)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self, stop_event: threading.Event) -> None:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS,
                                thread_name_prefix="ping") as pool:
            while not stop_event.is_set():
                started = time.monotonic()
                self.round(pool)
                stop_event.wait(max(0.5, self.interval_s - (time.monotonic() - started)))

    # -- one round (separate for tests) ----------------------------------
    def round(self, pool: ThreadPoolExecutor | None = None) -> None:
        targets = sorted(set(self._targets() or ()))
        if pool is None:
            results = [(ip, self._ping(ip)) for ip in targets]
        else:
            results = list(zip(targets, pool.map(self._ping, targets), strict=True))
        now = self._clock()
        cutoff = now - self.history_s
        with self._lock:
            for ip, rtt in results:
                self._history.setdefault(ip, deque()).append((now, rtt))
            for ip in list(self._history):
                h = self._history[ip]
                while h and h[0][0] < cutoff:
                    h.popleft()
                if not h:
                    del self._history[ip]

    # -- read side -------------------------------------------------------
    def snapshot(self, window_s: float = STATS_WINDOW_S,
                 history_points: int = 120) -> dict[str, dict]:
        """{ip: {last_ms, last_at, last_reply_at, median_ms, max_ms, loss_pct,
        samples, history: [[t, ms|None], ...]}}. Stats cover the last
        ``window_s``; history is the whole retained hour, thinned to at most
        ``history_points`` (each kept point is the worst RTT of its stretch, a
        lost reply winning over any RTT)."""
        now = self._clock()
        with self._lock:
            copied = {ip: list(h) for ip, h in self._history.items()}
        out = {}
        for ip, h in copied.items():
            recent = [r for t, r in h if t >= now - window_s]
            replies = sorted(r for r in recent if r is not None)
            last_t, last_r = h[-1]
            last_reply = next((t for t, r in reversed(h) if r is not None), None)
            out[ip] = {
                "last_ms": last_r,
                "last_at": last_t,
                "last_reply_at": last_reply,
                "median_ms": replies[len(replies) // 2] if replies else None,
                "max_ms": replies[-1] if replies else None,
                "loss_pct": (round(100 * (len(recent) - len(replies)) / len(recent), 1)
                             if recent else None),
                "samples": len(recent),
                "history": _thin(h, history_points),
            }
        return out


def _thin(h: list[tuple[float, float | None]], n: int) -> list[list]:
    if len(h) <= n:
        return [[round(t, 1), r] for t, r in h]
    step = len(h) / n
    out = []
    for i in range(n):
        chunk = h[int(i * step):int((i + 1) * step)] or h[-1:]
        lost = any(r is None for _, r in chunk)
        worst = None if lost else max(r for _, r in chunk)
        out.append([round(chunk[-1][0], 1), worst])
    return out
