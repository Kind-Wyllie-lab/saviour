"""hailo_camera with a Lightning Pose model: per-frame keypoints into the
timestamp CSV.

The capture-thread hook and the worker are driven by hand (no camera, no
Hailo): a fake detector whose "inference" reads the frame it was given, so
each row's keypoints prove which frame they came from.
"""

import collections
import json
import logging
import queue
import threading
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

import src.modules.variants.hailo_camera.hailo_camera_module as hcm
from modules.lp_decode import decode, prepare_input, validate_meta
from src.modules.variants.hailo_camera.hailo_camera_module import (
    HailoCameraModule,
    HailoLPPoseDetector,
)

BASE = len(HailoCameraModule.BASE_CSV_COLUMNS)


def _meta(names=("nose", "tail"), input_size=(256, 256), **kw):
    d = {"type": "lp_heatmap", "keypoint_names": list(names),
         "input_size": list(input_size), "downsample_factor": 2,
         "input_channel_order": "BGR", "skeleton": [list(names[:2])]}
    d.update(kw)
    return validate_meta(d)


class _MA:
    """Stands in for picamera2.MappedArray: the fake request IS the lores array."""
    def __init__(self, req, stream):
        assert stream == "lores"
        self.array = req

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextmanager
def _fake_mapped_array():
    with patch.object(hcm, "MappedArray", _MA):
        yield


def _detector(meta, infer):
    """A HailoLPPoseDetector without a device: `infer(frame)` -> (K,3) in
    model-input pixels; decode() just scales to out_size."""
    det = HailoLPPoseDetector.__new__(HailoLPPoseDetector)
    det.meta = meta
    det.decode_method = "exact"
    det.infer = infer
    h, w = meta.input_size
    det.decode = lambda raw, out_size: raw * np.array(
        [out_size[0] / w, out_size[1] / h, 1.0], np.float32)
    return det


def _module(det, every_n=1, csv_open=True):
    m = HailoCameraModule.__new__(HailoCameraModule)
    m.logger = logging.getLogger("test_hailo_lp")
    m.config = MagicMock()
    m.config.get.side_effect = lambda k, d=None: d
    m._det_lock = threading.Lock()
    m.detector = det
    m._task = "lp_pose"
    m._model_key = "rat.hef"
    m._detector_error = None
    m._infer_error_logged = False
    m._infer_q = queue.Queue(maxsize=1)
    m._infer_worker = None
    m._infer_ms = collections.deque(maxlen=600)
    m._decode_ms = collections.deque(maxlen=600)
    m._lp_selected = True
    m._lp_every_n = every_n
    m._lp_seq = 0
    m._lp_last = None
    m._lp_main_rot_k = 0
    m._lp_shapes = None
    m._lp_fov_error = None
    m._pending = collections.OrderedDict()
    m._pending_lock = threading.Lock()
    m._lp_counts = dict.fromkeys(("inferred", "skipped", "dropped", "late", "failed"), 0)
    m._csv_row_buffer = collections.deque()
    m._timestamp_csv_writer = object() if csv_open else None
    m._encoder_active = csv_open
    m._csv_kp_names = tuple(det.meta.keypoint_names) if det else ()
    m.CSV_EXTRA_COLUMNS = det.meta.csv_columns() if det else []
    m._cb_monochrome = False
    m._infer_enabled = lambda: True
    return m


def _timing(ts):
    t = MagicMock()
    t.timestamp_ns = ts
    return t


def _frame(value, shape=(360, 640, 3)):
    return np.full(shape, value, np.uint8)


def _capture(m, value, main_shape=(720, 1280, 3), rot=0, lores_shape=(360, 640, 3)):
    """One capture-thread frame: hook, then the base-built CSV row (frame_id
    = value, kp placeholders empty) through _emit_csv_row."""
    token = m._frame_request_hook(_frame(value, lores_shape), _timing(1000 + value),
                                  main_shape, rot)
    row = [value] + [""] * (BASE - 1) + [""] * len(m.CSV_EXTRA_COLUMNS)
    m._emit_csv_row(row, token)
    return token


def _run_worker_once(m):
    item = m._infer_q.get_nowait()
    m._infer_lp(*item)
    return item


def _value_infer(frame):
    """'Inference' that reports the frame's pixel value as nose x, so a row
    can be traced back to the frame that produced it."""
    v = float(frame[0, 0, 0])
    return np.array([[v, 10.0, 0.9], [v + 1, 20.0, 0.8]], np.float32)


def _kp(row, i=0):
    return row[BASE + 3 * i: BASE + 3 * i + 3]


