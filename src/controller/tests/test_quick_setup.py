"""Tests for src/controller/quick_setup.py (plans/rig-quick-setup.md)."""

import pytest

from src.controller.quick_setup import QuickSetupError, QuickSetupStore, clean_run


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


def test_clean_run_rejects_bad_shapes():
    with pytest.raises(QuickSetupError):
        clean_run("nope", now=0, by="guest")
    with pytest.raises(QuickSetupError):
        clean_run({"modules": []}, now=0, by="guest")
    with pytest.raises(QuickSetupError):
        clean_run({"modules": {str(i): {} for i in range(300)}}, now=0, by="guest")


def test_unreadable_file_starts_empty(tmp_path):
    p = tmp_path / "qs.json"
    p.write_text("{not json")
    assert QuickSetupStore(str(p)).last_run() is None
