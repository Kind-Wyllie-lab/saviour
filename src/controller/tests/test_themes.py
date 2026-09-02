"""Tests for src/controller/themes.py (ThemeStore).

Pure file I/O against a temp directory -- no root/hardware needed.
"""

import json
import os
import tempfile

import pytest

from src.controller.themes import TOKEN_KEYS, ThemeError, ThemeStore


def _store() -> ThemeStore:
    return ThemeStore(themes_dir=os.path.join(tempfile.mkdtemp(), "themes"))


def _map(color: str = "#123456") -> dict:
    return {k: color for k in TOKEN_KEYS}


def _theme(name: str = "Ocean", **over) -> dict:
    base = {"name": name, "light": _map("#eef4f7"), "dark": _map("#0f1b22")}
    base.update(over)
    return base


# --- save / list round-trip ------------------------------------------------

def test_save_then_list_round_trips():
    s = _store()
    saved = s.save(_theme("Ocean Blue"))
    assert saved["id"] == "ocean-blue"
    assert saved["light"]["--bg-color"] == "#eef4f7"

    listed = s.list()
    assert len(listed) == 1
    assert listed[0]["name"] == "Ocean Blue"
    assert listed[0]["dark"]["--bg-color"] == "#0f1b22"

    # File is on disk, plain JSON.
    with open(os.path.join(s.dir, "ocean-blue.json")) as f:
        assert json.load(f)["id"] == "ocean-blue"


def test_list_empty_when_dir_missing():
    assert _store().list() == []


def test_list_sorted_by_name():
    s = _store()
    s.save(_theme("Zebra"))
    s.save(_theme("apple"))
    assert [t["name"] for t in s.list()] == ["apple", "Zebra"]


# --- id handling ---------------------------------------------------------

def test_reserved_id_rejected():
    s = _store()
    with pytest.raises(ThemeError):
        s.save(_theme("Default"))
    with pytest.raises(ThemeError):
        s.save(_theme("whatever", id="sidb"))


def test_slug_collision_gets_suffixed():
    s = _store()
    a = s.save(_theme("My Theme"))
    b = s.save(_theme("My Theme!!"))  # slugs to the same base
    assert a["id"] == "my-theme"
    assert b["id"] == "my-theme-2"
    assert {t["id"] for t in s.list()} == {"my-theme", "my-theme-2"}


# --- validation --------------------------------------------------------

def test_non_hex_value_rejected():
    s = _store()
    bad = _theme()
    bad["light"]["--accent-color"] = "red; } body { display: none"
    with pytest.raises(ThemeError):
        s.save(bad)


def test_missing_token_key_rejected():
    s = _store()
    bad = _theme()
    del bad["dark"]["--border-color"]
    with pytest.raises(ThemeError):
        s.save(bad)


def test_missing_mode_block_rejected():
    s = _store()
    with pytest.raises(ThemeError):
        s.save({"name": "Half", "light": _map()})


def test_blank_name_rejected():
    s = _store()
    with pytest.raises(ThemeError):
        s.save(_theme("   "))


def test_unknown_keys_stripped():
    s = _store()
    payload = _theme("Trimmed")
    payload["light"]["--evil"] = "#000000"
    payload["extra"] = "ignored"
    saved = s.save(payload)
    assert "--evil" not in saved["light"]
    assert "extra" not in saved
    assert set(saved["light"]) == set(TOKEN_KEYS)


def test_short_hex_accepted():
    s = _store()
    payload = _theme("Short")
    payload["light"] = {k: "#abc" for k in TOKEN_KEYS}
    saved = s.save(payload)
    assert saved["light"]["--bg-color"] == "#abc"


# --- delete ----------------------------------------------------------

def test_delete_removes_file():
    s = _store()
    s.save(_theme("Gone"))
    assert s.delete("gone") is True
    assert s.list() == []
    assert s.delete("gone") is False  # already gone


def test_delete_reserved_rejected():
    s = _store()
    with pytest.raises(ThemeError):
        s.delete("default")


def test_delete_bad_id_rejected():
    s = _store()
    with pytest.raises(ThemeError):
        s.delete("../etc/passwd")


# --- resilience ------------------------------------------------------

def test_bad_file_in_dir_is_skipped(caplog):
    s = _store()
    s.save(_theme("Good"))
    os.makedirs(s.dir, exist_ok=True)
    with open(os.path.join(s.dir, "broken.json"), "w") as f:
        f.write("{not json")
    with open(os.path.join(s.dir, "wrongshape.json"), "w") as f:
        json.dump({"name": "x"}, f)
    listed = s.list()
    assert [t["name"] for t in listed] == ["Good"]
