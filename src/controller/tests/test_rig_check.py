"""Tests for src/controller/rig_check.py (plans/rig-expected-hardware.md)."""

import json
import os
from unittest.mock import MagicMock

import pytest

from src.controller.rig_check import (
    RigMonitor,
    RigProfileError,
    describe_item,
    evaluate,
    load_profile,
    shortfalls,
    validate_profile,
)

APA = [
    {"label": "APA camera", "types": ["apa_camera"], "min": 1, "severity": "required"},
    {"label": "Shock/motor", "types": ["apa_arduino"], "min": 1, "severity": "required"},
    {"label": "Microphone", "types": ["microphone"], "min": 1, "severity": "optional"},
]


def _mod(type_, online=True, status="READY", name=None):
    return {"type": type_, "online": online, "status": status, "name": name or ""}


# ---------------------------------------------------------------------------
# profile loading / validation
# ---------------------------------------------------------------------------

class TestProfile:
    def test_empty_config_is_empty_profile(self):
        p = load_profile({})
        assert p["expected"] == []
        assert p["startup_grace_s"] == 120

    def test_normalises_entries(self):
        p = load_profile({"rig": {"expected": [{"types": "sound"}]}})
        assert p["expected"] == [
            {"label": "sound", "types": ["sound"], "min": 1, "severity": "required"}]

    def test_skips_invalid_entries_keeps_rest(self):
        p = load_profile({"rig": {"expected": [
            {"types": []}, {"types": ["ttl"], "min": "x"}, {"types": ["sound"]}]}})
        assert [e["types"] for e in p["expected"]] == [["sound"]]

    def test_legacy_expected_module_counts_migrated(self):
        p = load_profile({"recording": {"expected_module_counts": {
            "camera": 2, "microphone": 0}}})
        assert p["expected"] == [
            {"label": "camera", "types": ["camera"], "min": 2, "severity": "required"}]

    def test_rig_expected_wins_over_legacy(self):
        p = load_profile({"rig": {"expected": []},
                          "recording": {"expected_module_counts": {"camera": 2}}})
        assert p["expected"] == []

    def test_validate_names_bad_item(self):
        with pytest.raises(RigProfileError, match="item 2"):
            validate_profile([{"types": ["sound"]}, {"types": ["ttl"], "severity": "x"}])
        with pytest.raises(RigProfileError):
            validate_profile([{"types": ["ttl"], "min": -1}])

    @pytest.mark.parametrize("variant", ["apa", "acoustic_startle", "loom", "habitat"])
    def test_shipped_variant_defaults_are_valid(self, variant):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "variants", variant, f"{variant}_controller_config.json")
        with open(path) as f:
            rig = json.load(f)["rig"]
        assert validate_profile(rig["expected"])


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------

