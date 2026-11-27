"""Tests for src/shared/ttl_application.py (plans/ttl-applications-ephys-sync.md)."""

from src.shared import ttl_application as ta


def _ttl(app=None, **pins):
    cfg = {"pins": {k.lstrip("g"): v for k, v in pins.items()}}
    if app:
        cfg["application"] = app
    return cfg


SYNC = {"mode": "pseudorandom", "channel": 1}


class TestApplication:
    def test_follows_the_sync_lines_system(self):
        assert ta.application_of({}) == "mixed"
        assert ta.application_of(_ttl(g26=SYNC)) == "mixed"
        assert ta.application_of(_ttl(g26={**SYNC, "system": "open_ephys"})) == "ephys_sync"
        assert ta.application_of(_ttl(g26={**SYNC, "system": "neurologger"})) \
            == "neurologger_sync"
        assert ta.application_of(_ttl(g26={**SYNC, "system": "nope"})) == "mixed"

    def test_older_configs_keep_their_application(self):
        # ttl.application set directly, sync line without a system.
        assert ta.application_of(_ttl("ephys_sync", g26=SYNC)) == "ephys_sync"
        # ... but an explicit "no system" on the line wins
        assert ta.application_of(_ttl("ephys_sync", g26={**SYNC, "system": ""})) == "mixed"
        # and an application with no sync line means nothing now
        assert ta.application_of({"application": "ephys_sync"}) == "mixed"

    def test_module_application_only_for_ttl(self):
        assert ta.module_application({"type": "camera"}) is None
        assert ta.module_application({"type": "ttl", "config": {}}) == "mixed"
        assert ta.module_application({"type": "ttl", "config": {
            "ttl": _ttl(g26={**SYNC, "system": "open_ephys"})}}) == "ephys_sync"

    def test_definitions_hang_together(self):
        for use in ta.USES:
            assert use in ta.ROLES and not ta.ROLES[use].get("more")
        for system in ta.SYNC_SYSTEMS.values():
            assert system["application"] in ta.APPLICATIONS
        modes = [m for r in ta.ROLES.values() for m in r["modes"]]
        assert len(modes) == len(set(modes))


class TestRoles:
    def test_role_follows_the_mode(self):
        assert ta.pin_role({"mode": "pseudorandom"}) == "sync"
        assert ta.pin_role({"mode": "experiment_marker"}) == "experiment_marker"
        assert ta.pin_role({"mode": "scheduled"}) == "protocol"
        assert ta.pin_role({"mode": "input", "role": "sync"}) == "marker_in"
        assert ta.pin_role({"mode": "None"}) == ta.pin_role({}) == "manual"
        assert ta.pin_role({"mode": "warp"}) == "custom"


class TestValidate:
    def test_fine(self):
        assert ta.validate(_ttl(g19={"mode": "experiment_clock"})) == []
        assert ta.validate(_ttl(g26={**SYNC, "system": "open_ephys"},
                                g6={"mode": "experiment_marker"})) == []

    def test_at_most_one_sync(self):
        problems = ta.validate(_ttl(g26=SYNC, g27=SYNC))
        assert any("at most 1" in p for p in problems)

    def test_unknown_system(self):
        assert ta.validate(_ttl(g26={**SYNC, "system": "telepathy"})) == [
            "GPIO 26: unknown sync system 'telepathy'"]


class TestLines:
    def test_lines_and_sync_line(self):
        cfg = _ttl("ephys_sync", g26=SYNC, g19={"mode": "experiment_clock"},
                   g4={"mode": "input", "label": "lever"})
        assert [ln["gpio"] for ln in ta.lines(cfg)] == [4, 19, 26]
        assert ta.sync_line(cfg) == {"gpio": 26, "role": "sync", "mode": "pseudorandom",
                                     "channel": 1, "label": "", "system": "open_ephys"}
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