# ---------------------------------------------------------------------------
# rows carry the result of the frame that was inferred
# ---------------------------------------------------------------------------

class TestFrameStamping:
    def test_result_lands_on_its_own_frame_not_the_current_one(self):
        m = _module(_detector(_meta(), _value_infer))
        with _fake_mapped_array():
            _capture(m, 10)
            item = m._infer_q.get_nowait()     # worker picks frame 10 up...
            _capture(m, 11)                    # ...frames 11 and 12 arrive
            _capture(m, 12)                    # (12 replaces 11 in the queue)
            m._infer_lp(*item)                 # frame 10's result arrives now
            _run_worker_once(m)                # then frame 12's
        rows = list(m._csv_row_buffer)
        assert [r[0] for r in rows] == [10, 11, 12]          # frame order kept
        # nose x = frame value * (1280 / 256) out of _value_infer
        assert float(_kp(rows[0])[0]) == pytest.approx(10 * 5.0)
        assert _kp(rows[1]) == ["", "", ""]                  # dropped: worker behind
        assert float(_kp(rows[2])[0]) == pytest.approx(12 * 5.0)
        assert m._lp_counts["dropped"] == 1
        assert m._lp_last[1] == 1012                         # timestamp of frame 12

    def test_queue_item_carries_frame_number_and_timestamp(self):
        m = _module(_detector(_meta(), _value_infer))
        with _fake_mapped_array():
            _capture(m, 7)
        seq, ts, frame, out_size = m._infer_q.get_nowait()
        assert (seq, ts, out_size) == (1, 1007, (1280, 720))
        assert frame[0, 0, 0] == 7

    def test_rows_wait_until_their_result_arrives(self):
        m = _module(_detector(_meta(), _value_infer))
        with _fake_mapped_array():
            _capture(m, 1)
        assert not m._csv_row_buffer           # held back
        _run_worker_once(m)
        assert len(m._csv_row_buffer) == 1

    def test_values_formatted_subpixel(self):
        m = _module(_detector(_meta(), lambda f: np.array(
            [[12.3456, 7.25, 0.98766], [np.nan, np.nan, 0.01]], np.float32)))
        with _fake_mapped_array():
            _capture(m, 1)
            _run_worker_once(m)
        row = m._csv_row_buffer[0]
        assert _kp(row, 0) == [f"{12.3456 * 5:.3f}", f"{7.25 * 720 / 256:.3f}", "0.9877"]
        assert _kp(row, 1) == ["", "", "0.0100"]


class TestEveryN:
    def test_skipped_frames_are_empty_not_carried_forward(self):
        m = _module(_detector(_meta(), _value_infer), every_n=2)
        with _fake_mapped_array():
            for v in (1, 2, 3, 4):
                _capture(m, v)
                if not m._infer_q.empty():
                    _run_worker_once(m)
        rows = list(m._csv_row_buffer)
        assert [r[0] for r in rows] == [1, 2, 3, 4]
        assert _kp(rows[0]) == ["", "", ""] and _kp(rows[2]) == ["", "", ""]
        assert float(_kp(rows[1])[0]) == pytest.approx(10.0)
        assert float(_kp(rows[3])[0]) == pytest.approx(20.0)
        assert m._lp_counts["skipped"] == 2

    def test_no_csv_open_still_infers_for_the_overlay(self):
        m = _module(_detector(_meta(), _value_infer), csv_open=False)
        with _fake_mapped_array():
            token = m._frame_request_hook(_frame(5), _timing(5), (720, 1280, 3), 0)
            _run_worker_once(m)
        assert token == (1, True)
        assert not m._pending and m._lp_last is not None


class TestLateAndClose:
    def test_result_past_timeout_releases_row_empty(self):
        m = _module(_detector(_meta(), _value_infer))
        with _fake_mapped_array():
            _capture(m, 1)
        m._infer_q.get_nowait()                # the worker took it, then hung
        m._pending[1]["t"] -= hcm._LP_RESULT_TIMEOUT_S + 0.1
        with _fake_mapped_array():
            _capture(m, 2)                     # any later activity releases it
        assert m._csv_row_buffer[0][0] == 1
        assert _kp(m._csv_row_buffer[0]) == ["", "", ""]
        assert m._lp_counts["late"] == 1

    def test_late_result_is_discarded(self):
        m = _module(_detector(_meta(), _value_infer))
        with _fake_mapped_array():
            _capture(m, 1)
        item = m._infer_q.get_nowait()
        m._pending[1]["t"] -= hcm._LP_RESULT_TIMEOUT_S + 0.1
        with m._pending_lock:
            m._release_pending_locked(hcm.time.monotonic())
        m._infer_lp(*item)                     # arrives after release: no effect
        assert _kp(m._csv_row_buffer[0]) == ["", "", ""]

    def test_close_flush_releases_everything_in_order(self):
        m = _module(_detector(_meta(), _value_infer))
        with _fake_mapped_array():
            _capture(m, 1)
            _capture(m, 2)
        m._flush_pending(0.05)                 # no worker alive: no wait
        assert [r[0] for r in m._csv_row_buffer] == [1, 2]
        assert not m._pending


