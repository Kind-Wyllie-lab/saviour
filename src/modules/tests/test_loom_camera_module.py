"""
Tests for src/modules/variants/loom_camera/loom_camera_module.py.

Covers LoomBlobDiffTracker (abs-diff blob detection on real synthetic
numpy/cv2 frames -- no hardware needed) and loom_load_roi_and_line (pure
JSON + geometry parsing, exercised with real temp JSON files). The
crossing-line debounce state machine (LoomCrossingState /
loom_update_crossing_state) already has dedicated coverage in
test_loom_crossing.py. LoomCameraModule itself (the CameraBase subclass)
is out of scope here -- see test_camera_base.py for the __new__-based
construction pattern that would apply to it too.
"""

import json
import tempfile
from pathlib import Path

import numpy as np

from src.modules.variants.loom_camera.loom_camera_module import (
    LoomBlobDiffTracker,
    loom_load_roi_and_line,
)


def _frame_with_blob(shape=(64, 64), blob_center=None, blob_size=6, bg=20, fg=220):
    """A synthetic BGR frame: uniform background plus one bright square blob."""
    h, w = shape
    frame = np.full((h, w, 3), bg, dtype=np.uint8)
    if blob_center is not None:
        cx, cy = blob_center
        half = blob_size // 2
        frame[max(0, cy - half):cy + half, max(0, cx - half):cx + half] = fg
    return frame


# ---------------------------------------------------------------------------
# LoomBlobDiffTracker
# ---------------------------------------------------------------------------

class TestResizeToWidth:
    def test_preserves_aspect_ratio(self):
        w, h = LoomBlobDiffTracker._resize_to_width(1920, 1080, 256)
        assert w == 256
        assert h == round(256 * 1080 / 1920)


class TestLoomBlobDiffTrackerDetectCenter:
    def test_reset_clears_temporal_state(self):
        tracker = LoomBlobDiffTracker(process_width=64)
        tracker.detect_center(_frame_with_blob())
        tracker.reset()
        assert tracker.last_detection_center_proc is None
        assert tracker.last_display_center_proc is None

    def test_first_frame_returns_no_detection(self):
        tracker = LoomBlobDiffTracker(process_width=64)
        center, _scale, proc_shape = tracker.detect_center(_frame_with_blob())
        assert center is None
        assert proc_shape == (64, 64)

    def test_detects_a_blob_that_appears_on_the_second_frame(self):
        tracker = LoomBlobDiffTracker(
            process_width=64, min_area_px=4, smoothing_alpha=1.0
        )
        tracker.detect_center(_frame_with_blob(blob_center=None))  # primes _prev_gray

        center, _scale, _shape = tracker.detect_center(
            _frame_with_blob(blob_center=(32, 32), blob_size=8)
        )

        assert center is not None
        cx, cy = center
        assert abs(cx - 32) <= 2
        assert abs(cy - 32) <= 2

    def test_no_blob_area_below_min_area_is_ignored(self):
        tracker = LoomBlobDiffTracker(
            process_width=64, min_area_px=10_000, smoothing_alpha=1.0
        )
        tracker.detect_center(_frame_with_blob())
        center, _scale, _shape = tracker.detect_center(
            _frame_with_blob(blob_center=(32, 32), blob_size=4)
        )
        assert center is None

    def test_holds_last_center_within_patience_then_drops_it(self):
        """The tracker is motion-based (abs-diff of consecutive frames), so
        the 'miss' case to test is the target going still -- not vanishing,
        which would itself register as a fresh diff at the vacated spot."""
        tracker = LoomBlobDiffTracker(
            process_width=64, min_area_px=4, smoothing_alpha=1.0, patience_frames=2
        )
        tracker.detect_center(_frame_with_blob())
        tracker.detect_center(_frame_with_blob(blob_center=(32, 32), blob_size=8))
        held_center = tracker.last_display_center_proc
        assert held_center is not None

        # Blob freezes in place -> zero diff -> genuine misses, held within patience.
        frozen = _frame_with_blob(blob_center=(32, 32), blob_size=8)
        center, _s, _sh = tracker.detect_center(frozen)
        assert center == held_center
        center, _s, _sh = tracker.detect_center(frozen)
        assert center == held_center

        # Patience exceeded: center is dropped.
        center, _s, _sh = tracker.detect_center(frozen)
        assert center is None
        assert tracker.last_display_center_proc is None

    def test_roi_mask_excludes_blob_outside_it(self):
        tracker = LoomBlobDiffTracker(
            process_width=64, min_area_px=4, smoothing_alpha=1.0
        )
        roi = np.zeros((64, 64), dtype=bool)
        roi[:, :32] = True  # only the left half is in-ROI
        tracker.set_roi_mask_proc(roi)

        tracker.detect_center(_frame_with_blob())
        # Blob appears on the right half -- outside the ROI.
        center, _s, _sh = tracker.detect_center(
            _frame_with_blob(blob_center=(48, 32), blob_size=8)
        )
        assert center is None

    def test_smoothing_moves_display_center_partway_toward_new_detection(self):
        tracker = LoomBlobDiffTracker(
            process_width=64, min_area_px=4, smoothing_alpha=0.5
        )
        tracker.detect_center(_frame_with_blob())
        tracker.detect_center(_frame_with_blob(blob_center=(20, 20), blob_size=8))
        first_center = tracker.last_display_center_proc

        # A jump to a new position also leaves a same-sized diff signature at
        # the vacated old position, so connected-components sees two
        # candidate regions in this one frame -- make the new blob clearly
        # larger so it deterministically wins on area rather than depending
        # on cv2's raster-scan label-ordering tie-break.
        center, _s, _sh = tracker.detect_center(
            _frame_with_blob(blob_center=(40, 40), blob_size=20)
        )

        # Smoothed center must land strictly between the old and new raw
        # detections, not jump straight to the new one.
        assert first_center[0] < center[0] < 40
        assert first_center[1] < center[1] < 40


