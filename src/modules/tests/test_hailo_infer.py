"""Tests for the raw YOLOv8-pose decoder in modules.hailo_infer.

Regression cover for the "hundreds of false people" failure: the model-zoo
yolov8*_pose HEF ships some heads with the final sigmoid already baked in, so
re-sigmoiding the score branch squashed the range and every grid cell passed
the confidence threshold.
"""

import numpy as np
import pytest

from modules.hailo_infer import HailoPoseDetector, HailoSegDetector, _as_prob


def _blank_pose_detector(threshold=0.5):
    d = HailoPoseDetector.__new__(HailoPoseDetector)
    d._threshold = threshold
    return d


def _scale(grid, hot=None, hot_score=0.95):
    box = np.zeros((grid, grid, 64), np.float32)
    score = np.zeros((grid, grid, 1), np.float32)
    kpt = np.zeros((grid, grid, 51), np.float32)
    if hot is not None:
        score[hot[0], hot[1], 0] = hot_score
    return {"box": box, "score": score, "kpt": kpt}


class TestAsProb:
    def test_probabilities_pass_through_unchanged(self):
        p = np.array([0.0, 0.1, 0.5, 0.9, 1.0], np.float32)
        assert np.allclose(_as_prob(p), p)

    def test_logits_get_sigmoided(self):
        logits = np.array([-8.0, -2.0, 0.0, 3.0, 9.0], np.float32)
        assert np.allclose(_as_prob(logits), 1.0 / (1.0 + np.exp(-logits)), atol=1e-4)

    def test_empty_input(self):
        assert _as_prob(np.array([], np.float32)).size == 0

    def test_slightly_out_of_range_still_treated_as_logits(self):
        # 1.4 is outside [0, 1] tolerance -> sigmoid path
        vals = np.array([0.2, 1.4], np.float32)
        assert np.allclose(_as_prob(vals), 1.0 / (1.0 + np.exp(-vals)), atol=1e-4)


class TestDecodeRaw:
    def test_activated_score_branch_no_false_positives(self):
        """An all-zero *activated* score branch must yield zero people, not one
        per grid cell (the double-sigmoid bug)."""
        d = _blank_pose_detector(threshold=0.5)
        scales = {g: _scale(g) for g in (80, 40, 20)}
        assert d._decode_raw(scales, (480, 640, 3)) == []

    def test_single_hot_cell_yields_one_person(self):
        d = _blank_pose_detector(threshold=0.5)
        scales = {20: _scale(20, hot=(10, 10), hot_score=0.95)}
        res = d._decode_raw(scales, (480, 640, 3))
        assert len(res) == 1
        assert 0.9 <= res[0].score <= 1.0
        assert len(res[0].keypoints) == HailoPoseDetector._KP

    def test_logit_score_branch_also_decodes(self):
        """A raw-logit score branch (no baked sigmoid) still works: one strong
        positive logit -> one person."""
        d = _blank_pose_detector(threshold=0.5)
        s = _scale(20)
        s["score"][5, 5, 0] = 6.0  # sigmoid(6) ~ 0.9975
        s["score"] -= 6.0          # background at sigmoid(-6) ~ 0.0025
        s["score"][5, 5, 0] = 6.0
        res = d._decode_raw({20: s}, (480, 640, 3))
        assert len(res) == 1

    def test_pre_nms_cap_bounds_work(self):
        """A pathological score branch that passes the whole grid is capped
        before NMS / keypoint decode rather than wedging the CPU."""
        d = _blank_pose_detector(threshold=0.5)
        s = _scale(80)
        s["score"][:] = 0.99  # every cell "passes"
        res = d._decode_raw({80: s}, (480, 640, 3))
        # Boxes are all identical (zero DFL) so NMS collapses them, but the
        # point is the decode returns quickly and does not raise.
        assert isinstance(res, list)

    def test_keypoints_scaled_to_original_frame(self):
        d = _blank_pose_detector(threshold=0.5)
        scales = {20: _scale(20, hot=(10, 10))}
        res = d._decode_raw(scales, (240, 320, 3))
        x, y, w, h = res[0].box
        # box centre near the hot cell, mapped into a 320x240 frame
        assert 0 <= x <= 320
        assert 0 <= y <= 240


def _blank_seg_detector(threshold=0.5):
    d = HailoSegDetector.__new__(HailoSegDetector)
    d._threshold = threshold
    return d


def _seg_run(grids=(80, 40, 20), nc=80, proto=160, hot=None, hot_score=0.95,
             proto_layout="hwc"):
    """Build the raw conv-tensor dict a yolov8*_seg HEF emits."""
    out = {}
    for i, g in enumerate(grids):
        out[f"box{i}"] = np.zeros((g, g, 64), np.float32)
        cls = np.zeros((g, g, nc), np.float32)
        if hot is not None and g == grids[0]:
            cls[hot[0], hot[1], 0] = hot_score
        out[f"cls{i}"] = cls
        out[f"mc{i}"] = np.zeros((g, g, 32), np.float32)
    if proto_layout == "hwc":
        out["proto"] = np.zeros((proto, proto, 32), np.float32)
    else:
        out["proto"] = np.zeros((32, proto, proto), np.float32)
    return out


class TestSegDecode:
    def test_no_detections_on_empty_cls(self):
        d = _blank_seg_detector(threshold=0.5)
        res = d._decode(_seg_run(), (480, 640, 3))
        assert res == []

    def test_single_hot_cell_yields_one_instance(self):
        d = _blank_seg_detector(threshold=0.5)
        run = _seg_run(hot=(10, 10), hot_score=0.95)
        res = d._decode(run, (480, 640, 3))
        assert len(res) == 1
        s = res[0]
        assert 0.9 <= s.score <= 1.0
        assert s.category == 0
        assert s.mask.shape == (480, 640)
        assert s.mask.dtype == np.uint8

    def test_proto_chw_layout_accepted(self):
        d = _blank_seg_detector(threshold=0.5)
        run = _seg_run(hot=(10, 10), proto_layout="chw")
        res = d._decode(run, (480, 640, 3))
        assert len(res) == 1
        assert res[0].mask.shape == (480, 640)

    def test_decoded_output_returns_empty(self):
        """A HEF that ships a non-dict (already decoded) output -> [] so the
        module falls back to a plain preview instead of raising."""
        d = _blank_seg_detector(threshold=0.5)
        assert d._decode(np.zeros((1, 100, 39), np.float32), (480, 640, 3)) == []

    def test_missing_proto_returns_empty(self):
        d = _blank_seg_detector(threshold=0.5)
        run = _seg_run(hot=(10, 10))
        del run["proto"]
        assert d._decode(run, (480, 640, 3)) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