# ---------------------------------------------------------------------------
# field of view
# ---------------------------------------------------------------------------

class TestFieldOfView:
    def test_preview_aspect_mismatch_fails_and_blocks_readiness(self):
        m = _module(_detector(_meta(), _value_infer))
        with _fake_mapped_array():
            token = _capture(m, 1, main_shape=(1080, 1920, 3), lores_shape=(480, 640, 3))
        assert token is None and m._infer_q.empty()
        assert "differ in aspect" in m._lp_fov_error
        assert _kp(m._csv_row_buffer[0]) == ["", "", ""]   # row still written
        ok, msg = m._check_hailo()
        assert ok is False and "aspect" in msg

    def test_training_aspect_mismatch_fails(self):
        m = _module(_detector(_meta(source_frame_size=[1080, 1080]), _value_infer))
        with _fake_mapped_array():
            _capture(m, 1, main_shape=(1080, 1920, 3), lores_shape=(360, 640, 3))
        assert "trained on 1080x1080" in m._lp_fov_error

    def test_even_rounding_of_preview_size_is_tolerated(self):
        m = _module(_detector(_meta(), _value_infer))
        with _fake_mapped_array():
            token = _capture(m, 1, main_shape=(1000, 1334, 3), lores_shape=(478, 638, 3))
        assert token == (1, True) and m._lp_fov_error is None

    def test_static_check_at_model_load(self):
        m = _module(None)
        m.width, m.height, m.lores_width, m.lores_height = 1920, 1080, 640, 480
        assert "differ in aspect" in m._lp_static_fov_error(_meta())
        m.lores_height = 360
        assert m._lp_static_fov_error(_meta()) is None


def _dot_infer(meta, recorded_wh):
    """'Inference' that finds a bright dot in the model input and returns the
    LP heatmap a perfect LP-trained model would, decoded by the real decoder:
    the test then checks the mapping back onto the recorded frame.

    LP's coordinate convention is plain scaling (labels resized as x * w/W,
    decoded as x * W/w), while the image content is resampled pixel-centre to
    pixel-centre, so the dot's pixel-index centroid c in the input sits at
    x*s + (s - 1)/2 for a frame point x (s = input/recorded scale). A model
    trained on LP labels learns to report x*s = c - (s - 1)/2."""
    hm, wm, _ = meta.heatmap_shape
    s = meta.heatmap_stride
    sx = meta.input_size[1] / recorded_wh[0]
    sy = meta.input_size[0] / recorded_wh[1]

    def infer(frame):
        x = prepare_input(frame, meta.input_size)[..., 1].astype(np.float64)
        ys, xs = np.mgrid[0:x.shape[0], 0:x.shape[1]]
        cx, cy = (x * xs).sum() / x.sum(), (x * ys).sum() / x.sum()
        cx, cy = cx - (sx - 1) / 2, cy - (sy - 1) / 2
        yy, xx = np.mgrid[0:hm, 0:wm].astype(np.float64)
        raw = -((xx - cx / s) ** 2 + (yy - cy / s) ** 2) / (2 * 1.25 ** 2)
        return np.repeat(raw[..., None], len(meta.keypoint_names), axis=-1).astype(np.float32)
    return infer


def _dot_frame(w, h, x, y, sigma=6.0):
    yy, xx = np.mgrid[0:h, 0:w]
    g = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))
    f = np.zeros((h, w, 3), np.uint8)
    f[..., 1] = np.round(255 * g).astype(np.uint8)
    return f


