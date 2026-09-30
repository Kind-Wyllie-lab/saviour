"""Tests for src/shared/supervised.py (v1.0 roadmap A6)."""

import logging
import threading
import time

from src.shared.supervised import SupervisedRegistry, crash_looping, supervise

FAST = (0.01,)


def _wait_for(pred, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return False


def test_crash_on_third_iteration_is_logged_once_and_restarted(caplog):
    """Acceptance criterion from the plan: a target raising on its 3rd
    iteration restarts, logs exactly one ERROR+traceback per crash, and the
    restart counter increments."""
    reg = SupervisedRegistry()
    stop = threading.Event()
    calls = {"iterations": 0, "runs": 0}

    def target(stop_event):
        calls["runs"] += 1
        while not stop_event.is_set():
            calls["iterations"] += 1
            if calls["iterations"] == 3:
                raise RuntimeError("boom")
            stop_event.wait(0.005)

    with caplog.at_level(logging.ERROR):
        t = supervise("t1", target, stop_event=stop, restart_backoff=FAST,
                      registry=reg)
        assert _wait_for(lambda: calls["runs"] >= 2)
        stop.set()
        t.join(timeout=2)

    assert not t.is_alive()
    crashes = [r for r in caplog.records if "crashed" in r.getMessage()]
    assert len(crashes) == 1
    assert crashes[0].exc_info is not None  # traceback attached
    snap = reg.snapshot()["t1"]
    assert snap["restarts"] == 1
    assert snap["restarts_last_hour"] == 1
    assert "boom" in snap["last_error"]
    assert snap["state"] == "stopped"


def test_unexpected_return_is_restarted_too():
    reg = SupervisedRegistry()
    stop = threading.Event()
    runs = []

    def target(stop_event):
        runs.append(1)

    t = supervise("t2", target, stop_event=stop, restart_backoff=FAST, registry=reg)
    assert _wait_for(lambda: len(runs) >= 3)
    stop.set()
    t.join(timeout=2)
    assert reg.snapshot()["t2"]["last_error"] == "exited unexpectedly"


def test_clean_stop_is_not_a_crash_and_joins_promptly():
    reg = SupervisedRegistry()
    stop = threading.Event()

    def target(stop_event):
        while not stop_event.is_set():
            stop_event.wait(0.01)

    t = supervise("t3", target, stop_event=stop, registry=reg)
    assert _wait_for(lambda: reg.snapshot().get("t3", {}).get("state") == "running")
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive()
    snap = reg.snapshot()["t3"]
    assert snap["restarts"] == 0
    assert snap["state"] == "stopped"


def test_exception_raised_because_of_shutdown_is_not_a_crash():
    reg = SupervisedRegistry()
    stop = threading.Event()

    def target(stop_event):
        stop_event.wait()
        raise OSError("socket closed during shutdown")

    t = supervise("t4", target, stop_event=stop, registry=reg)
    stop.set()
    t.join(timeout=2)
    assert reg.snapshot()["t4"]["restarts"] == 0


def test_stop_during_backoff_exits_without_restarting():
    reg = SupervisedRegistry()
    stop = threading.Event()
    runs = []

    def target(stop_event):
        runs.append(1)
        raise RuntimeError("fail")

    t = supervise("t5", target, stop_event=stop, restart_backoff=(10,), registry=reg)
    assert _wait_for(lambda: reg.snapshot().get("t5", {}).get("restarts") == 1)
    stop.set()
    t.join(timeout=2)
    assert not t.is_alive()
    assert len(runs) == 1


def test_target_runs_on_the_returned_thread():
    stop = threading.Event()
    seen = {}

    def target(stop_event):
        seen["thread"] = threading.current_thread()
        stop_event.wait()

    t = supervise("t6", target, stop_event=stop, registry=SupervisedRegistry())
    assert _wait_for(lambda: "thread" in seen)
    assert seen["thread"] is t
    assert t.name == "t6"
    stop.set()
    t.join(timeout=2)


def test_crash_looping_threshold():
    snap = {
        "a": {"restarts_last_hour": 3},
        "b": {"restarts_last_hour": 2},
        "c": {"restarts_last_hour": 7},
    }
    assert crash_looping(snap) == ["a", "c"]
    assert crash_looping(None) == []