# ---------------------------------------------------------------------------
# loom_load_roi_and_line
# ---------------------------------------------------------------------------

class TestLoomLoadRoiAndLine:
    def test_none_path_returns_full_frame_roi_and_no_line(self):
        mask, poly, line = loom_load_roi_and_line(
            None, src_width=1080, src_height=1080, proc_width=64, proc_height=64
        )
        assert mask.shape == (64, 64)
        assert mask.all()
        assert poly is None
        assert line is None

    def test_loads_polygon_and_crossing_line_scaled_to_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            roi_path = Path(tmpdir) / "roi.json"
            roi_path.write_text(json.dumps({
                "image_size": {"width": 1000, "height": 1000},
                "arena_polygon": [
                    {"x": 100, "y": 100}, {"x": 900, "y": 100},
                    {"x": 900, "y": 900}, {"x": 100, "y": 900},
                ],
                "crossing_line": {
                    "kind": "vertical", "x": 500, "direction": "right_is_in"
                },
            }))

            mask, poly, line = loom_load_roi_and_line(
                str(roi_path), src_width=2000, src_height=2000,
                proc_width=100, proc_height=100,
            )

            # image_size (1000) -> src (2000) is a 2x scale factor.
            assert poly[0].tolist() == [200.0, 200.0]
            assert line == {"kind": "vertical", "x": 1000.0, "direction": "right_is_in"}
            assert mask.shape == (100, 100)
            # polygon covers part, not all, of the frame
            assert mask.any() and not mask.all()

    def test_legacy_vertical_line_key_is_supported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            roi_path = Path(tmpdir) / "roi.json"
            roi_path.write_text(json.dumps({
                "image_size": {"width": 100, "height": 100},
                "vertical_line": {"x": 50},
            }))

            _mask, _poly, line = loom_load_roi_and_line(
                str(roi_path), src_width=100, src_height=100,
                proc_width=50, proc_height=50,
            )

            assert line == {"kind": "vertical", "x": 50.0, "direction": "left_is_in"}

    def test_missing_polygon_and_line_yields_full_roi_no_line(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            roi_path = Path(tmpdir) / "roi.json"
            roi_path.write_text(
                json.dumps({"image_size": {"width": 100, "height": 100}})
            )

            mask, poly, line = loom_load_roi_and_line(
                str(roi_path), src_width=100, src_height=100,
                proc_width=32, proc_height=32,
            )

            assert mask.all()
            assert poly is None
            assert line is None
