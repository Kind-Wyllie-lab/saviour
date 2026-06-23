"""
Tests for src/modules/config.py

Covers merge helpers, set_all behaviour, and thread safety.
"""

import json
import threading
import tempfile
import os
import pytest

from src.modules.config import Config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(base: dict = None, active: dict = None) -> Config:
    """Return a Config backed by temp JSON files."""
    tmpdir = tempfile.mkdtemp()
    base_path   = os.path.join(tmpdir, "base_config.json")
    active_path = os.path.join(tmpdir, "active_config.json")

    with open(base_path, "w") as f:
        json.dump(base or {}, f)

    if active is not None:
        with open(active_path, "w") as f:
            json.dump(active, f)

    cfg = Config(base_config_path=base_path, active_config_path=active_path)
    # set_all requires these to be initialised
    cfg.module_config_keys = set()
    cfg.configure_module = lambda *_: None
    return cfg


def _make_config_with_module(base: dict, module: dict,
                              active: dict = None) -> Config:
    """Return a Config that also loads a module config file."""
    tmpdir      = tempfile.mkdtemp()
    base_path   = os.path.join(tmpdir, "base_config.json")
    active_path = os.path.join(tmpdir, "active_config.json")
    module_path = os.path.join(tmpdir, "module_config.json")

    with open(base_path,   "w") as f: json.dump(base,   f)
    with open(module_path, "w") as f: json.dump(module, f)
    if active is not None:
        with open(active_path, "w") as f: json.dump(active, f)

    cfg = Config(base_config_path=base_path, active_config_path=active_path)
    cfg.module_config_keys = set()
    cfg.configure_module = lambda *_: None
    cfg.on_module_config_change = lambda *_: None
    cfg.load_module_config(module_path)
    return cfg


# ---------------------------------------------------------------------------
# _merge_defaults
# ---------------------------------------------------------------------------

class TestMergeDefaults:
    def setup_method(self):
        self.cfg = _make_config()

    def test_fills_missing_keys(self):
        target = {"a": 1}
        self.cfg._merge_defaults(target, {"a": 99, "b": 2})
        assert target == {"a": 1, "b": 2}

    def test_does_not_overwrite_existing(self):
        target = {"a": 1, "b": "keep"}
        self.cfg._merge_defaults(target, {"a": 99, "b": "default", "c": 3})
        assert target["a"] == 1
        assert target["b"] == "keep"
        assert target["c"] == 3

    def test_recursive_fill(self):
        target = {"camera": {"fps": 30}}
        self.cfg._merge_defaults(target, {"camera": {"fps": 60, "width": 1920}})
        assert target["camera"]["fps"] == 30       # unchanged
        assert target["camera"]["width"] == 1920   # filled

    def test_nested_dict_not_replaced_by_scalar(self):
        target = {"camera": {"fps": 30}}
        self.cfg._merge_defaults(target, {"camera": "replaced"})
        # target has a dict, defaults has a scalar — target wins
        assert isinstance(target["camera"], dict)

    def test_empty_defaults_is_noop(self):
        target = {"a": 1}
        self.cfg._merge_defaults(target, {})
        assert target == {"a": 1}


# ---------------------------------------------------------------------------
# _merge_internal_defaults
# ---------------------------------------------------------------------------

class TestMergeInternalDefaults:
    def setup_method(self):
        self.cfg = _make_config()

    def test_overwrites_private_keys(self):
        target = {"_codec": "old", "public": "keep"}
        self.cfg._merge_internal_defaults(target, {"_codec": "new", "public": "different"})
        assert target["_codec"] == "new"

    def test_leaves_public_keys_unchanged(self):
        target = {"_codec": "old", "public": "keep"}
        self.cfg._merge_internal_defaults(target, {"_codec": "new", "public": "different"})
        assert target["public"] == "keep"

    def test_recursive_private_overwrite(self):
        target  = {"camera": {"_internal": "old", "fps": 30}}
        source  = {"camera": {"_internal": "new", "fps": 999}}
        self.cfg._merge_internal_defaults(target, source)
        assert target["camera"]["_internal"] == "new"
        assert target["camera"]["fps"] == 30     # public key untouched

    def test_missing_private_key_added(self):
        target = {"public": "x"}
        self.cfg._merge_internal_defaults(target, {"_new_key": "v"})
        assert target["_new_key"] == "v"


# ---------------------------------------------------------------------------
# _merge_dicts (full override)
# ---------------------------------------------------------------------------

class TestMergeDicts:
    def setup_method(self):
        self.cfg = _make_config()

    def test_overwrites_existing_value(self):
        base = {"a": 1}
        self.cfg._merge_dicts(base, {"a": 99})
        assert base["a"] == 99

    def test_adds_new_keys(self):
        base = {"a": 1}
        self.cfg._merge_dicts(base, {"b": 2})
        assert base == {"a": 1, "b": 2}

    def test_recursive_merge(self):
        base     = {"camera": {"fps": 30, "width": 1920}}
        override = {"camera": {"fps": 60}}
        self.cfg._merge_dicts(base, override)
        assert base["camera"]["fps"] == 60
        assert base["camera"]["width"] == 1920  # not in override, preserved


