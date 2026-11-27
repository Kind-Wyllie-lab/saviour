"""Tests for src/controller/ttl_lines.py (plans/ttl-applications-ephys-sync.md)."""

import json

import pytest

from src.controller.ttl_lines import (
    TTLLinesError,
    TTLLineStore,
    sync_wiring,
    wiring_warnings,
)


class _Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def _ttl(app="ephys_sync", channel=1):
    return {"application": app, "pins": {
        "26": {"mode": "pseudorandom", "role": "sync", "channel": channel},
        "19": {"mode": "experiment_start", "role": "experiment_start", "channel": 2},
    }}


def _modules(ttl):
    return {"ttl_2904": {"type": "ttl", "name": "Sync box", "online": True,
                         "config": {"ttl": ttl}},
            "camera_1": {"type": "camera", "config": {}}}


def _store(tmp_path):
    clock = _Clock()
    return TTLLineStore(str(tmp_path / "lines.json"), clock=clock), clock


def _sync(status):
    return next(ln for ln in status["ttl_2904"]["lines"] if ln["role"] == "sync")


class TestStore:
    def test_unconfirmed_then_confirmed(self, tmp_path):
        store, _ = _store(tmp_path)
        st = store.status(_modules(_ttl()))
        assert list(st) == ["ttl_2904"]
        assert st["ttl_2904"]["label"] == "Ephys sync"
        assert _sync(st)["state"] == "unconfirmed"
        store.confirm("ttl_2904", _ttl(), 26)
        assert _sync(store.status(_modules(_ttl())))["state"] == "confirmed"

    def test_persists(self, tmp_path):
        store, _ = _store(tmp_path)
        store.confirm("ttl_2904", _ttl(), 26)
        again = TTLLineStore(store.path)
        assert again.raw()["ttl_2904"]["lines"]["26"]["key"] == "26:pseudorandom:1"
        assert json.loads(open(store.path).read())["modules"]

    def test_wiring_change_invalidates(self, tmp_path):
        store, _ = _store(tmp_path)
        store.confirm("ttl_2904", _ttl(channel=1), 26)
        assert _sync(store.status(_modules(_ttl(channel=3))))["state"] == "changed"

    def test_goes_stale(self, tmp_path):
        store, clock = _store(tmp_path)
        store.confirm("ttl_2904", _ttl(), 26)
        clock.t += 8 * 86400
        assert _sync(store.status(_modules(_ttl())))["state"] == "stale"

    def test_clear(self, tmp_path):
        store, _ = _store(tmp_path)
        store.confirm("ttl_2904", _ttl(), 26)
        store.clear("ttl_2904", 26)
        assert _sync(store.status(_modules(_ttl())))["state"] == "unconfirmed"

    def test_confirm_needs_a_role(self, tmp_path):
        store, _ = _store(tmp_path)
        with pytest.raises(TTLLinesError):
            store.confirm("ttl_2904", _ttl(), 4)

    def test_module_without_wired_lines_not_listed(self, tmp_path):
        store, _ = _store(tmp_path)
        ttl = {"pins": {"19": {"mode": "experiment_clock"}}}
        assert store.status(_modules(ttl)) == {}

    def test_record_alignment(self, tmp_path):
        store, _ = _store(tmp_path)
        store.record_alignment("ttl_2904", "s1", 26, {"r2": 0.99999, "edges_used": 120})
        last = store.status(_modules(_ttl()))["ttl_2904"]["last_verified"]
        assert last["session"] == "s1" and last["r2"] == 0.99999 and last["gpio"] == 26


class TestWiring:
    def test_sync_wiring_and_warnings(self, tmp_path):
        store, _ = _store(tmp_path)
        wiring = sync_wiring(store.status(_modules(_ttl())))
        assert wiring[0]["module_id"] == "ttl_2904"
        assert wiring[0]["sync"]["gpio"] == 26
        assert wiring_warnings(wiring) == [
            "Sync box: sync line (GPIO 26 → channel 1) never confirmed on the "
            "acquisition system"]
        store.confirm("ttl_2904", _ttl(), 26)
        assert wiring_warnings(sync_wiring(store.status(_modules(_ttl())))) == []

    def test_stimulus_modules_are_not_sync_wiring(self, tmp_path):
        store, _ = _store(tmp_path)
        ttl = {"pins": {"5": {"mode": "scheduled"}}}
        assert sync_wiring(store.status(_modules(ttl))) == []

    def test_sync_module_without_sync_line(self):
        assert wiring_warnings([{"name": "Box", "sync": None}]) == ["Box has no sync line"]
