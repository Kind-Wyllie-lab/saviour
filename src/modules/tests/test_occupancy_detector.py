"""Tests for src/modules/variants/habitat_camera/occupancy_detector.py.

Covers the debounce state machine (confirm_samples to turn on, clear_secs
hangover to turn off) with an injected score_fn -- the ONNX backend and the
live camera thread are out of scope here, same split as motion_detector.py.
"""

import numpy as np

from src.modules.variants.habitat_camera.occupancy_detector import (
    OccupancyDetector,
    build_score_fn,
)

S = 1_000_000_000  # ns per second
_FRAME = np.zeros((8, 8, 3), dtype=np.uint8)


def _det(scores, **kw):
    """A detector whose score_fn returns the next value from `scores` each
    call (last value repeats once exhausted)."""
    it = iter(scores)
    last = [0.0]

    def score_fn(_frame):
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]

    return OccupancyDetector(score_fn=score_fn, **kw)


class TestDebounce:
    def test_needs_confirm_samples_consecutive_positives(self):
        d = _det([0.9, 0.9, 0.9], threshold=0.5, confirm_samples=2)
        assert d.observe(_FRAME, 1 * S) is False   # 1/2
        assert d.observe(_FRAME, 2 * S) is True    # 2/2
        assert d.present is True

    def test_a_single_positive_then_negative_does_not_trigger(self):
        d = _det([0.9, 0.1, 0.9], threshold=0.5, confirm_samples=2)
        d.observe(_FRAME, 1 * S)   # 1/2
        d.observe(_FRAME, 2 * S)   # negative -> counter reset
        assert d.present is False
        d.observe(_FRAME, 3 * S)   # 1/2 again
        assert d.present is False

    def test_stays_present_through_clear_secs_hangover(self):
        d = _det([0.9, 0.9], threshold=0.5, confirm_samples=2, clear_secs=30.0)
        d.observe(_FRAME, 1 * S)
        d.observe(_FRAME, 2 * S)
        assert d.present is True
        # negatives within the hangover window keep it present
        for t in (10, 20, 31):
            d._score_fn = lambda _f: 0.1  # now scoring negative
            d.observe(_FRAME, t * S)
        # last observe was at t=31, last positive at t=2 -> 29s < 30s
        assert d.present is True
        d.observe(_FRAME, 33 * S)  # 31s since last positive -> clears
        assert d.present is False

    def test_zero_clear_secs_drops_on_first_negative(self):
        d = _det([0.9, 0.9], threshold=0.5, confirm_samples=2, clear_secs=0.0)
        d.observe(_FRAME, 1 * S)
        d.observe(_FRAME, 2 * S)
        assert d.present is True
        d._score_fn = lambda _f: 0.0
        d.observe(_FRAME, 3 * S)
        assert d.present is False

    def test_scorer_exception_holds_previous_state(self):
        d = _det([0.9, 0.9], threshold=0.5, confirm_samples=2)
        d.observe(_FRAME, 1 * S)
        d.observe(_FRAME, 2 * S)
        assert d.present is True

        def boom(_f):
            raise RuntimeError("model died")

        d._score_fn = boom
        assert d.observe(_FRAME, 3 * S) is True  # unchanged, no raise

    def test_reset_clears_everything(self):
        d = _det([0.9, 0.9], threshold=0.5, confirm_samples=2)
        d.observe(_FRAME, 1 * S)
        d.observe(_FRAME, 2 * S)
        d.reset()
        assert d.present is False
        assert d.last_score == 0.0

    def test_last_score_tracks_raw_output(self):
        d = _det([0.3, 0.7], threshold=0.5, confirm_samples=1)
        d.observe(_FRAME, 1 * S)
        assert d.last_score == 0.3 and d.present is False
        d.observe(_FRAME, 2 * S)
        assert d.last_score == 0.7 and d.present is True


class TestFromConfig:
    def test_disabled_when_not_enabled(self):
        assert OccupancyDetector.from_config({"enabled": False}) is None
        assert OccupancyDetector.from_config({}) is None
        assert OccupancyDetector.from_config(None) is None

    def test_enabled_but_no_model_returns_none(self):
        assert OccupancyDetector.from_config(
            {"enabled": True, "model_path": ""}) is None

    def test_enabled_with_missing_model_file_returns_none(self):
        assert OccupancyDetector.from_config(
            {"enabled": True, "model_path": "/no/such/model.onnx"}) is None


class TestBuildScoreFn:
    def test_no_path_returns_none(self):
        assert build_score_fn("") is None

    def test_missing_file_returns_none(self):
        assert build_score_fn("/nope/model.onnx") is None
