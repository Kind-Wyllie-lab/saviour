"""
Tests for src/modules/variants/lightning_camera/lightning_camera_module.py.

Covers StubPoseDetector (pure), HailoPoseDetector._decode_heatmaps (pure numpy
logic, constructed via __new__ to skip the real picamera2.devices.hailo.Hailo
import in __init__ -- same pattern test_camera_base.py uses for CameraBase),
and the CSV-column-from-keypoint-names generation. LightningCameraModule
itself (the CameraBase subclass) is out of scope here -- see
test_loom_camera_module.py's docstring for the same reasoning, and
test_camera_base.py for the __new__-based construction pattern that would
apply to it too.
"""

import numpy as np
import pytest

from src.modules.variants.lightning_camera.lightning_camera_module import (
    HailoPoseDetector,
    StubPoseDetector,
)

KEYPOINT_NAMES = ["nose", "neck", "tail_base"]


def _make_hailo_detector(keypoint_names=KEYPOINT_NAMES, threshold=0.3):
    det = HailoPoseDetector.__new__(HailoPoseDetector)
    det._keypoint_names = keypoint_names
    det._threshold = threshold
    return det


# ---------------------------------------------------------------------------
# StubPoseDetector
# ---------------------------------------------------------------------------

class TestStubPoseDetector:
    def test_detect_returns_none_for_every_keypoint(self):
        detector = StubPoseDetector(KEYPOINT_NAMES)
        result = detector.detect(np.zeros((10, 10, 3), dtype=np.uint8))
        assert set(result.keys()) == set(KEYPOINT_NAMES)
        assert all(v == (None, None, None) for v in result.values())

    def test_detect_ignores_frame_content(self):
        detector = StubPoseDetector(KEYPOINT_NAMES)
        a = detector.detect(np.zeros((10, 10, 3), dtype=np.uint8))
        b = detector.detect(np.full((20, 20, 3), 255, dtype=np.uint8))
        assert a == b

    def test_close_does_not_raise(self):
        StubPoseDetector(KEYPOINT_NAMES).close()

    def test_empty_keypoint_list(self):
        detector = StubPoseDetector([])
        assert detector.detect(np.zeros((4, 4, 3), dtype=np.uint8)) == {}


# ---------------------------------------------------------------------------
# HailoPoseDetector._decode_heatmaps
# ---------------------------------------------------------------------------

class TestHailoPoseDetectorDecodeHeatmaps:
    def test_channel_first_peak_maps_to_original_frame_coords(self):
        det = _make_hailo_detector(keypoint_names=["nose"], threshold=0.1)
        heatmaps = np.zeros((1, 10, 20), dtype=np.float32)  # (K, Hm, Wm)
        heatmaps[0, 5, 15] = 0.9  # row=5 (y), col=15 (x)
        result = det._decode_heatmaps(heatmaps, orig_shape=(100, 200, 3))
        x, y, conf = result["nose"]
        assert conf == pytest.approx(0.9)
        # (15 + 0.5) / 20 * 200 = 155 ; (5 + 0.5) / 10 * 100 = 55
        assert x == pytest.approx(155.0)
        assert y == pytest.approx(55.0)

    def test_channel_last_layout_is_also_handled(self):
        det = _make_hailo_detector(keypoint_names=["nose", "neck"], threshold=0.1)
        heatmaps = np.zeros((10, 20, 2), dtype=np.float32)  # (Hm, Wm, K)
        heatmaps[2, 4, 0] = 0.8   # keypoint 0 ("nose")
        heatmaps[7, 18, 1] = 0.7  # keypoint 1 ("neck")
        result = det._decode_heatmaps(heatmaps, orig_shape=(50, 100, 3))
        assert result["nose"][2] == pytest.approx(0.8)
        assert result["neck"][2] == pytest.approx(0.7)

    def test_batched_input_uses_first_element(self):
        det = _make_hailo_detector(keypoint_names=["nose"], threshold=0.1)
        heatmaps = np.zeros((1, 1, 10, 10), dtype=np.float32)  # (N, K, Hm, Wm)
        heatmaps[0, 0, 3, 3] = 0.6
        result = det._decode_heatmaps(heatmaps, orig_shape=(20, 20, 3))
        assert result["nose"][2] == pytest.approx(0.6)

    def test_below_threshold_reports_no_detection(self):
        det = _make_hailo_detector(keypoint_names=["nose"], threshold=0.5)
        heatmaps = np.zeros((1, 10, 10), dtype=np.float32)
        heatmaps[0, 0, 0] = 0.2  # below threshold
        result = det._decode_heatmaps(heatmaps, orig_shape=(20, 20, 3))
        assert result["nose"] == (None, None, None)

    def test_unrecognised_channel_count_reports_no_detection_for_all(self):
        det = _make_hailo_detector(keypoint_names=["nose", "neck"], threshold=0.1)
        heatmaps = np.zeros((5, 10, 10), dtype=np.float32)  # neither dim is 2
        result = det._decode_heatmaps(heatmaps, orig_shape=(20, 20, 3))
        assert result == {"nose": (None, None, None), "neck": (None, None, None)}


# ---------------------------------------------------------------------------
# CSV column generation (module.py-level string convention, exercised as a
# standalone function matching _configure_pose_estimation's list comprehension
# exactly, since that method itself needs a constructed CameraBase instance).
# ---------------------------------------------------------------------------

def _csv_columns_for(keypoint_names: list[str]) -> list[str]:
    return [
        col
        for name in keypoint_names
        for col in (f"kp_{name}_x", f"kp_{name}_y", f"kp_{name}_conf")
    ]


class TestCsvColumnGeneration:
    def test_three_columns_per_keypoint_in_order(self):
        cols = _csv_columns_for(["nose", "tail_base"])
        assert cols == [
            "kp_nose_x", "kp_nose_y", "kp_nose_conf",
            "kp_tail_base_x", "kp_tail_base_y", "kp_tail_base_conf",
        ]

    def test_empty_keypoint_list_yields_no_columns(self):
        assert _csv_columns_for([]) == []