# ---------------------------------------------------------------------------
# set_all
# ---------------------------------------------------------------------------

class TestSetAll:
    def setup_method(self):
        self.cfg = _make_config(base={"camera": {"fps": 30, "width": 1920}})
        self.cfg.config = {"camera": {"fps": 30, "width": 1920}}

    def test_updates_existing_value(self):
        self.cfg.set_all({"camera": {"fps": 60, "width": 1920}})
        assert self.cfg.config["camera"]["fps"] == 60

    def test_stale_non_private_key_removed(self):
        # The frontend sends a config that no longer includes "width" — it should
        # be deleted from the live config (not silently retained).
        self.cfg.set_all({"camera": {"fps": 60}})
        assert "width" not in self.cfg.config["camera"]

    def test_stale_private_key_preserved(self):
        self.cfg.config = {"camera": {"fps": 30, "_internal": "keep"}}
        self.cfg.set_all({"camera": {"fps": 60}})
        # _-prefixed keys must survive even when absent from the update
        assert self.cfg.config["camera"]["_internal"] == "keep"

    def test_new_key_added(self):
        self.cfg.set_all({"camera": {"fps": 30, "width": 1920, "bitrate": 10}})
        assert self.cfg.config["camera"]["bitrate"] == 10

    def test_persist_saves_to_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            active_path = os.path.join(tmpdir, "active_config.json")
            base_path   = os.path.join(tmpdir, "base_config.json")
            with open(base_path, "w") as f:
                json.dump({}, f)
            cfg = Config(base_config_path=base_path, active_config_path=active_path)
            cfg.module_config_keys = set()
            cfg.configure_module = lambda *_: None
            cfg.set_all({"key": "value"}, persist=True)
            with open(active_path) as f:
                saved = json.load(f)
            assert saved.get("key") == "value"


# ---------------------------------------------------------------------------
# set_all — thread safety
# ---------------------------------------------------------------------------

class TestSetAllThreadSafety:
    """Concurrent set_all calls must not corrupt the config dict.

    Each worker claims one key and sets it to its own thread index 1000 times.
    After all writes, each key must hold a single consistent integer value,
    not a partially-written structure.
    """

    def test_concurrent_writes_do_not_corrupt(self):
        cfg = _make_config()
        cfg.config = {}
        errors = []
        n_threads = 8
        n_iters = 200

        def worker(idx: int):
            for _ in range(n_iters):
                try:
                    cfg.set_all({f"thread_{idx}": idx}, persist=False)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Exceptions during concurrent set_all: {errors}"
        for i in range(n_threads):
            assert cfg.config.get(f"thread_{i}") == i


# ---------------------------------------------------------------------------
# _prune_stale_keys
# ---------------------------------------------------------------------------

class TestPruneStaleKeys:
    def setup_method(self):
        self.cfg = _make_config()

    def test_removes_key_absent_from_reference(self):
        target    = {"a": 1, "stale": "gone"}
        reference = {"a": 1}
        self.cfg._prune_stale_keys(target, reference)
        assert "stale" not in target
        assert target["a"] == 1

    def test_recursive_prune(self):
        target    = {"camera": {"fps": 30, "old_key": 5}}
        reference = {"camera": {"fps": 30}}
        self.cfg._prune_stale_keys(target, reference)
        assert "old_key" not in target["camera"]
        assert target["camera"]["fps"] == 30

    def test_private_keys_not_pruned(self):
        target    = {"_codec": "h264", "public": "keep"}
        reference = {"public": "keep"}  # _codec absent from reference
        self.cfg._prune_stale_keys(target, reference)
        assert target["_codec"] == "h264"

    def test_key_in_reference_kept(self):
        target    = {"a": 1, "b": 2}
        reference = {"a": 1, "b": 99}  # different value, but key present
        self.cfg._prune_stale_keys(target, reference)
        assert target == {"a": 1, "b": 2}

    def test_empty_target_is_noop(self):
        target    = {}
        reference = {"a": 1}
        self.cfg._prune_stale_keys(target, reference)
        assert target == {}

    def test_prunes_entire_stale_section(self):
        target    = {"camera": {"fps": 30}, "old_section": {"x": 1}}
        reference = {"camera": {"fps": 30}}
        self.cfg._prune_stale_keys(target, reference)
        assert "old_section" not in target


# ---------------------------------------------------------------------------
# load_module_config — stale key pruning on subsequent restarts
# ---------------------------------------------------------------------------

