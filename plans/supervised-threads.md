# Supervised long-lived threads

- **Status:** proposed
- **Created:** 2026-09-07
- **Owner:** Andrew SG
- **CLAUDE.md ref:** Architectural concerns → "Unsupervised threading + broad exception policy"

## Problem

`grep` on `staging` (excluding tests): **64** `threading.Thread(...)` spawns,
**361** `except Exception` handlers. Almost every long-lived loop is a bare
`daemon=True` thread with either:

- **no top-level guard** — an unexpected exception kills the thread silently
  and nothing notices (the process stays up, the feature is just gone), or
- **a blanket `except Exception: log-and-continue` inside the loop body** —
  which hides a permanently-broken iteration as a once-per-second log line
  nobody reads.

This is the root-cause *class* behind several separately-filed bugs:

| Symptom (filed separately) | Same underlying gap |
|---|---|
| `ptp.py::_monitor()` exits permanently on the first transient hiccup | loop self-terminates, nothing restarts it *(fixed 2026-09-07, branch `fix/ptp-monitor-*`)* |
| `is_recording` write-once, wedged pipeline invisible | `_monitor_recording_health` is the only backstop and it's an unsupervised daemon |
| libzmq heartbeat-reconnect `abort()` mid-recording | `_force_reconnect` / listener-thread teardown race, no supervision |
| "a crashed monitor/retry thread dies silently" (CLAUDE.md) | general case |

## Non-goals

- Not a full actor framework, not `asyncio`, not a thread pool.
- Not touching the ~40 short-lived one-shot worker threads (`_do_update`,
  `_do_mend`, `delayed_reconnect`, per-request workers) — those are fine as
  fire-and-forget; a failure there is already surfaced by its own status push.
- Not changing the `except Exception` count wholesale. Only the long-lived
  loops get restructured.

## Proposal

A ~40-line helper, `src/common/supervised.py` (new shared module; both
`src/controller` and `src/modules` import it):

```python
def supervise(name, target, *, stop_event, logger,
              restart_backoff=(1, 2, 5, 15, 30, 60),
              on_state=None):
    """Run `target(stop_event)` in a daemon thread. If it returns or raises
    while stop_event is unset, log at ERROR (with traceback) and restart it
    after the next backoff step. Reset backoff once it has run clean for
    >60 s. Call on_state('crashed'|'running', name, exc) on every transition
    so callers can fold it into health reporting."""
```

Contract for a supervised target:

- signature `def _loop(stop_event): ...`
- loops on `while not stop_event.is_set():` and returns promptly when set
- does **not** carry its own catch-all — a genuinely unexpected exception
  should propagate to `supervise`, which logs it *once* with a full
  traceback and restarts. Expected, recoverable per-iteration errors (a
  `subprocess` timeout, a transient `nmcli` failure) are still caught
  locally and `continue`d — the `ptp.py::_monitor` rewrite is the reference
  pattern: catch the known-transient case, log the *transition*, keep going.

### Health surfacing

`on_state` feeds a new `supervised_threads` dict on each side's health
report: `{name: {"state": "running"|"crashed", "restarts": n,
"last_crash": iso, "last_error": str}}`. The controller aggregates modules'
copies; a thread that has restarted >N times in the last hour raises a
typed fault (same pipeline as PTP-degraded / recording-health-warning), so
"a monitor is crash-looping" becomes operator-visible instead of a journal
grep.

## Migration order (highest data-loss leverage first)

1. `src/modules/recording.py::_monitor_recording_health` +
   `_monitor_recording_length` — the pipeline-liveness + disk-full loops.
2. `src/modules/ptp.py::_monitor` / `src/controller/ptp.py::_monitor` —
   already made non-self-terminating on 2026-09-07; move them onto
   `supervise` so an *unexpected* exception (not just a non-active reading)
   also recovers, and so the restart shows up in health.
3. `src/controller/recording.py::_monitor_thread` (`_monitor_sessions`) —
   the controller's session watchdog; if it dies, every liveness check dies.
4. `src/controller/health.py::monitor_health`,
   `src/controller/web.py::_nas_monitor_loop`.
5. `src/modules/communication.py` listener + heartbeat-monitor threads —
   needs care (the libzmq teardown race is here; do this one with the
   `_reconnect_lock` unification in the same pass).
6. `apa_arduino` `send_state_loop`, `modules.py` ready-timeout / dropout
   threads.

Each step: convert the target to the `(stop_event)` signature, delete its
internal catch-all where it only hid failures, add one `supervise(...)`
call, assert the loop still stops cleanly on shutdown (join with timeout).

## Acceptance criteria

- A supervised target that raises on its 3rd iteration is restarted, logs
  exactly one ERROR+traceback per crash, and `supervised_threads[name]
  ["restarts"]` increments — covered by a unit test with a fake target.
- Killing `-STOP`/`-CONT` or forcing an exception in `_monitor_recording_health`
  during an integration-test recording produces an operator-visible fault
  within one backoff cycle, and recording/health resume when it recovers.
- Clean shutdown still joins every supervised thread within its timeout (no
  hang on `systemctl stop saviour`).
- No change to the count of *short-lived* worker threads.

## Rollback

Per-loop. Each migration is an independent commit; reverting one restores
that loop's previous bare-thread form. The helper module is inert if unused.
