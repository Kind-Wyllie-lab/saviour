"""Tests for src/shared/ttl_application.py (plans/ttl-applications-ephys-sync.md)."""

from src.shared import ttl_application as ta


def _ttl(app=None, **pins):
    cfg = {"pins": {k.lstrip("g"): v for k, v in pins.items()}}
    if app:
        cfg["application"] = app
    return cfg


SYNC = {"mode": "pseudorandom", "role": "sync", "channel": 1}


class TestApplication:
    def test_default_and_unknown_read_as_mixed(self):
        assert ta.application_of({}) == "mixed"
        assert ta.application_of({"application": "nope"}) == "mixed"
        assert ta.application_of({"application": "ephys_sync"}) == "ephys_sync"

    def test_module_application_only_for_ttl(self):
        assert ta.module_application({"type": "camera"}) is None
        assert ta.module_application({"type": "ttl", "config": {}}) == "mixed"
        assert ta.module_application({"type": "ttl", "config": {
            "ttl": {"application": "ephys_sync"}}}) == "ephys_sync"

    def test_every_application_role_is_defined(self):
        for spec in ta.APPLICATIONS.values():
            for role in (spec.get("roles") or []) + (spec.get("requires") or []):
                assert role in ta.ROLES


class TestValidate:
    def test_mixed_roleless_pins_are_fine(self):
        assert ta.validate(_ttl(g19={"mode": "experiment_clock"})) == []

    def test_mixed_checks_roles_that_are_set(self):
        problems = ta.validate(_ttl(g19={"mode": "input", "role": "sync"}))
        assert any("must be pseudorandom" in p for p in problems)

    def test_ephys_sync_ok(self):
        assert ta.validate(_ttl("ephys_sync", g26=SYNC, g19={
            "mode": "experiment_start", "role": "experiment_start", "channel": 2})) == []

    def test_ephys_sync_missing_sync(self):
        problems = ta.validate(_ttl("ephys_sync", g19={
            "mode": "experiment_start", "role": "experiment_start"}))
        assert problems == ["Ephys sync needs a sync line"]

    def test_role_needed_outside_mixed(self):
        problems = ta.validate(_ttl("ephys_sync", g26=SYNC, g4={"mode": "input"}))
        assert problems == ["GPIO 4 has no role (Ephys sync lines each need one)"]

    def test_role_not_offered_by_application(self):
        problems = ta.validate(_ttl("ephys_sync", g26=SYNC, g5={
            "mode": "interval_pulse", "role": "stimulus"}))
        assert any("Stimulus isn't used by Ephys sync" in p for p in problems)

    def test_at_most_one_sync(self):
        problems = ta.validate(_ttl("ephys_sync", g26=SYNC, g27=SYNC))
        assert any("at most 1" in p for p in problems)


class TestLines:
    def test_lines_and_sync_line(self):
        cfg = _ttl("ephys_sync", g26=SYNC, g19={"mode": "experiment_clock"},
                   g4={"mode": "input", "role": "marker_in", "label": "lever"})
        assert [ln["gpio"] for ln in ta.lines(cfg)] == [4, 26]
        assert ta.sync_line(cfg) == {"gpio": 26, "role": "sync", "mode": "pseudorandom",
                                     "channel": 1, "label": ""}
        assert ta.sync_line(_ttl()) is None

    def test_wiring_key_changes_with_channel(self):
        a = ta.sync_line(_ttl(g26=SYNC))
        b = ta.sync_line(_ttl(g26={**SYNC, "channel": 2}))
        assert ta.wiring_key(a) != ta.wiring_key(b)


def test_external_sync_kinds():
    from src.shared.ttl_application import external_sync_kind, sync_application
    assert external_sync_kind(None) is None
    assert external_sync_kind("none") is None
    assert external_sync_kind(None, legacy_ephys=True) == "ephys"
    assert external_sync_kind("neurologger") == "neurologger"
    assert sync_application("ephys") == "ephys_sync"
    assert sync_application("neurologger") == "neurologger_sync"
    assert sync_application(None) is None
    import pytest
    with pytest.raises(ValueError):
        external_sync_kind("telepathy")