class TestKnownPointMapping:
    def _run(self, main, lores, main_shape, rot=0):
        meta = _meta()
        det = HailoLPPoseDetector.__new__(HailoLPPoseDetector)
        det.meta, det.decode_method = meta, "exact"
        det.infer = _dot_infer(meta, (main_shape[1], main_shape[0]))
        det.decode = lambda raw, out_size: decode(raw, meta, out_size=out_size)
        m = _module(det)
        with _fake_mapped_array():
            m._frame_request_hook(lores, _timing(1), main_shape, rot)
            _run_worker_once(m)
        return m._lp_last[2][0]

    def test_point_in_recorded_pixels_landscape(self):
        main = _dot_frame(1280, 720, 900.0, 300.0)
        lores = cv2.resize(main, (640, 360), interpolation=cv2.INTER_AREA)
        x, y, _ = self._run(main, lores, main.shape)
        assert (x, y) == pytest.approx((900.0, 300.0), abs=0.25)

    def test_point_follows_in_place_rotation_of_a_square_frame(self):
        # main is rotated 90 deg in place by CameraBase; lores arrives unrotated
        sensor = _dot_frame(720, 720, 600.0, 150.0)
        recorded = np.rot90(sensor, 1)
        lores = cv2.resize(sensor, (640, 640), interpolation=cv2.INTER_AREA)
        x, y, _ = self._run(recorded, lores, recorded.shape, rot=1)
        ys, xs = np.unravel_index(np.argmax(recorded[..., 1]), recorded.shape[:2])
        assert (x, y) == pytest.approx((float(xs), float(ys)), abs=0.25)


# ---------------------------------------------------------------------------
# model selection, sidecars, columns, readiness
# ---------------------------------------------------------------------------

class TestSidecar:
    def test_model_spec_by_sidecar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hcm, "USER_HEF_DIR", str(tmp_path))
        m = _module(None)
        m._uploaded_labels = lambda name: []
        (tmp_path / "det.hef").write_bytes(b"x")
        (tmp_path / "rat.hef").write_bytes(b"x")
        (tmp_path / "rat.json").write_text(json.dumps(_meta().to_dict()))
        (tmp_path / "bad.hef").write_bytes(b"x")
        (tmp_path / "bad.json").write_text(json.dumps({"type": "lp_heatmap"}))
        assert m._model_spec("det.hef")[1]["task"] == "detection"
        spec = m._model_spec("rat.hef")[1]
        assert spec["task"] == "lp_pose" and spec["meta"].keypoint_names == ("nose", "tail")
        spec = m._model_spec("bad.hef")[1]
        assert spec["task"] == "lp_pose" and "keypoint_names" in spec["meta_error"]

    def test_set_hef_metadata_validates_and_writes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(hcm, "USER_HEF_DIR", str(tmp_path))
        (tmp_path / "rat.hef").write_bytes(b"x")
        m = _module(None)
        m._recording_active = lambda: False
        m._model_key = "yolov8s"
        m.config.get.side_effect = lambda k, d=None: {"hailo.model": "yolov8s"}.get(k, d)
        r = m.set_hef_metadata("rat.hef", {"type": "lp_heatmap"})
        assert "refused" in r["error"] and not (tmp_path / "rat.json").exists()
        r = m.set_hef_metadata("rat.hef", _meta().to_dict())
        assert r["metadata_saved"] == "rat.hef"
        info = next(h for h in r["hefs"] if h["name"] == "rat.hef")["model"]
        assert info["type"] == "lp_heatmap" and info["keypoint_names"] == ["nose", "tail"]
        m.set_hef_metadata("rat.hef", None)
        assert not (tmp_path / "rat.json").exists()


class TestColumnsAndReadiness:
    def test_column_change_waits_for_next_csv(self):
        m = _module(_detector(_meta(), _value_infer))
        before = list(m.CSV_EXTRA_COLUMNS)
        m._set_lp_columns(_meta(names=("a", "b", "c")))
        assert m.CSV_EXTRA_COLUMNS == before               # CSV open: unchanged
        m._timestamp_csv_writer = None
        m._set_lp_columns(_meta(names=("a", "b", "c")))
        assert len(m.CSV_EXTRA_COLUMNS) == 9

    def test_result_from_other_keypoints_left_empty(self):
        m = _module(_detector(_meta(), _value_infer))
        m._csv_kp_names = ("x", "y")
        with _fake_mapped_array():
            _capture(m, 1)
            _run_worker_once(m)
        assert _kp(m._csv_row_buffer[0]) == ["", "", ""]

    def test_lp_model_not_loaded_blocks_readiness(self):
        m = _module(None)
        m._detector_error = "HEF not found"
        ok, msg = m._check_hailo()
        assert ok is False and "HEF not found" in msg

    def test_demo_model_failure_never_blocks(self):
        m = _module(None)
        m._lp_selected = False
        m._task = "detection"
        ok, _ = m._check_hailo()
        assert ok is True

    def test_no_lp_model_rows_go_straight_through(self):
        m = _module(None)
        m._task = "detection"
        m.detector = MagicMock()
        assert m._frame_request_hook(_frame(1), _timing(1), (720, 1280, 3), 0) is None
        m._emit_csv_row([1], None)
        assert list(m._csv_row_buffer) == [[1]]
