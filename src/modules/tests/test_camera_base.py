"""
Tests for src/modules/camera_base.py.

CameraBase.__init__ talks to real Picamera2 hardware, so every test here
constructs via CameraBase.__new__(CameraBase) (bypassing __init__ entirely,
same pattern as test_export.py's Export.__new__(Export)) and sets only the
attributes the method under test actually reads.

Covers the pure/near-pure helpers (sensor-mode enrichment, timestamp
maths, grayscale conversion using real cv2/numpy) and the
streaming/autofocus command handlers with picam2/facade/communication
mocked. The per-frame hot path (_frame_precallback, _stream_post_callback),
_configure_camera, and the recording/CSV-flush-thread machinery are
deliberately out of scope -- they need a real or deeply-faked
MappedArray/Picamera2 pipeline rather than distinct branching logic.
"""

import logging
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.modules.camera_base import CameraBase, _FrameShim
from src.shared.ratelimit_log import RateLimitedLogger


def _make_camera(**attrs) -> CameraBase:
    cam = CameraBase.__new__(CameraBase)
    cam.logger = MagicMock()
    # Hot-path failure logs go through this coalescing wrapper (see
    # RateLimitedLogger); it forwards to cam.logger.log(level, msg).
    cam._rl_log = RateLimitedLogger(cam.logger)
    for key, value in attrs.items():
        setattr(cam, key, value)
    return cam


# ---------------------------------------------------------------------------
# Tier A: pure / near-pure helpers
# ---------------------------------------------------------------------------

class TestGetSensorModes:
    def test_no_sensor_modes_returns_empty_list(self):
        cam = _make_camera(sensor_modes=[])
        assert cam.get_sensor_modes() == {"sensor_modes": []}

    def test_labels_full_and_partial_fov_modes(self):
        modes = [
            {"crop_limits": (0, 0, 100, 100), "size": (1920, 1080), "fps": 30.0,
             "bit_depth": 10, "format": "SBGGR10"},
            {"crop_limits": (0, 0, 50, 100), "size": (1280, 720), "fps": 60.0,
             "bit_depth": 10, "format": "SBGGR10"},
        ]
        cam = _make_camera(
            sensor_modes=modes, sensor_model="imx708", has_autofocus=True
        )

        result = cam.get_sensor_modes()

        assert result["sensor_model"] == "imx708"
        assert result["has_autofocus"] is True
        full, partial = result["sensor_modes"]
        assert full["label"].endswith("Full FoV")
        assert partial["label"] == "Mode 1: 1280×720 @ 60fps — Partial FoV (50%)"


class TestGetWallMonoOffsetNs:
    def test_computes_and_caches_within_threshold(self):
        cam = _make_camera()
        with patch("src.modules.camera_base.time.monotonic", return_value=100.0), \
             patch("src.modules.camera_base.time.time", return_value=1_000.0):
            first = cam._get_wall_mono_offset_ns()
        assert first == int((1_000.0 - 100.0) * 1e9)

        # Still within the 0.01s recompute threshold -- and time.time() is
        # deliberately given a different value to prove the cache is used,
        # not recomputed.
        with patch("src.modules.camera_base.time.monotonic", return_value=100.005), \
             patch("src.modules.camera_base.time.time", return_value=9_999.0):
            second = cam._get_wall_mono_offset_ns()
        assert second == first

    def test_recomputes_after_threshold_elapses(self):
        cam = _make_camera()
        with patch("src.modules.camera_base.time.monotonic", return_value=100.0), \
             patch("src.modules.camera_base.time.time", return_value=1_000.0):
            cam._get_wall_mono_offset_ns()

        with patch("src.modules.camera_base.time.monotonic", return_value=100.5), \
             patch("src.modules.camera_base.time.time", return_value=1_100.5):
            updated = cam._get_wall_mono_offset_ns()
        assert updated == int((1_100.5 - 100.5) * 1e9)


class TestGetFrameTimestamp:
    def test_prefers_sensor_timestamp_with_wall_mono_offset(self):
        cam = _make_camera()
        cam._get_wall_mono_offset_ns = lambda: 500
        result = cam._get_frame_timestamp({"SensorTimestamp": 1000})
        assert result == 1500

    def test_falls_back_to_frame_wall_clock(self):
        cam = _make_camera()
        result = cam._get_frame_timestamp({"FrameWallClock": 42})
        assert result == 42

    def test_returns_none_when_neither_field_present(self):
        cam = _make_camera()
        assert cam._get_frame_timestamp({}) is None

    def test_returns_none_and_logs_on_exception(self):
        cam = _make_camera()
        bad_metadata = MagicMock()
        bad_metadata.get.side_effect = RuntimeError("boom")
        assert cam._get_frame_timestamp(bad_metadata) is None
        # Routed through the rate-limited wrapper -> logger.log(ERROR, ...)
        cam.logger.log.assert_called_once()
        assert cam.logger.log.call_args[0][0] == logging.ERROR