class TestEvaluate:
    def test_empty_profile_is_ok(self):
        r = evaluate([], {"camera_1": _mod("camera")})
        assert r["ok"] and r["level"] == "ok" and r["items"] == []

    def test_all_present(self):
        mods = {"apa_camera_1": _mod("apa_camera"), "apa_arduino_1": _mod("apa_arduino"),
                "microphone_1": _mod("microphone")}
        r = evaluate(APA, mods)
        assert r["level"] == "ok"
        assert all(i["status"] == "ok" for i in r["items"])

    def test_required_missing_is_error(self):
        mods = {"apa_camera_1": _mod("apa_camera"), "microphone_1": _mod("microphone")}
        r = evaluate(APA, mods)
        assert r["level"] == "error" and not r["ok"]
        item = r["items"][1]
        assert item["status"] == "short" and item["count"] == 0
        assert "Shock/motor: 0 of 1" in r["summary"]

    def test_only_optional_missing_is_warning(self):
        mods = {"apa_camera_1": _mod("apa_camera"), "apa_arduino_1": _mod("apa_arduino")}
        assert evaluate(APA, mods)["level"] == "warning"

    def test_faulty_module_not_counted_and_reason_listed(self):
        mods = {"apa_camera_1": _mod("apa_camera"), "apa_arduino_1": _mod("apa_arduino"),
                "microphone_1": _mod("microphone", name="Mic A")}
        health = {"microphone_1": {"hardware_fault": "No AudioMoth microphones detected"}}
        r = evaluate(APA, mods, health)
        mic = r["items"][2]
        assert mic["status"] == "faulty" and mic["count"] == 0
        assert mic["faulty"] == [{"id": "microphone_1", "name": "Mic A",
                                  "reason": "No AudioMoth microphones detected"}]
        assert "Mic A — No AudioMoth" in describe_item(mic)
        assert r["level"] == "warning"

    def test_offline_module_listed(self):
        mods = {"apa_camera_1": _mod("apa_camera"),
                "apa_arduino_1": _mod("apa_arduino", online=False)}
        r = evaluate(APA[:2], mods)
        assert r["items"][1]["offline"] == [{"id": "apa_arduino_1", "name": "apa_arduino_1"}]
        assert r["level"] == "error"

    def test_offline_status_counts_as_offline(self):
        r = evaluate(APA[:1], {"apa_camera_1": _mod("apa_camera", status="OFFLINE")})
        assert r["items"][0]["status"] == "short"

    def test_glob_types_and_counts(self):
        profile = [{"label": "Cams", "types": ["*camera"], "min": 3, "severity": "required"}]
        mods = {"camera_1": _mod("camera"), "habitat_camera_2": _mod("habitat_camera"),
                "microphone_1": _mod("microphone")}
        r = evaluate(profile, mods)
        assert r["items"][0]["count"] == 2 and r["level"] == "error"
        mods["hailo_camera_3"] = _mod("hailo_camera")
        assert evaluate(profile, mods)["ok"]

    def test_pending_during_grace(self):
        r = evaluate(APA, {}, pending=True)
        assert r["level"] == "pending" and r["pending"]
        assert all(i["status"] == "pending" for i in r["items"])
        assert shortfalls(r) == []

    def test_shortfalls_filters_severity(self):
        r = evaluate(APA, {})
        assert [i["label"] for i in shortfalls(r, "required")] == ["APA camera", "Shock/motor"]
        assert len(shortfalls(r)) == 3
        assert shortfalls(None) == [] and shortfalls(MagicMock()) == []


# ---------------------------------------------------------------------------
# RigMonitor
# ---------------------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _monitor(config, modules, health=None, **kw):
    clock = _Clock()
    emit, alert = MagicMock(), MagicMock()
    m = RigMonitor(get_config=lambda: config, get_modules=lambda: modules,
                   get_health=lambda: health or {}, emit=emit, send_alert=alert,
                   clock=clock, today=lambda: "2026-11-20", **kw)
    return m, clock, emit, alert


CFG = {"rig": {"expected": APA[:2], "startup_grace_s": 60, "alert_after_s": 300}}


class TestRigMonitor:
    def test_pending_then_short_after_grace(self):
        m, clock, emit, _ = _monitor(CFG, {})
        assert m.tick()["level"] == "pending"
        clock.t += 61
        assert m.tick()["level"] == "error"
        assert emit.call_count == 2
        assert emit.call_args[0][0] == "rig_check"

    def test_emits_only_on_change(self):
        m, clock, emit, _ = _monitor(CFG, {})
        clock.t += 61
        m.tick()
        m.tick()
        assert emit.call_count == 1

    def test_restart_grace(self):
        m, clock, _, _ = _monitor(CFG, {})
        clock.t += 61
        assert m.tick()["level"] == "error"
        m.restart_grace()
        assert m.tick()["level"] == "pending"

    def test_alerts_once_per_day_after_alert_after_s(self):
        m, clock, _, alert = _monitor(CFG, {"apa_camera_1": _mod("apa_camera")})
        clock.t += 61
        m.tick()
        clock.t += 200
        m.tick()
        alert.assert_not_called()
        clock.t += 101
        m.tick()
        assert alert.call_count == 1
        kw = alert.call_args.kwargs
        assert "Shock/motor" in kw["title"] and kw["severity"] == "warning"
        clock.t += 1000
        m.tick()
        assert alert.call_count == 1

    def test_recovery_resets_timer(self):
        modules = {}
        m, clock, _, alert = _monitor(CFG, modules)
        clock.t += 61
        m.tick()
        clock.t += 250
        modules.update({"apa_camera_1": _mod("apa_camera"),
                        "apa_arduino_1": _mod("apa_arduino")})
        assert m.tick()["ok"]
        modules.pop("apa_arduino_1")
        clock.t += 100
        m.tick()
        alert.assert_not_called()

    def test_alert_toggle_off(self):
        cfg = {**CFG, "teams": {"notify_rig_incomplete": False}}
        m, clock, _, alert = _monitor(cfg, {})
        clock.t += 1000
        m.tick()
        clock.t += 1000
        m.tick()
        alert.assert_not_called()

    def test_optional_shortfall_never_alerts(self):
        cfg = {"rig": {"expected": APA[2:], "startup_grace_s": 0, "alert_after_s": 0}}
        m, clock, _, alert = _monitor(cfg, {})
        m.tick()
        clock.t += 10
        m.tick()
        alert.assert_not_called()

    def test_latest_evaluates_when_never_ticked(self):
        m, _, emit, _ = _monitor({}, {})
        assert m.latest()["level"] == "ok"
        emit.assert_not_called()

    def test_health_error_tolerated(self):
        def boom():
            raise RuntimeError("x")
        m = RigMonitor(get_config=lambda: CFG, get_modules=lambda: {},
                       get_health=boom, clock=lambda: 0.0)
        assert m.tick()["level"] == "pending"


# ---------------------------------------------------------------------------
# application / when (plans/ttl-applications-ephys-sync.md)
# ---------------------------------------------------------------------------

EPHYS_ITEM = {"label": "Ephys sync", "types": ["ttl"], "application": "ephys_sync",
              "min": 1, "severity": "required", "when": "ephys"}


def _ttl_mod(app=None, online=True):
    cfg = {"ttl": {"application": app}} if app else {}
    return {"type": "ttl", "online": online, "status": "READY", "name": "",
            "config": cfg}


class TestConditionalItems:
    def test_validates_application_and_when(self):
        assert validate_profile([EPHYS_ITEM])[0]["when"] == "ephys"
        with pytest.raises(RigProfileError, match="application"):
            validate_profile([{**EPHYS_ITEM, "application": "nope"}])
        with pytest.raises(RigProfileError, match="when"):
            validate_profile([{**EPHYS_ITEM, "when": "sometimes"}])

    def test_inactive_without_the_condition(self):
        r = evaluate([EPHYS_ITEM], {})
        assert r["level"] == "ok" and r["items"][0]["active"] is False
        assert shortfalls(r) == []

    def test_active_for_an_ephys_session(self):
        r = evaluate([EPHYS_ITEM], {}, conditions={"ephys"})
        assert r["level"] == "error" and r["items"][0]["active"]
        assert [i["label"] for i in shortfalls(r)] == ["Ephys sync"]

    def test_application_must_match(self):
        mods = {"ttl_1": _ttl_mod("stimulus_control")}
        assert evaluate([EPHYS_ITEM], mods, conditions={"ephys"})["level"] == "error"
        mods["ttl_2"] = _ttl_mod("ephys_sync")
        r = evaluate([EPHYS_ITEM], mods, conditions={"ephys"})
        assert r["ok"] and [p["id"] for p in r["items"][0]["present"]] == ["ttl_2"]

    def test_with_conditions_rejudges(self):
        from src.controller.rig_check import with_conditions
        live = evaluate([EPHYS_ITEM], {})
        assert with_conditions(live, {"ephys"})["level"] == "error"
        assert with_conditions(live, ())["level"] == "ok"

    def test_monitor_for_session(self):
        m, clock, _, alert = _monitor({"rig": {"expected": [EPHYS_ITEM],
                                                "startup_grace_s": 0,
                                                "alert_after_s": 0}}, {})
        clock.t += 1
        assert m.tick()["level"] == "ok"
        assert m.for_session({"ephys"})["level"] == "error"
        clock.t += 10
        m.tick()
        alert.assert_not_called()  # conditional items never alert live
