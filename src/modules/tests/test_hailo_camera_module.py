"""Tests for HailoCameraModule -- the hailo.infer_enabled toggle and the
inference worker thread (docs/CHANGELOG.md, 2026-09-08).

Covered via the __new__ construction pattern (no Hailo device, no camera).
The inference decoders themselves live in test_hailo_infer.py.
"""

import logging
import queue
import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np

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


# ---------------------------------------------------------------------------
# inference worker thread
# ---------------------------------------------------------------------------

class _Shim:
    __slots__ = ("array",)

    def __init__(self, arr):
        self.array = arr


def _worker_module(detector=None, infer_every_n=1):
    m = HailoCameraModule.__new__(HailoCameraModule)
    m.logger = logging.getLogger("test_hailo")
    m._det_lock = threading.Lock()
    m._infer_q = queue.Queue(maxsize=1)
    m._infer_stop = threading.Event()
    m._infer_worker = None
    m.detector = detector
    m._labels = ["rat"]
    m._model_key = "yolov8s"
    m._task = "detection"
    m._rebuilding = False
    m._detector_error = None
    m._infer_error_logged = False
    m._infer_counter = 0
    m._infer_every_n = infer_every_n
    m._max_labels = 40
    m._last_results = []
    m._last_summary = ""
    m._infer_ms = __import__("collections").deque(maxlen=600)
    m._infer_ms_logged_at = 0.0
    return m


class TestProcessLoresFrameHandsOff:
    def _frame(self):
        return _Shim(np.zeros((48, 64, 3), dtype=np.uint8))

    def test_pushes_a_frame_copy_and_never_calls_detect(self):
        det = MagicMock()
        m = _worker_module(detector=det)
        f = self._frame()
        m._process_lores_frame(f, timing=None)
        det.detect.assert_not_called()                 # detect() is worker-only
        queued = m._infer_q.get_nowait()
        assert queued.shape == (48, 64, 3)
        queued[0, 0, 0] = 255
        assert f.array[0, 0, 0] == 0                    # it's a copy

    def test_respects_infer_every_n(self):
        m = _worker_module(detector=MagicMock(), infer_every_n=3)
        for _ in range(3):
            m._process_lores_frame(self._frame(), timing=None)
        assert m._infer_q.qsize() == 1                  # only the 3rd frame

    def test_drop_oldest_keeps_only_the_freshest(self):
        m = _worker_module(detector=MagicMock())
        a = _Shim(np.full((4, 4, 3), 1, np.uint8))
        b = _Shim(np.full((4, 4, 3), 2, np.uint8))
        m._process_lores_frame(a, timing=None)
        m._process_lores_frame(b, timing=None)
        assert m._infer_q.get_nowait()[0, 0, 0] == 2
        assert m._infer_q.empty()

    def test_no_detector_draws_status_and_does_not_push(self):
        m = _worker_module(detector=None)
        m._rebuilding = True
        m._process_lores_frame(self._frame(), timing=None)
        assert m._infer_q.empty()

    def test_draws_last_results_from_the_worker(self):
        m = _worker_module(detector=MagicMock())
        m._draw_detections = MagicMock(return_value="1x rat")
        m._status_line = MagicMock()
        m._last_results = ["fake-det"]
        m._process_lores_frame(self._frame(), timing=None)
        m._draw_detections.assert_called_once()
        assert m._draw_detections.call_args[0][1] == ["fake-det"]