class TestApplyGrayscale:
    def test_converts_colour_array_to_equal_channels_in_place(self):
        cam = _make_camera()
        arr = np.zeros((4, 4, 3), dtype=np.uint8)
        arr[..., 0] = 10   # B
        arr[..., 1] = 200  # G
        arr[..., 2] = 50   # R
        shim = _FrameShim(arr)

        cam._apply_grayscale(shim)

        # After a real grayscale round-trip all three channels must be equal,
        # and the operation must have happened in-place on the same array.
        assert np.array_equal(shim.array[..., 0], shim.array[..., 1])
        assert np.array_equal(shim.array[..., 1], shim.array[..., 2])
        assert shim.array is arr


class TestApplyFramerate:
    def test_draws_top_right_in_cyan(self):
        cam = _make_camera()
        arr = np.zeros((600, 800, 3), dtype=np.uint8)

        cam._apply_framerate(arr, "24.8")

        # Top-right corner (current placement) has drawn pixels; the old
        # bottom-center placement does not.
        top_right = arr[:80, 600:800]
        bottom_center = arr[480:600, 300:500]
        assert np.any(top_right != 0)
        assert np.all(bottom_center == 0)

        # Colour is BGR cyan (255, 255, 0). At this small font scale, stroke
        # rasterization gives partial-intensity edge pixels rather than flat
        # 255s, so check the B==G, R==0 relationship the blend preserves
        # (green would have B far below G) instead of an exact match, and
        # that at least one pixel reaches near-full brightness.
        drawn_pixels = arr[np.any(arr != 0, axis=2)]
        assert np.array_equal(drawn_pixels[:, 0], drawn_pixels[:, 1])
        assert np.all(drawn_pixels[:, 2] == 0)
        assert drawn_pixels[:, 0].max() > 200


class TestCacheFrameConfig:
    def test_reads_config_and_facade_module_name(self):
        cam = _make_camera(
            config=MagicMock(),
            facade=MagicMock(),
            _rotation=90,
        )
        cam.config.get.side_effect = lambda key, default=None: {
            "camera.monochrome": True,
            "camera.overlay_timestamp": False,
        }.get(key, default)
        cam.facade.get_module_name.return_value = "camera1"

        cam._cache_frame_config()

        assert cam._cb_monochrome is True
        assert cam._cb_overlay_timestamp is False
        assert cam._cb_rotation == 90
        assert cam._cb_module_name == "camera1"
        assert cam._cb_flip_code is None
        assert cam._ts_layout_main is None
        assert cam._ts_layout_lores is None

    def test_module_name_none_without_a_facade_attribute(self):
        cam = _make_camera(config=MagicMock())
        cam.config.get.return_value = False

        cam._cache_frame_config()

        assert cam._cb_module_name is None


# ---------------------------------------------------------------------------
# Tier B: command handlers -- picam2 / facade / communication mocked
# ---------------------------------------------------------------------------

class TestTriggerAutofocus:
    def test_camera_without_autofocus_returns_error(self):
        cam = _make_camera(has_autofocus=False)
        result = cam.trigger_autofocus()
        assert result == {
            "result": "error", "output": "Camera does not support autofocus"
        }

    def test_camera_not_started_returns_error(self):
        cam = _make_camera(has_autofocus=True, picam2=MagicMock(started=False))
        result = cam.trigger_autofocus()
        assert result["result"] == "error"

    def test_success_sets_autofocus_controls(self):
        picam2 = MagicMock(started=True)
        cam = _make_camera(has_autofocus=True, picam2=picam2)

        result = cam.trigger_autofocus()

        assert result == {"result": "success"}
        picam2.set_controls.assert_called_once_with({"AfMode": 1, "AfTrigger": 0})

    def test_exception_from_picam2_is_caught(self):
        picam2 = MagicMock(started=True)
        picam2.set_controls.side_effect = RuntimeError("focus motor stuck")
        cam = _make_camera(has_autofocus=True, picam2=picam2)

        result = cam.trigger_autofocus()

        assert result["result"] == "error"
        assert "focus motor stuck" in result["output"]


