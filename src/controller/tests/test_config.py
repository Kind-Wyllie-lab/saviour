"""
Tests for src/controller/config.py.

Config.__init__ only does file I/O (no root/hardware requirements), so
every test constructs a real Config backed by temp JSON files -- same
pattern as src/modules/tests/test_config.py for the module-side
equivalent. on_controller_config_change is stubbed the same way that file
stubs configure_module, since it's assigned externally by controller.py
after construction.
"""

import json
import os
import tempfile
from unittest.mock import patch

from src.controller.config import Config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(base: dict = None, active: dict = None) -> Config:
    tmpdir = tempfile.mkdtemp()
    base_path = os.path.join(tmpdir, "base_config.json")
    active_path = os.path.join(tmpdir, "active_config.json")

    with open(base_path, "w") as f:
        json.dump(base or {}, f)
    if active is not None:
        with open(active_path, "w") as f:
            json.dump(active, f)

    cfg = Config(base_config_path=base_path, active_config_path=active_path)
    cfg.on_controller_config_change = lambda *_: None
    return cfg


# ---------------------------------------------------------------------------
# Merge helpers
# ---------------------------------------------------------------------------

class TestMergeDefaults:
    def setup_method(self):
        self.cfg = _make_config()

    def test_fills_missing_keys_without_overwriting_existing(self):
        target = {"a": 1, "b": "keep"}
        self.cfg._merge_defaults(target, {"a": 99, "b": "default", "c": 3})
        assert target == {"a": 1, "b": "keep", "c": 3}

    def test_recurses_into_nested_dicts(self):
        target = {"camera": {"fps": 30}}
        self.cfg._merge_defaults(target, {"camera": {"fps": 99, "gain": 1.0}})
        assert target == {"camera": {"fps": 30, "gain": 1.0}}


class TestMergeDicts:
    def setup_method(self):
        self.cfg = _make_config()

    def test_override_replaces_existing_values(self):
        base = {"a": 1, "b": 2}
        self.cfg._merge_dicts(base, {"a": 99})
        assert base == {"a": 99, "b": 2}

    def test_recurses_and_adds_new_nested_keys(self):
        base = {"camera": {"fps": 30}}
        self.cfg._merge_dicts(base, {"camera": {"gain": 1.0}, "new_top": True})
        assert base == {"camera": {"fps": 30, "gain": 1.0}, "new_top": True}


class TestFlattenKeys:
    def test_flattens_nested_dict_to_dotted_keys(self):
        cfg = _make_config()
        keys = cfg._flatten_keys({"a": 1, "b": {"c": 2, "d": {"e": 3}}})
        assert keys == {"a", "b.c", "b.d.e"}


# ---------------------------------------------------------------------------
# get / set
# ---------------------------------------------------------------------------

class TestGet:
    def test_returns_nested_value(self):
        cfg = _make_config(active={"camera": {"fps": 30}})
        assert cfg.get("camera.fps") == 30

    def test_missing_key_returns_default(self):
        cfg = _make_config(active={})
        assert cfg.get("ghost.key", "fallback") == "fallback"

    def test_falls_back_to_underscore_prefixed_internal_key(self):
        cfg = _make_config(active={"camera": {"_codec": "h264"}})
        assert cfg.get("camera.codec") == "h264"

    def test_non_dict_intermediate_returns_default(self):
        cfg = _make_config(active={"camera": "not_a_dict"})
        assert cfg.get("camera.fps", "default") == "default"


class TestSet:
    def test_sets_nested_value_and_persists(self):
        cfg = _make_config(active={})
        assert cfg.set("camera.fps", 60) is True
        assert cfg.get("camera.fps") == 60
        with open(cfg.active_config_path) as f:
            assert json.load(f)["camera"]["fps"] == 60

    def test_rejects_private_key(self):
        cfg = _make_config(active={"camera": {"_codec": "h264"}})
        assert cfg.set("camera._codec", "h265") is False
        assert cfg.get("camera._codec") == "h264"  # unchanged

    def test_no_persist_skips_save(self):
        cfg = _make_config(active={})
        cfg.set("camera.fps", 60, persist=False)
        with open(cfg.active_config_path) as f:
            assert json.load(f) == {}

    def test_notifies_controller_config_change_for_tracked_key(self):
        cfg = _make_config(active={})
        cfg.controller_config_keys = {"apa.shock_duration_ms"}
        calls = []
        cfg.on_controller_config_change = lambda keys: calls.append(keys)
        cfg.set("apa.shock_duration_ms", 500)
        assert calls == [["apa.shock_duration_ms"]]


class TestGetAll:
    def test_returns_a_copy(self):
        cfg = _make_config(active={"a": 1})
        result = cfg.get_all()
        result["a"] = 999
        assert cfg.config["a"] == 1


# ---------------------------------------------------------------------------
# set_all
# ---------------------------------------------------------------------------

