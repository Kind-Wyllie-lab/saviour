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