class TestStartStreaming:
    def test_already_streaming_returns_false(self):
        cam = _make_camera(is_streaming=True)
        assert cam.start_streaming() is False

    def test_success_starts_camera_and_reports_status(self):
        picam2 = MagicMock(started=True)
        monitor_stream = MagicMock()
        monitor_stream.start.return_value = True
        cam = _make_camera(
            is_streaming=False, picam2=picam2, monitor_stream=monitor_stream,
            communication=MagicMock(), network=MagicMock(ip="10.0.0.5"),
        )

        result = cam.start_streaming()

        assert result is True
        assert cam.is_streaming is True
        monitor_stream.start.assert_called_once_with(8080)
        cam.communication.send_status.assert_called_once()

    def test_monitor_stream_failure_returns_false(self):
        picam2 = MagicMock(started=True)
        monitor_stream = MagicMock()
        monitor_stream.start.return_value = False
        cam = _make_camera(
            is_streaming=False, picam2=picam2, monitor_stream=monitor_stream,
            communication=MagicMock(), network=MagicMock(ip="10.0.0.5"),
        )

        result = cam.start_streaming()

        assert result is False
        assert cam.is_streaming is False

    def test_exception_reports_failure_status(self):
        picam2 = MagicMock(started=True)
        monitor_stream = MagicMock()
        monitor_stream.start.side_effect = RuntimeError("port in use")
        cam = _make_camera(
            is_streaming=False, picam2=picam2, monitor_stream=monitor_stream,
            communication=MagicMock(), network=MagicMock(ip="10.0.0.5"),
        )

        result = cam.start_streaming()

        assert result is False
        cam.communication.send_status.assert_called_once()
        sent = cam.communication.send_status.call_args[0][0]
        assert sent["type"] == "streaming_start_failed"


class TestStopStreaming:
    def test_not_streaming_returns_false(self):
        cam = _make_camera(is_streaming=False)
        assert cam.stop_streaming() is False

    def test_success_stops_and_reports_status(self):
        monitor_stream = MagicMock()
        cam = _make_camera(
            is_streaming=True, monitor_stream=monitor_stream, communication=MagicMock()
        )

        result = cam.stop_streaming()

        assert result is True
        assert cam.is_streaming is False
        monitor_stream.stop.assert_called_once()
        cam.communication.send_status.assert_called_once_with({
            "type": "streaming_stopped", "status": "success",
            "message": "Streaming stopped successfully",
        })

    def test_exception_reports_error_status(self):
        monitor_stream = MagicMock()
        monitor_stream.stop.side_effect = RuntimeError("stream already gone")
        cam = _make_camera(
            is_streaming=True, monitor_stream=monitor_stream, communication=MagicMock()
        )

        result = cam.stop_streaming()

        assert result is False
        assert cam.communication.send_status.call_args[0][0]["status"] == "error"


class TestGetHealthOverrideIsUnreachable:
    """CameraBase.get_health() calls super().get_health(), but Module (its
    only base class) never defines get_health() -- the real health command
    dispatch goes straight to self.health.get_health() instead (see
    Module.command_callbacks and Facade.get_health()), never through this
    override. So the wall_mono_offset_s field this method claims to add is
    never actually produced by any real call path -- calling it directly
    raises, which is what this documents rather than a passing "it works"
    test that would misrepresent that."""

    def test_raises_because_super_has_no_get_health(self):
        cam = _make_camera()
        with pytest.raises(AttributeError):
            cam.get_health()


class TestExposureSampling:
    """CameraBase._maybe_sample_exposure -- the coarse over/under-exposure
    data-quality indicator (pure numpy on a frame array + a throttle)."""

    def _cam(self, **cfg):
        defaults = {
            "camera.exposure_check_interval_s": 1.0,
            "camera.exposure_clip_high": 250,
            "camera.exposure_clip_low": 5,
            "camera.exposure_warn_pct": 5.0,
        }
        defaults.update(cfg)
        config = MagicMock()
        config.get.side_effect = lambda k, d=None: defaults.get(k, d)
        import collections
        return _make_camera(
            config=config,
            _exposure_sample_history=collections.deque(maxlen=3),
            _exposure_over_pct=0.0, _exposure_under_pct=0.0,
            _exposure_last_sample_s=0.0, _exposure_warned=False,
        )

    def test_all_white_frame_reads_as_overexposed(self):
        cam = self._cam()
        white = np.full((80, 80, 3), 255, dtype=np.uint8)
        with patch("src.modules.camera_base.time.monotonic", return_value=10.0):
            cam._maybe_sample_exposure(white)
        assert cam._exposure_over_pct == 100.0
        assert cam._exposure_under_pct == 0.0
        cam.logger.warning.assert_called_once()
        assert cam._exposure_warned is True

    def test_all_black_frame_reads_as_underexposed(self):
        cam = self._cam()
        black = np.zeros((80, 80, 3), dtype=np.uint8)
        with patch("src.modules.camera_base.time.monotonic", return_value=10.0):
            cam._maybe_sample_exposure(black)
        assert cam._exposure_under_pct == 100.0
        assert cam._exposure_over_pct == 0.0

    def test_well_exposed_frame_does_not_warn(self):
        cam = self._cam()
        mid = np.full((80, 80, 3), 128, dtype=np.uint8)
        with patch("src.modules.camera_base.time.monotonic", return_value=10.0):
            cam._maybe_sample_exposure(mid)
        assert cam._exposure_over_pct == 0.0
        assert cam._exposure_under_pct == 0.0
        cam.logger.warning.assert_not_called()

    def test_throttled_to_the_interval(self):
        cam = self._cam()
        white = np.full((80, 80, 3), 255, dtype=np.uint8)
        with patch("src.modules.camera_base.time.monotonic", return_value=10.0):
            cam._maybe_sample_exposure(white)
        # 0.5s later -- inside the 1s interval, skipped
        black = np.zeros((80, 80, 3), dtype=np.uint8)
        with patch("src.modules.camera_base.time.monotonic", return_value=10.5):
            cam._maybe_sample_exposure(black)
        assert cam._exposure_over_pct == 100.0  # unchanged, second call skipped
        with patch("src.modules.camera_base.time.monotonic", return_value=11.1):
            cam._maybe_sample_exposure(black)
        assert cam._exposure_over_pct < 100.0  # now rolled the black sample in

    def test_recovery_logs_once(self):
        cam = self._cam()
        white = np.full((80, 80, 3), 255, dtype=np.uint8)
        mid = np.full((80, 80, 3), 128, dtype=np.uint8)
        for t, frame in [(10.0, white), (11.1, mid), (12.2, mid), (13.3, mid)]:
            with patch("src.modules.camera_base.time.monotonic", return_value=t):
                cam._maybe_sample_exposure(frame)
        assert cam._exposure_warned is False
        cam.logger.info.assert_called_with("Exposure back within range")