class TestSetAll:
    def test_updates_existing_nested_value(self):
        cfg = _make_config(active={"camera": {"fps": 30}})
        cfg.set_all({"camera": {"fps": 60}})
        assert cfg.get("camera.fps") == 60

    def test_unchanged_value_is_left_alone_and_not_flagged(self):
        cfg = _make_config(active={"camera": {"fps": 30}})
        cfg.controller_config_keys = {"camera.fps"}
        calls = []
        cfg.on_controller_config_change = lambda keys: calls.append(keys)
        cfg.set_all({"camera": {"fps": 30}})
        assert calls == []

    def test_notifies_for_changed_controller_specific_keys(self):
        cfg = _make_config(active={"apa": {"shock_duration_ms": 100}})
        cfg.controller_config_keys = {"apa.shock_duration_ms"}
        calls = []
        cfg.on_controller_config_change = lambda keys: calls.append(keys)
        cfg.set_all({"apa": {"shock_duration_ms": 200}})
        assert calls == [["apa.shock_duration_ms"]]

    def test_persist_true_writes_active_config(self):
        cfg = _make_config(active={"camera": {"fps": 30}})
        cfg.set_all({"camera": {"fps": 60}}, persist=True)
        with open(cfg.active_config_path) as f:
            assert json.load(f)["camera"]["fps"] == 60

    def test_accepts_a_genuinely_new_top_level_key(self):
        """`elif target.get(k) != v` (~line 303) uses .get(k), matching the
        module-side Config.set_all (src/modules/config.py) fix — a config key
        that doesn't exist yet in an existing device's active_config.json is
        added rather than raising KeyError. Reachable live: web.py's
        save_controller_config handler calls facade.set_config ->
        Config.set_all with whatever the frontend sends."""
        cfg = _make_config(active={"camera": {"fps": 30}})
        cfg.set_all({"brand_new_top_level_key": True})
        assert cfg.get("brand_new_top_level_key") is True

    def test_accepts_a_genuinely_new_nested_key(self):
        cfg = _make_config(active={"camera": {"fps": 30}})
        cfg.set_all({"camera": {"brand_new_nested_key": True}})
        assert cfg.get("camera.brand_new_nested_key") is True


# ---------------------------------------------------------------------------
# load_controller_config
# ---------------------------------------------------------------------------

class TestLoadControllerConfig:
    def test_missing_file_only_warns(self):
        cfg = _make_config(active={})
        cfg.load_controller_config("/no/such/controller_config.json")
        assert cfg.get_all() == {}

    def test_no_active_config_does_full_merge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            controller_path = os.path.join(tmpdir, "apa_controller_config.json")
            with open(controller_path, "w") as f:
                json.dump({"apa": {"shock_duration_ms": 500}}, f)

            cfg = _make_config()  # no active config -> built from empty base
            cfg.load_controller_config(controller_path)

            assert cfg.get("apa.shock_duration_ms") == 500
            assert cfg.controller_config_keys == {"apa.shock_duration_ms"}

    def test_existing_active_config_only_fills_missing_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            controller_path = os.path.join(tmpdir, "apa_controller_config.json")
            with open(controller_path, "w") as f:
                json.dump({"apa": {"shock_duration_ms": 500}}, f)

            cfg = _make_config(active={"apa": {"shock_duration_ms": 999}})
            cfg.load_controller_config(controller_path)

            assert cfg.get("apa.shock_duration_ms") == 999  # untouched


# ---------------------------------------------------------------------------
# reset_to_defaults
# ---------------------------------------------------------------------------

class TestResetToDefaults:
    def test_rebuilds_from_base_and_removes_active_file(self):
        cfg = _make_config(base={"a": 1}, active={"a": 999, "b": 2})
        assert os.path.exists(cfg.active_config_path)

        cfg.reset_to_defaults()

        assert cfg.get_all() == {"a": 1}
        with open(cfg.active_config_path) as f:
            assert json.load(f) == {"a": 1}

    def test_merges_in_controller_config_when_given(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            controller_path = os.path.join(tmpdir, "controller_config.json")
            with open(controller_path, "w") as f:
                json.dump({"apa": {"shock_duration_ms": 500}}, f)

            cfg = _make_config(base={"a": 1}, active={"a": 999})
            cfg.reset_to_defaults(controller_config_path=controller_path)

            assert cfg.get_all() == {"a": 1, "apa": {"shock_duration_ms": 500}}


# ---------------------------------------------------------------------------
# One-time active-config migration from the old in-tree path
# ---------------------------------------------------------------------------

class TestOldActiveConfigMigration:
    def test_migrates_from_old_path_when_new_path_absent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            old_path = os.path.join(tmpdir, "old_active_config.json")
            with open(old_path, "w") as f:
                json.dump({"migrated": True}, f)

            base_path = os.path.join(tmpdir, "base_config.json")
            with open(base_path, "w") as f:
                json.dump({}, f)
            new_active_path = os.path.join(tmpdir, "new", "active_config.json")

            with patch("src.controller.config._OLD_ACTIVE_CONFIG_PATH", old_path):
                cfg = Config(
                    base_config_path=base_path, active_config_path=new_active_path
                )

            assert cfg.get("migrated") is True
            assert os.path.exists(new_active_path)