class TestInferenceWorker:
    def _run_once(self, m):
        m._start_inference_worker()
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not m._infer_error_logged \
                    and m._last_results == []:
                time.sleep(0.02)
        finally:
            m._stop_inference_worker(timeout=2.0)

    def test_runs_detect_and_publishes_results(self):
        det = MagicMock()
        det.detect.return_value = ["d1", "d2"]
        m = _worker_module(detector=det)
        m._infer_q.put(np.zeros((4, 4, 3), np.uint8))
        self._run_once(m)
        det.detect.assert_called()
        assert m._last_results == ["d1", "d2"]

    def test_detect_exception_clears_results_and_logs_once(self):
        det = MagicMock()
        det.detect.side_effect = RuntimeError("hef blew up")
        m = _worker_module(detector=det)
        m._infer_q.put(np.zeros((4, 4, 3), np.uint8))
        m.logger = MagicMock()
        self._run_once(m)
        assert m._last_results == []
        assert m._infer_error_logged is True

    def test_none_detector_is_a_noop(self):
        m = _worker_module(detector=None)
        m._infer_q.put(np.zeros((4, 4, 3), np.uint8))
        self._run_once(m)
        assert m._last_results == []

    def test_stop_joins_the_thread(self):
        m = _worker_module(detector=MagicMock())
        m._start_inference_worker()
        assert m._infer_worker.is_alive()
        m._stop_inference_worker(timeout=2.0)
        assert m._infer_worker is None


class TestStopOrdering:
    def test_worker_stopped_before_detector_closed(self):
        m = _worker_module(detector=MagicMock())
        order = []

        def _stop(*a, **k):
            order.append("worker")
            return True

        m._stop_inference_worker = MagicMock(side_effect=_stop)
        m.detector.close.side_effect = lambda: order.append("close")
        camera_base_cls = HailoCameraModule.__mro__[1]   # CameraBase (dual import path)
        with patch.object(camera_base_cls, "stop",
                          MagicMock(return_value=True)) as super_stop:
            assert HailoCameraModule.stop(m) is True
        assert order == ["worker", "close"]   # worker down before device close
        assert m.detector is None
        super_stop.assert_called_once()

    def test_wedged_worker_skips_the_close(self):
        m = _worker_module(detector=MagicMock())
        m._stop_inference_worker = MagicMock(return_value=False)  # didn't stop
        camera_base_cls = HailoCameraModule.__mro__[1]
        with patch.object(camera_base_cls, "stop", MagicMock(return_value=True)):
            HailoCameraModule.stop(m)
        m.detector.close.assert_not_called()   # never close a running device


# --------------------------------------------------------------------------- #
# Uploaded HEFs: model resolution, class names, list / fetch / delete
# --------------------------------------------------------------------------- #