# ---------------------------------------------------------------------------
# Missing/dead camera hardware: __init__'s Picamera2() probe (see the try/
# except around it) sets picam2=None + hardware_fault instead of letting the
# exception crash module startup. These tests don't exercise that probe
# itself (out of scope per this file's docstring -- it needs a real or
# deeply-faked Picamera2), only the downstream guards that make picam2=None
# a safe, well-explained state rather than a latent AttributeError.
# ---------------------------------------------------------------------------

_NO_SENSOR = "No camera sensor detected: boom"


class TestCheckPicam:
    def test_no_camera_reports_fault_reason(self):
        cam = _make_camera(picam2=None, hardware_fault=_NO_SENSOR)
        ok, message = cam._check_picam()
        assert ok is False
        assert message == _NO_SENSOR

    def test_no_camera_falls_back_to_generic_message(self):
        # hardware_fault unset (e.g. attribute never set on an old instance) --
        # still reports something, not a crash from the `or` falling through.
        cam = _make_camera(picam2=None, hardware_fault=None)
        ok, message = cam._check_picam()
        assert ok is False
        assert message == "No camera hardware detected"

    def test_camera_present_reports_sensor_model(self):
        cam = _make_camera(
            picam2=MagicMock(), hardware_fault=None, sensor_model="imx708",
        )
        ok, message = cam._check_picam()
        assert ok is True
        assert message == "imx708 present"


class TestStartNewRecordingNoHardware:
    def test_returns_false_and_reports_status_without_touching_picam2(self):
        # picam2=None on purpose -- if the guard didn't fire first, any of the
        # SplittableOutput/PyavOutput/_open_timestamp_csv calls below it would
        # raise on a None object.
        cam = _make_camera(picam2=None, hardware_fault=_NO_SENSOR, facade=MagicMock())
        result = cam._start_new_recording()
        assert result is False
        cam.facade.send_status.assert_called_once_with({
            "type": "recording_start_failed", "error": _NO_SENSOR,
        })


class TestStartNextRecordingSegmentNoHardware:
    def test_returns_false_without_touching_picam2(self):
        cam = _make_camera(picam2=None, hardware_fault="boom")
        assert cam._start_next_recording_segment() is False


class TestStartStreamingNoHardware:
    def test_returns_false_and_reports_status(self):
        cam = _make_camera(
            is_streaming=False, picam2=None, hardware_fault=_NO_SENSOR,
            communication=MagicMock(),
        )
        result = cam.start_streaming()
        assert result is False
        cam.communication.send_status.assert_called_once_with({
            "type": "streaming_start_failed", "status": "error", "error": _NO_SENSOR,
        })


class TestConfigureModuleSpecialNoHardware:
    def test_no_ops_cleanly_instead_of_raising(self):
        cam = _make_camera(picam2=None, hardware_fault="boom")
        # subclass hook, called first either way
        cam._configure_module_extra = MagicMock()
        cam.configure_module_special(["camera.fps"])  # must not raise
        cam._configure_module_extra.assert_called_once_with(["camera.fps"])