class TestLoadModuleConfigStalePruning:
    def test_stale_module_key_pruned_after_module_update(self):
        """Key removed from module config must disappear from active on next load."""
        base   = {"base_key": 1}
        # v1 module had "old_param"; user ran system with it, value in active
        active = {"base_key": 1, "audiomoth": {"sample_rate": 96000, "old_param": 5}}
        # v2 module no longer has "old_param"
        module = {"audiomoth": {"sample_rate": 192000}}
        cfg = _make_config_with_module(base, module, active)
        assert "old_param" not in cfg.config.get("audiomoth", {})
        assert cfg.config["audiomoth"]["sample_rate"] == 96000  # user value kept

    def test_user_value_for_valid_key_preserved(self):
        """A user-modified value for a key that still exists must survive."""
        base   = {"network": {"timeout": 30}}
        active = {"network": {"timeout": 10}}   # user changed to 10
        module = {"audiomoth": {"sample_rate": 192000}}
        cfg = _make_config_with_module(base, module, active)
        assert cfg.config["network"]["timeout"] == 10

    def test_stale_base_key_pruned_after_base_update(self):
        """Key removed from base config must not survive in active."""
        # Simulate: old base had "legacy_key"; new base doesn't
        old_active = {"legacy_key": "old", "current_key": 1}
        new_base   = {"current_key": 1}
        module     = {"module_key": 2}
        cfg = _make_config_with_module(new_base, module, old_active)
        assert "legacy_key" not in cfg.config
        assert cfg.config["current_key"] == 1

    def test_private_keys_in_active_survive_pruning(self):
        """Internal _-prefixed keys in active must not be pruned."""
        base   = {"a": 1}
        active = {"a": 1, "_internal": "keep_me"}
        module = {"b": 2}
        cfg = _make_config_with_module(base, module, active)
        assert cfg.config.get("_internal") == "keep_me"

    def test_first_run_no_active_loads_all_module_keys(self):
        """Fresh install (no active config) must inherit all module defaults."""
        base   = {"base_key": 1}
        module = {"module_key": 42, "nested": {"x": 7}}
        cfg = _make_config_with_module(base, module, active=None)
        assert cfg.config["module_key"] == 42
        assert cfg.config["nested"]["x"] == 7


# ---------------------------------------------------------------------------
# reset_to_defaults
# ---------------------------------------------------------------------------

class TestResetToDefaults:
    def test_restores_base_default_values(self):
        """User-modified values must return to base defaults after reset."""
        base   = {"fps": 30, "width": 1920}
        active = {"fps": 60, "width": 3840}  # user changed both
        cfg = _make_config(base=base, active=active)
        cfg.reset_to_defaults()
        assert cfg.config["fps"]   == 30
        assert cfg.config["width"] == 1920

    def test_purges_stale_key_not_in_base(self):
        """A key in active that's absent from base must be gone after reset."""
        base   = {"fps": 30}
        active = {"fps": 60, "stale_key": "gone"}
        cfg = _make_config(base=base, active=active)
        cfg.reset_to_defaults()
        assert "stale_key" not in cfg.config

    def test_purges_stale_module_key_after_module_update(self):
        """Key removed from module config must be absent after reset."""
        tmpdir      = tempfile.mkdtemp()
        base_path   = os.path.join(tmpdir, "base_config.json")
        active_path = os.path.join(tmpdir, "active_config.json")
        module_path = os.path.join(tmpdir, "module_config.json")

        with open(base_path,   "w") as f: json.dump({"base": 1}, f)
        # v2 module: old_param removed
        with open(module_path, "w") as f: json.dump({"sample_rate": 192000}, f)
        # active from v1 still has old_param
        with open(active_path, "w") as f: json.dump({"base": 1, "sample_rate": 96000, "old_param": 5}, f)

        cfg = Config(base_config_path=base_path, active_config_path=active_path)
        cfg.module_config_keys = set()
        cfg.configure_module = lambda *_: None
        cfg.reset_to_defaults(module_config_path=module_path)

        assert "old_param" not in cfg.config
        assert cfg.config["sample_rate"] == 192000

    def test_active_file_recreated_with_defaults(self):
        """After reset, active_config.json must exist and contain base values."""
        base   = {"fps": 30}
        active = {"fps": 99}
        cfg = _make_config(base=base, active=active)
        cfg.reset_to_defaults()
        assert os.path.exists(cfg.active_config_path)
        with open(cfg.active_config_path) as f:
            saved = json.load(f)
        assert saved["fps"] == 30

    def test_reset_without_active_file(self):
        """reset_to_defaults must work cleanly when no active config exists."""
        base = {"fps": 30}
        cfg  = _make_config(base=base, active=None)
        cfg.reset_to_defaults()  # should not raise
        assert cfg.config["fps"] == 30

    def test_internal_keys_from_module_applied_after_reset(self):
        """_-prefixed keys in module config must be present after reset."""
        tmpdir      = tempfile.mkdtemp()
        base_path   = os.path.join(tmpdir, "base_config.json")
        active_path = os.path.join(tmpdir, "active_config.json")
        module_path = os.path.join(tmpdir, "module_config.json")

        with open(base_path,   "w") as f: json.dump({}, f)
        with open(module_path, "w") as f: json.dump({"_codec": "h264", "fps": 30}, f)
        with open(active_path, "w") as f: json.dump({"_codec": "old", "fps": 60}, f)

        cfg = Config(base_config_path=base_path, active_config_path=active_path)
        cfg.module_config_keys = set()
        cfg.configure_module = lambda *_: None
        cfg.reset_to_defaults(module_config_path=module_path)

        assert cfg.config["_codec"] == "h264"
        assert cfg.config["fps"] == 30