def _upload_module(tmp_path, monkeypatch, cfg):
    from src.modules.variants.hailo_camera import hailo_camera_module as mod
    monkeypatch.setattr(mod, "USER_HEF_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "HAILO_DEVICE", str(tmp_path / "no-device"))
    m = _module()
    m.config.get.side_effect = lambda k, d=None: cfg.get(k, d)
    m._rebuild_detector_async = MagicMock()
    return m


class TestUploadedModels:
    def test_curated_key_resolves_to_the_registry(self, tmp_path, monkeypatch):
        m = _upload_module(tmp_path, monkeypatch, {})
        key, spec = m._model_spec("yolov8s")
        assert key == "yolov8s" and not spec.get("uploaded")

    def test_hef_filename_resolves_to_the_upload_dir_with_its_labels(
            self, tmp_path, monkeypatch):
        m = _upload_module(tmp_path, monkeypatch, {"hailo.custom_labels": [
            {"hef": "other.hef", "labels": ["cat"]},
            {"hef": "ducks.hef", "labels": [" rubber duck ", "goose"]},
        ]})
        key, spec = m._model_spec("ducks.hef")
        assert key == "ducks.hef"
        assert spec["hef"] == str(tmp_path / "ducks.hef")
        assert m._hef_path(spec) == str(tmp_path / "ducks.hef")
        assert spec["task"] == "detection"
        assert spec["labels"] == ["rubber duck", "goose"]

    def test_unnamed_upload_has_no_labels(self, tmp_path, monkeypatch):
        m = _upload_module(tmp_path, monkeypatch, {})
        assert m._model_spec("ducks.hef")[1]["labels"] == []

    def test_unknown_non_hef_key_falls_back_to_default(self, tmp_path, monkeypatch):
        from src.modules.hailo_infer import DEFAULT_MODEL
        m = _upload_module(tmp_path, monkeypatch, {})
        assert m._model_spec("../etc/passwd")[0] == DEFAULT_MODEL

    def test_missing_upload_reports_reupload_not_download_script(
            self, tmp_path, monkeypatch):
        m = _upload_module(tmp_path, monkeypatch, {
            "hailo.infer_enabled": True, "hailo.model": "gone.hef"})
        m._build_detector()
        assert m.detector is None
        assert "upload it again" in m._detector_error
        assert m._model_key == "gone.hef"

    def test_label_edit_applies_without_a_reload(self, tmp_path, monkeypatch):
        cfg = {"hailo.custom_labels": [{"hef": "ducks.hef", "labels": ["duck"]}]}
        m = _upload_module(tmp_path, monkeypatch, cfg)
        m._model_key = "ducks.hef"
        m._labels = []
        m._configure_module_extra({"hailo.custom_labels"})
        assert m._labels == ["duck"]
        m._rebuild_detector_async.assert_not_called()

    def test_list_hefs_lists_only_hef_files(self, tmp_path, monkeypatch):
        (tmp_path / "a.hef").write_bytes(b"xy")
        (tmp_path / "a.hef.part").write_bytes(b"x")
        (tmp_path / "notes.txt").write_bytes(b"x")
        m = _upload_module(tmp_path, monkeypatch, {"hailo.model": "a.hef"})
        out = m.list_hefs()
        assert out["hailo_present"] is False
        assert [(h["name"], h["size_bytes"]) for h in out["hefs"]] == [("a.hef", 2)]
        assert out["current"] == "a.hef"

    def test_fetch_hef_rejects_path_tricks(self, tmp_path, monkeypatch):
        m = _upload_module(tmp_path / "u", monkeypatch, {})
        for bad in ("../x.hef", "x.txt", "", ".hidden.hef"):
            assert "error" in m.fetch_hef("http://c:5000", bad)
        assert not (tmp_path / "u").exists()

    def test_fetch_hef_downloads_and_reloads_only_the_selected_model(
            self, tmp_path, monkeypatch):
        from src.modules.variants.hailo_camera import hailo_camera_module as mod
        m = _upload_module(tmp_path, monkeypatch, {})
        got = []
        monkeypatch.setattr(mod, "download_hef",
                            lambda url, name, d: got.append((url, name, d)) or 10)
        out = m.fetch_hef("http://c:5000", "ducks.hef")
        assert got == [("http://c:5000", "ducks.hef", str(tmp_path))]
        assert out["fetched"] == "ducks.hef"
        m._rebuild_detector_async.assert_not_called()
        m._model_key = "ducks.hef"
        m.fetch_hef("http://c:5000", "ducks.hef")
        m._rebuild_detector_async.assert_called_once()

    def test_delete_hef_refuses_the_selected_model(self, tmp_path, monkeypatch):
        (tmp_path / "a.hef").write_bytes(b"x")
        (tmp_path / "b.hef").write_bytes(b"x")
        m = _upload_module(tmp_path, monkeypatch, {"hailo.model": "a.hef"})
        assert "error" in m.delete_hef("a.hef")
        assert (tmp_path / "a.hef").exists()
        out = m.delete_hef("b.hef")
        assert "error" not in out
        assert [h["name"] for h in out["hefs"]] == ["a.hef"]


class TestDownloadHef:
    def test_writes_via_part_file_and_cleans_up_on_failure(self, tmp_path):
        import io

        from src.modules import hailo_infer

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch.object(hailo_infer.urllib.request, "urlopen",
                          return_value=_Resp(b"abc")) as uo:
            assert hailo_infer.download_hef("http://c:5000/", "m.hef", str(tmp_path)) == 3
        uo.assert_called_once()
        assert uo.call_args[0][0] == "http://c:5000/hailo_models/m.hef"
        assert (tmp_path / "m.hef").read_bytes() == b"abc"

        with patch.object(hailo_infer.urllib.request, "urlopen",
                          side_effect=OSError("refused")):
            try:
                hailo_infer.download_hef("http://c:5000", "n.hef", str(tmp_path))
                raise AssertionError("expected OSError")
            except OSError:
                pass
        assert sorted(p.name for p in tmp_path.iterdir()) == ["m.hef"]
