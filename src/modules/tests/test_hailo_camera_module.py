"""Tests for the hailo.infer_enabled toggle in HailoCameraModule.

Only the config-driven enable/disable path is covered here -- it is pure
Python and testable via the __new__ construction pattern (no Hailo device,
no camera). The inference decoders themselves live in test_hailo_infer.py.

The toggle exists to characterise how much the preview-inference thread
costs the H264 encoder on the sync-client camera -- see
plans/multicam-frame-alignment-and-sync-provenance.md.
"""

import logging
import threading
from unittest.mock import MagicMock

from src.modules.variants.hailo_camera.hailo_camera_module import HailoCameraModule


def _module(infer_enabled=True, model="yolov8s"):
    """A HailoCameraModule with just the attributes the toggle path touches."""
    m = HailoCameraModule.__new__(HailoCameraModule)
    m.logger = logging.getLogger("test_hailo")
    m.config = MagicMock()
    cfg = {"hailo.infer_enabled": infer_enabled, "hailo.model": model}
    m.config.get.side_effect = lambda k, d=None: cfg.get(k, d)
    m._rebuild_lock = threading.Lock()
    m._det_lock = threading.Lock()
    m.detector = None
    m._detector_error = None
    m._model_key = model
    m._rebuilding = False
    return m


class TestInferEnabledFlag:
    def test_defaults_true_when_key_absent(self):
        m = _module()
        m.config.get.side_effect = lambda k, d=None: d  # nothing configured
        assert m._infer_enabled() is True

    def test_reads_explicit_false(self):
        assert _module(infer_enabled=False)._infer_enabled() is False

    def test_reads_explicit_true(self):
        assert _module(infer_enabled=True)._infer_enabled() is True


class TestBuildDetectorDisabled:
    def test_disabled_skips_load_and_leaves_no_detector(self):
        m = _module(infer_enabled=False)
        m._build_detector()
        assert m.detector is None
        assert "hailo.infer_enabled=false" in m._detector_error
        # model_key still tracked so the UI/check can name the selection
        assert m._model_key == "yolov8s"

    def test_disabled_tears_down_a_live_detector(self):
        m = _module(infer_enabled=False)
        live = MagicMock()
        m.detector = live
        m._build_detector(swap=True)
        assert m.detector is None
        live.close.assert_called_once()

    def test_disabled_swallows_close_error(self):
        m = _module(infer_enabled=False)
        live = MagicMock()
        live.close.side_effect = RuntimeError("device busy")
        m.detector = live
        m._build_detector(swap=True)  # must not raise
        assert m.detector is None


class TestConfigureModuleExtra:
    def test_infer_enabled_change_triggers_rebuild(self):
        m = _module()
        m._rebuild_detector_async = MagicMock()
        m._apply_light_config = MagicMock()
        m._configure_module_extra({"hailo.infer_enabled"})
        m._rebuild_detector_async.assert_called_once()
        m._apply_light_config.assert_not_called()

    def test_light_key_still_takes_the_in_place_path(self):
        m = _module()
        m._rebuild_detector_async = MagicMock()
        m._apply_light_config = MagicMock()
        m._configure_module_extra({"hailo.threshold"})
        m._apply_light_config.assert_called_once()
        m._rebuild_detector_async.assert_not_called()

    def test_non_hailo_keys_are_ignored(self):
        m = _module()
        m._rebuild_detector_async = MagicMock()
        m._apply_light_config = MagicMock()
        m._configure_module_extra({"camera.fps"})
        m._rebuild_detector_async.assert_not_called()
        m._apply_light_config.assert_not_called()
