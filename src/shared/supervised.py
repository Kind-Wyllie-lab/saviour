"""
Supervised long-lived threads (v1.0 roadmap A6, plans/supervised-threads.md).

A bare ``threading.Thread(daemon=True)`` loop that hits an unexpected
exception just dies: the process stays up, the feature silently stops
(the PTP monitor freezing its telemetry, a recording-health watchdog that
no longer watches). ``supervise()`` runs ``target(stop_event)`` in a daemon
thread and, if it raises or returns while ``stop_event`` is still unset,
logs it once at ERROR with a traceback and restarts it after a backoff.

Every state change is recorded in a per-process ``SupervisedRegistry``
(``REGISTRY``); each side folds ``REGISTRY.snapshot()`` into its health
report so a crash-looping monitor is operator-visible, not a journal grep.

Target contract:
- signature ``def _loop(stop_event): ...``
- loops on ``while not stop_event.is_set():`` and returns promptly once set
  (use ``stop_event.wait(interval)`` rather than ``time.sleep``)
- catches only *expected*, recoverable per-iteration errors locally; anything
  unexpected propagates here to be logged and restarted
"""

import collections
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

DEFAULT_BACKOFF = (1, 2, 5, 15, 30, 60)
CRASH_WINDOW_SECS = 3600
CRASH_LOOP_THRESHOLD = 3


class SupervisedRegistry:
    """Thread-safe record of every supervised thread's state in this process."""

    def __init__(self):
        self._lock = threading.Lock()
        self._threads: dict[str, dict] = {}

    def _entry(self, name: str) -> dict:
        return self._threads.setdefault(name, {
            "state": "running", "restarts": 0, "last_crash": None,
            "last_error": None, "_crash_times": collections.deque(),
        })

    def mark_running(self, name: str) -> None:
        with self._lock:
            self._entry(name)["state"] = "running"

    def mark_stopped(self, name: str) -> None:
        with self._lock:
            self._entry(name)["state"] = "stopped"

    def mark_crashed(self, name: str, error: str) -> None:
        with self._lock:
            e = self._entry(name)
            e["state"] = "crashed"
            e["restarts"] += 1
            e["last_crash"] = datetime.now(UTC).isoformat(timespec="seconds")
            e["last_error"] = error[:300]
            e["_crash_times"].append(time.monotonic())

    def snapshot(self) -> dict[str, dict]:
        """JSON-safe copy: {name: {state, restarts, restarts_last_hour,
        last_crash, last_error}}."""
        cutoff = time.monotonic() - CRASH_WINDOW_SECS
        out = {}
        with self._lock:
            for name, e in self._threads.items():
                times = e["_crash_times"]
                while times and times[0] < cutoff:
                    times.popleft()
                out[name] = {
                    "state": e["state"],
                    "restarts": e["restarts"],
                    "restarts_last_hour": len(times),
                    "last_crash": e["last_crash"],
                    "last_error": e["last_error"],
                }
        return out


REGISTRY = SupervisedRegistry()


def crash_looping(snapshot: dict | None,
                  threshold: int = CRASH_LOOP_THRESHOLD) -> list[str]:
    """Names in a snapshot that have crashed >= threshold times in the last
    hour -- the condition worth alerting an operator about."""
    return sorted(
        name for name, s in (snapshot or {}).items()
        if (s or {}).get("restarts_last_hour", 0) >= threshold
    )


def supervise(name: str, target: Callable[[threading.Event], None], *,
              stop_event: threading.Event,
              logger: logging.Logger | None = None,
              restart_backoff: tuple = DEFAULT_BACKOFF,
              healthy_after_secs: float = 60,
              registry: SupervisedRegistry = REGISTRY) -> threading.Thread:
    """Run ``target(stop_event)`` in a daemon thread named ``name``,
    restarting it on an unexpected exception or return until ``stop_event``
    is set. Backoff steps through ``restart_backoff`` and resets once a run
    has lasted ``healthy_after_secs``. Returns the (started) thread; join it
    after setting ``stop_event``. ``target`` runs *on* that thread, so a
    ``threading.current_thread()`` check inside it still identifies it."""
    log = logger or logging.getLogger(__name__)

    def run():
        attempt = 0
        registry.mark_running(name)
        while not stop_event.is_set():
            started = time.monotonic()
            try:
                target(stop_event)
            except Exception as e:
                if stop_event.is_set():
                    break
                log.exception(f"Supervised thread '{name}' crashed: {e!r}")
                registry.mark_crashed(name, repr(e))
            else:
                if stop_event.is_set():
                    break
                log.error(f"Supervised thread '{name}' exited unexpectedly")
                registry.mark_crashed(name, "exited unexpectedly")

            if time.monotonic() - started >= healthy_after_secs:
                attempt = 0
            delay = restart_backoff[min(attempt, len(restart_backoff) - 1)]
            attempt += 1
            if stop_event.wait(delay):
                break
            log.warning(f"Restarting supervised thread '{name}' "
                        f"(attempt {attempt}, after {delay}s)")
            registry.mark_running(name)
        registry.mark_stopped(name)

    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread
