"""Tests for src/controller/quick_setup.py (plans/rig-quick-setup.md)."""

from unittest.mock import MagicMock

import pytest

from src.controller.facade import ControllerFacade
from src.controller.quick_setup import QuickSetupError, QuickSetupStore, clean_run
from src.controller.rig_check import RigProfileError


def _run(**over):
    return {"researcher": "asg", "variant": "apa", "external_sync": "ephys",
            "rig": {"level": "ok", "summary": "All expected hardware present"},
            "modules": {"cam_1": {"name": "Top", "type": "apa_camera", "confirmed": True},
                        "mic_1": {"name": "Mic", "type": "microphone", "confirmed": False}},
            **over}


def test_save_and_reload(tmp_path):
    store = QuickSetupStore(str(tmp_path / "qs.json"), clock=lambda: 1000.0)
    assert store.last_run() is None
    saved = store.save(_run(), by="admin")
    assert saved["at"] == 1000.0 and saved["by"] == "admin"
    assert saved["modules"]["mic_1"]["confirmed"] is False
    again = QuickSetupStore(store.path)
    assert again.last_run()["external_sync"] == "ephys"


def test_only_last_run_kept(tmp_path):
    store = QuickSetupStore(str(tmp_path / "qs.json"))
    store.save(_run(), by="admin")
    store.save(_run(external_sync=None, modules={}), by="guest")
    last = store.last_run()
    assert last["external_sync"] is None and last["modules"] == {} and last["by"] == "guest"


def test_clean_run_ignores_client_time_and_unknown_keys():
    run = clean_run(_run(at=5, by="admin", extra="x"), now=42.0, by="guest")
    assert run["at"] == 42.0 and run["by"] == "guest" and "extra" not in run
    assert run["rig"]["level"] == "ok"


def test_clean_run_keeps_sync_lines():
    run = clean_run(_run(sync_lines=[
        {"module_id": "ttl_1", "name": "Sync box", "gpio": "26", "role": "sync",
         "channel": 1, "state": "confirmed", "junk": 1},
        {"module_id": "ttl_1", "gpio": "x"}]), now=0, by="admin")
    assert run["sync_lines"] == [{"module_id": "ttl_1", "name": "Sync box", "gpio": 26,
                                  "role": "sync", "channel": 1, "state": "confirmed"}]
    assert clean_run(_run(), now=0, by="admin")["sync_lines"] == []


def test_clean_run_rejects_bad_shapes():
    with pytest.raises(QuickSetupError):
        clean_run("nope", now=0, by="guest")
    with pytest.raises(QuickSetupError):
        clean_run({"modules": []}, now=0, by="guest")
    with pytest.raises(QuickSetupError):
        clean_run({"sync_lines": {}}, now=0, by="guest")
    with pytest.raises(QuickSetupError):
        clean_run({"modules": {str(i): {} for i in range(300)}}, now=0, by="guest")


def test_unreadable_file_starts_empty(tmp_path):
    p = tmp_path / "qs.json"
    p.write_text("{not json")
    assert QuickSetupStore(str(p)).last_run() is None


# ── Facade: add an expected item (Quick setup step 3) ────────────────────────


def _facade(config):
    f = ControllerFacade(MagicMock())
    f.get_config = MagicMock(return_value=config)
    f.set_config = MagicMock()
    f.refresh_rig_check = MagicMock()
    return f


def test_add_rig_expected_item_appends_canonical_entry():
    f = _facade({"rig": {"expected": [{"label": "Cam", "types": ["*camera"],
                                       "min": 1, "severity": "required"}]}})
    entry = f.add_rig_expected_item({"types": ["microphone"], "min": "2"})
    assert entry == {"label": "microphone", "types": ["microphone"], "min": 2,
                     "severity": "required"}
    expected = f.set_config.call_args.args[0]["rig"]["expected"]
    assert [e["label"] for e in expected] == ["Cam", "microphone"]
    f.refresh_rig_check.assert_called_once()


def test_add_rig_expected_item_keeps_legacy_counts():
    f = _facade({"recording": {"expected_module_counts": {"camera": 2}}})
    f.add_rig_expected_item({"types": ["ttl"], "when": "ephys",
                             "application": "ephys_sync"})
    expected = f.set_config.call_args.args[0]["rig"]["expected"]
    assert [e["types"] for e in expected] == [["camera"], ["ttl"]]
    assert expected[1]["when"] == "ephys"


def test_add_rig_expected_item_rejects_bad_item():
    f = _facade({})
    with pytest.raises(RigProfileError):
        f.add_rig_expected_item({"types": []})
    f.set_config.assert_not_called()
