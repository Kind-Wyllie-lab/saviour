"""Rate-limited logging for hot paths.

The capture / event hot paths — the camera per-frame callback, TTL edge
callbacks, PTP log-line parsing — can emit the *same* log line tens to
thousands of times a second when something goes persistently wrong (a camera
throwing on every frame, a sensor with no timestamp metadata, a parser hitting
malformed lines). That floods the journal and, worse, trips journald's
per-service RateLimitBurst so genuinely useful lines from elsewhere in the
service get dropped too.

RateLimitedLogger coalesces repeats keyed by a caller-supplied string:

* the first occurrence of a key logs immediately at the given level;
* subsequent occurrences are counted and at most one summary line is emitted
  per ``interval_s``, carrying the suppressed count;
* calling ``ok(key)`` clears the key and, if anything was suppressed, emits a
  one-line recovery notice with the total — so "it started failing" and "it
  stopped failing" are both greppable without the spam in between.

Both the message text and the failure/recovery transitions stay in the
journal; only the identical repeats are dropped.
"""

import logging
import time


class RateLimitedLogger:
    def __init__(self, logger: logging.Logger, interval_s: float = 30.0):
        self._logger = logger
        self._interval_s = interval_s
        # key -> {"since": monotonic, "window_start": monotonic,
        #         "suppressed_window": int, "suppressed_total": int}
        self._state: dict = {}

    def log(self, level: int, key: str, msg: str) -> None:
        now = time.monotonic()
        st = self._state.get(key)
        if st is None:
            self._state[key] = {
                "since": now,
                "window_start": now,
                "suppressed_window": 0,
                "suppressed_total": 0,
            }
            self._logger.log(level, msg)
            return

        st["suppressed_window"] += 1
        st["suppressed_total"] += 1
        elapsed = now - st["window_start"]
        if elapsed >= self._interval_s:
            self._logger.log(
                level,
                f"{msg} (repeated {st['suppressed_window']}x in the last "
                f"{elapsed:.0f}s; {st['suppressed_total']} since "
                f"{now - st['since']:.0f}s ago)",
            )
            st["window_start"] = now
            st["suppressed_window"] = 0

    def error(self, key: str, msg: str) -> None:
        self.log(logging.ERROR, key, msg)

    def warning(self, key: str, msg: str) -> None:
        self.log(logging.WARNING, key, msg)

    def info(self, key: str, msg: str) -> None:
        self.log(logging.INFO, key, msg)

    def ok(self, key: str, msg: str | None = None) -> None:
        """Signal that the condition for ``key`` has cleared.

        Cheap to call unconditionally (e.g. once per good frame): a miss is a
        single dict lookup. Emits a recovery line only if the key was active
        and something had been suppressed.
        """
        st = self._state.pop(key, None)
        if st is None:
            return
        recovered = msg or f"{key}: cleared"
        if st["suppressed_total"] > 0:
            self._logger.info(
                f"{recovered} (after {st['suppressed_total']} suppressed "
                f"occurrence(s) over {time.monotonic() - st['since']:.0f}s)"
            )
