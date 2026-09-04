"""Tests for src/controller/dashboard_views.py (DashboardViewStore).

Pure file I/O against a temp directory -- no root/hardware needed.
"""

import os
import tempfile

import pytest

from src.controller.dashboard_views import DashboardViewStore, ViewError


def _store() -> DashboardViewStore:
    return DashboardViewStore(
        views_dir=os.path.join(tempfile.mkdtemp(), "dashboard_views")
    )


def _view(name: str = "Overview", **over) -> dict:
    base = {
        "name": name,
        "group": "",
        "widgets": [
            {"id": "stream:camera_ab12", "type": "stream", "target": "camera_ab12"},
            {"id": "widget:health", "type": "health"},
            {"id": "widget:module-list", "type": "module-list"},
        ],
        "layout": {
            "stream:camera_ab12": {"x": 0, "y": 0, "width": 440},
            "widget:health": {"x": 900, "y": 0, "width": 340, "height": 300},
        },
    }
    base.update(over)
    return base


# --- save / list round-trip ---------------------------------------------

def test_save_then_list_round_trips():
    s = _store()
    saved = s.save(_view("My Rig"))
    assert saved["id"] == "my-rig"
    assert saved["widgets"][0]["target"] == "camera_ab12"
    assert saved["layout"]["widget:health"]["height"] == 300.0

    listed = s.list()
    assert len(listed) == 1
    assert listed[0]["name"] == "My Rig"


def test_list_sorted_by_name_and_skips_junk():
    s = _store()
    s.save(_view("Zulu"))
    s.save(_view("alpha"))
    os.makedirs(s.dir, exist_ok=True)
    with open(os.path.join(s.dir, "broken.json"), "w") as f:
        f.write("{not json")
    names = [v["name"] for v in s.list()]
    assert names == ["alpha", "Zulu"]


def test_missing_dir_lists_empty():
    assert _store().list() == []


# --- id handling ------------------------------------------------------

def test_id_derived_from_name_and_deduped():
    s = _store()
    a = s.save(_view("Same Name"))
    b = s.save(_view("Same Name"))
    assert a["id"] == "same-name"
    assert b["id"] == "same-name-2"
    assert len(s.list()) == 2


def test_explicit_id_overwrites_itself():
    s = _store()
    s.save(_view("V1", id="fixed"))
    s.save(_view("V1 renamed", id="fixed"))
    listed = s.list()
    assert len(listed) == 1
    assert listed[0]["name"] == "V1 renamed"


# --- validation -----------------------------------------------------

def test_rejects_missing_name():
    with pytest.raises(ViewError):
        _store().save(_view(name="  "))


def test_rejects_unknown_widget_type():
    with pytest.raises(ViewError):
        _store().save(_view(widgets=[{"id": "x", "type": "bogus"}]))


def test_rejects_stream_widget_without_target():
    with pytest.raises(ViewError):
        _store().save(_view(widgets=[{"id": "stream:x", "type": "stream"}]))


def test_rejects_duplicate_widget_ids():
    with pytest.raises(ViewError):
        _store().save(_view(widgets=[
            {"id": "widget:health", "type": "health"},
            {"id": "widget:health", "type": "health"},
        ]))


def test_layout_coerces_and_clamps_numbers():
    s = _store()
    saved = s.save(_view(layout={
        "widget:health": {"x": -50, "y": "80", "width": "nonsense"},
        "junk key with spaces": {"x": 1, "y": 1, "width": 1},
    }))
    slot = saved["layout"]["widget:health"]
    assert slot["x"] == 0.0          # clamped up from -50
    assert slot["y"] == 80.0         # coerced from "80"
    assert slot["width"] == 440.0    # fell back from unparseable
    assert "junk key with spaces" not in saved["layout"]  # dropped, not fatal


def test_rejects_too_many_widgets():
    many = [
        {"id": f"stream:c{i}", "type": "stream", "target": f"c{i}"}
        for i in range(65)
    ]
    with pytest.raises(ViewError):
        _store().save(_view(widgets=many))


# --- delete + default marker --------------------------------------

def test_delete_removes_file_and_returns_flag():
    s = _store()
    s.save(_view("Gone", id="gone"))
    assert s.delete("gone") is True
    assert s.delete("gone") is False
    assert s.list() == []


def test_delete_rejects_bad_id():
    with pytest.raises(ViewError):
        _store().delete("../etc/passwd")


def test_default_id_round_trips_and_clears_on_delete():
    s = _store()
    s.save(_view("Main", id="main"))
    assert s.get_default_id() == ""
    s.set_default_id("main")
    assert s.get_default_id() == "main"
    s.delete("main")
    assert s.get_default_id() == ""


def test_set_default_rejects_unknown_view():
    with pytest.raises(ViewError):
        _store().set_default_id("nope")


def test_set_default_empty_clears():
    s = _store()
    s.save(_view("Main", id="main"))
    s.set_default_id("main")
    s.set_default_id("")
    assert s.get_default_id() == ""


def test_default_marker_not_listed_as_a_view():
    s = _store()
    s.save(_view("Main", id="main"))
    s.set_default_id("main")
    assert [v["id"] for v in s.list()] == ["main"]
    assert os.path.exists(os.path.join(s.dir, "_meta.json"))
