import json
import os
import time
import datetime
import csv
import threading
from dataclasses import dataclass
from typing import Optional, Tuple, Dict

import numpy as np
import cv2

from flask import Flask, Response, request
from picamera2 import Picamera2, MappedArray
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput, SplittableOutput
from pathlib import Path
from flask import jsonify

import subprocess

import sys

from modules.examples.loom_camera.loom_stimulus import LoomStimulusController, LoomStimulusConfig

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command, check


@dataclass(frozen=True)
class LoomCrossingState:
    """
    Container for zone-crossing logic state.

    Parameters
    ----------
    in_zone_prev : bool
        Previous boolean zone occupancy.
    state : str
        One of {'out', 'entering', 'in', 'leaving'}.
    last_event : str or None
        One of {'enter', 'leave'} when an event occurred, else None.
    """
    in_zone_prev: bool = False
    state: str = "out"
    last_event: Optional[str] = None

class LoomBlobDiffTracker:
    """
    ROI-aware abs-diff blob tracker with bridge-fill and hold-last behavior.

    Notes
    -----
    - Detection is based on abs(frame_t - frame_{t-1}) thresholded in processed space.
    - If no blob is found, the last known center is *held* for `patience_frames`.
    - Two centers are maintained:
        - last_detection_center_proc: raw centroid of best blob (processed px)
        - last_display_center_proc: exponentially smoothed centroid (processed px)
    - The tracker returns the *display* center (held/smoothed) when available.
    """

    def __init__(
        self,
        *,
        process_width: int = 256,
        thr_hi: float = 10.0,
        gap_h_px: int = 15,
        gap_v_px: int = 15,
        close_px: int = 7,
        open_px: int = 5,
        min_area_px: int = 50,
        patience_frames: int = 1000,
        smoothing_alpha: float = 0.3,
    ) -> None:
        self.process_width = int(process_width)
        self.thr_hi = float(thr_hi)
        self.min_area_px = int(min_area_px)
        self.patience_frames = int(patience_frames)
        self.smoothing_alpha = float(smoothing_alpha)

        def _rect(w: int, h: int) -> np.ndarray:
            return cv2.getStructuringElement(cv2.MORPH_RECT, (max(1, w), max(1, h)))

        def _ellipse(s: int) -> np.ndarray:
            return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(1, s), max(1, s)))

        self._kern_h = _rect(int(gap_h_px), 1) if int(gap_h_px) > 1 else None
        self._kern_v = _rect(1, int(gap_v_px)) if int(gap_v_px) > 1 else None
        self._kern_close = _ellipse(int(close_px)) if int(close_px) > 1 else None
        self._kern_open = _ellipse(int(open_px)) if int(open_px) > 1 else None

        self._roi_mask_proc: Optional[np.ndarray] = None

        self._prev_gray: Optional[np.ndarray] = None
        self.last_detection_center_proc: Optional[Tuple[float, float]] = None
        self.last_display_center_proc: Optional[Tuple[float, float]] = None
        self._miss_count: int = 0

    def reset(self) -> None:
        """Reset temporal state between sessions."""
        self._prev_gray = None
        self.last_detection_center_proc = None
        self.last_display_center_proc = None
        self._miss_count = 0

    @staticmethod
    def _resize_to_width(w0: int, h0: int, target_width: int) -> Tuple[int, int]:
        w = max(1, int(target_width))
        h = max(1, int(round(w * h0 / w0)))
        return w, h

    def set_roi_mask_proc(self, roi_mask_proc: Optional[np.ndarray]) -> None:
        """
        Parameters
        ----------
        roi_mask_proc : numpy.ndarray or None
            Boolean ROI mask of shape (ny, nx). None means full frame ROI.
        """
        self._roi_mask_proc = roi_mask_proc.astype(bool) if roi_mask_proc is not None else None

    def detect_center(
        self,
        frame_bgr: np.ndarray,
    ) -> Tuple[Optional[Tuple[float, float]], Tuple[float, float], Tuple[int, int]]:
        """
        Detect / update centroid.

        Parameters
        ----------
        frame_bgr : numpy.ndarray
            Source BGR frame, shape (H, W, 3).

        Returns
        -------
        center_proc_display : tuple of float or None
            Held + smoothed centroid in processed pixels. None if track is lost.
        scale_proc_to_src : tuple of float
            (sx, sy) mapping processed px -> source px.
        proc_shape : tuple of int
            (nx, ny) processed dimensions.
        """
        h0, w0 = frame_bgr.shape[:2]
        nx, ny = self._resize_to_width(w0, h0, self.process_width)
        sx = w0 / float(nx)
        sy = h0 / float(ny)

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        proc = cv2.resize(gray, (nx, ny), interpolation=cv2.INTER_AREA)

        if self._prev_gray is None or self._prev_gray.shape != proc.shape:
            self._prev_gray = proc
            # No detection on first frame; return current held display center if any
            return self.last_display_center_proc, (sx, sy), (nx, ny)

        diff = np.abs(proc.astype(np.float32) - self._prev_gray.astype(np.float32))
        self._prev_gray = proc

        mask = (diff >= self.thr_hi).astype(np.uint8)
        if self._roi_mask_proc is not None and self._roi_mask_proc.shape == mask.shape:
            mask = (mask.astype(bool) & self._roi_mask_proc).astype(np.uint8)

        for kern, op in (
            (self._kern_h, cv2.MORPH_CLOSE),
            (self._kern_v, cv2.MORPH_CLOSE),
            (self._kern_close, cv2.MORPH_CLOSE),
            (self._kern_open, cv2.MORPH_OPEN),
        ):
            if kern is not None:
                mask = cv2.morphologyEx(mask, op, kern)

        if self._roi_mask_proc is not None and self._roi_mask_proc.shape == mask.shape:
            mask = (mask.astype(bool) & self._roi_mask_proc).astype(np.uint8)

        n_labels, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)

        best_label = 0
        best_area = 0
        for lab in range(1, n_labels):
            area = int(stats[lab, cv2.CC_STAT_AREA])
            if area >= self.min_area_px and area > best_area:
                best_area = area
                best_label = lab

        if best_label > 0:
            cx_raw = float(centroids[best_label][0])
            cy_raw = float(centroids[best_label][1])
            self.last_detection_center_proc = (cx_raw, cy_raw)
            self._miss_count = 0

            if self.last_display_center_proc is None or self.smoothing_alpha <= 0:
                self.last_display_center_proc = (cx_raw, cy_raw)
            else:
                a = float(self.smoothing_alpha)
                self.last_display_center_proc = (
                    (1.0 - a) * self.last_display_center_proc[0] + a * cx_raw,
                    (1.0 - a) * self.last_display_center_proc[1] + a * cy_raw,
                )
        else:
            # HOLD-LAST behavior: keep last_display_center_proc for patience_frames
            if self.last_display_center_proc is not None:
                self._miss_count += 1
                if self._miss_count > self.patience_frames:
                    self.last_detection_center_proc = None
                    self.last_display_center_proc = None
                    self._miss_count = 0

        return self.last_display_center_proc, (sx, sy), (nx, ny)



def loom_load_roi_and_line(
    roi_json_path: Optional[str],
    *,
    src_width: int,
    src_height: int,
    proc_width: int,
    proc_height: int,
) -> Tuple[np.ndarray, Optional[np.ndarray], Optional[Dict[str, float]]]:
    """
    Load an arena ROI polygon and a line definition from JSON and map to proc/source spaces.

    Expected JSON schema (example)
    ------------------------------
    {
      "image_size": {"width": 1080, "height": 1080},
      "arena_polygon": [{"x": 10, "y": 20}, ...],
      "crossing_line": {
        "kind": "vertical", "x": 540, "direction": "left_is_in"
      }
    }

    Parameters
    ----------
    roi_json_path : str or None
        JSON file path. If None, ROI is full frame and no line exists.
    src_width, src_height : int
        Current camera frame dimensions.
    proc_width, proc_height : int
        Processed frame dimensions.

    Returns
    -------
    roi_mask_proc : numpy.ndarray
        Boolean ROI mask in processed space with shape (proc_height, proc_width).
    arena_poly_src : numpy.ndarray or None
        Arena polygon points in source coordinates, shape (N, 2).
    crossing_line_src : dict or None
        Line definition in source coordinates. Contains:
        - 'kind': 'vertical' (currently supported)
        - 'x': float
        - 'direction': 'left_is_in' or 'right_is_in'
    """
    if roi_json_path is None:
        return np.ones((proc_height, proc_width), dtype=bool), None, None

    roi_path = Path(roi_json_path).expanduser()
    with open(roi_path, "r") as f:
        data = json.load(f)

    ann_w = float(data.get("image_size", {}).get("width", src_width))
    ann_h = float(data.get("image_size", {}).get("height", src_height))
    sx_src = src_width / ann_w
    sy_src = src_height / ann_h

    poly_pts = None
    if "arena_polygon" in data:
        poly_pts = data["arena_polygon"]
    elif "points" in data:
        poly_pts = data["points"]

    pts_json = None
    if poly_pts is not None and len(poly_pts) >= 3:
        pts_json = np.array([[p["x"], p["y"]] for p in poly_pts], dtype=float)

    arena_poly_src = None
    if pts_json is not None and len(pts_json) >= 3:
        arena_poly_src = pts_json.copy()
        arena_poly_src[:, 0] *= sx_src
        arena_poly_src[:, 1] *= sy_src

    # ROI mask in processed space
    if arena_poly_src is None:
        roi_mask_proc = np.ones((proc_height, proc_width), dtype=bool)
    else:
        pts_proc = arena_poly_src.copy()
        pts_proc[:, 0] *= (proc_width / float(src_width))
        pts_proc[:, 1] *= (proc_height / float(src_height))
        poly_i = np.round(pts_proc).astype(np.int32).reshape((-1, 1, 2))
        mask = np.zeros((proc_height, proc_width), dtype=np.uint8)
        cv2.fillPoly(mask, [poly_i], 1)
        roi_mask_proc = mask.astype(bool)

    # Crossing line (source coords)
    crossing_line_src = None

    if "crossing_line" in data and isinstance(data["crossing_line"], dict):
        cl = data["crossing_line"]
        if str(cl.get("kind", "vertical")).lower() == "vertical" and "x" in cl:
            crossing_line_src = {
                "kind": "vertical",
                "x": float(cl["x"]) * sx_src,
                "direction": str(cl.get("direction", "left_is_in")).lower(),
            }
    elif "vertical_line" in data and isinstance(data["vertical_line"], dict) and "x" in data["vertical_line"]:
        crossing_line_src = {
            "kind": "vertical",
            "x": float(data["vertical_line"]["x"]) * sx_src,
            "direction": "left_is_in",
        }

    return roi_mask_proc, arena_poly_src, crossing_line_src


def loom_update_crossing_state(
    *,
    crossing_line_src: Optional[Dict[str, float]],
    center_src: Optional[Tuple[float, float]],
    prev: LoomCrossingState,
    track_valid: bool,
) -> LoomCrossingState:
    """
    Update enter/in/leave/out state relative to a crossing line.

    This version matches the Basler behavior:
    - If tracking is currently invalid (lost beyond patience), state is forced to 'out'
      and in_zone_prev becomes False.
    - If tracking is valid but center is None (should not happen often), preserve previous state.

    Parameters
    ----------
    crossing_line_src : dict or None
        Line definition in source coords.
    center_src : tuple of float or None
        Tracked centroid in source coords.
    prev : LoomCrossingState
        Previous state.
    track_valid : bool
        Whether we currently consider the track valid (held or detected).

    Returns
    -------
    next_state : LoomCrossingState
        Updated state with transition event labels.
    """
    if not track_valid or crossing_line_src is None:
        return LoomCrossingState(in_zone_prev=False, state="out", last_event=None)

    if center_src is None:
        # Preserve previous state (hold) if somehow center isn't available.
        return LoomCrossingState(in_zone_prev=prev.in_zone_prev, state=prev.state, last_event=None)

    if crossing_line_src.get("kind") != "vertical":
        return LoomCrossingState(in_zone_prev=prev.in_zone_prev, state=prev.state, last_event=None)

    x_line = float(crossing_line_src["x"])
    direction = str(crossing_line_src.get("direction", "left_is_in")).lower()

    cx, cy = center_src
    in_zone = (cx > x_line) if direction == "right_is_in" else (cx < x_line)

    last_event = None
    if (not prev.in_zone_prev) and in_zone:
        state = "entering"
        last_event = "enter"
    elif prev.in_zone_prev and in_zone:
        state = "in"
    elif prev.in_zone_prev and (not in_zone):
        state = "leaving"
        last_event = "leave"
    else:
        state = "out"

    return LoomCrossingState(in_zone_prev=in_zone, state=state, last_event=last_event)


class LoomCameraModule(Module):
    """
    SAVIOUR System - Loom Camera Module

    Notes
    -----
    - Uses Picamera2 for acquisition, segmented recording, and MJPEG preview like APACameraModule.
    - Implements live abs-diff blob tracking within an arena ROI.
    - Computes enter/in/leave/out state relative to a configured crossing line.
    """

    _STREAM_FPS = 24

    def __init__(self, module_type: str = "loom_camera"):
        super().__init__(module_type)
        self.config.load_module_config("loom_camera_config.json")

        # --- Camera ---
        self.picam2 = Picamera2()
        self.sensor_modes = self.picam2.sensor_modes
        self.sensor_model = self.picam2.camera_properties.get("Model", "").lower()
        self.has_autofocus = "imx708" in self.sensor_model
        self.logger.info(f"Sensor: {self.sensor_model!r}, autofocus: {self.has_autofocus}")

        self.height: Optional[int] = None
        self.width: Optional[int] = None
        self.lores_width: Optional[int] = None
        self.lores_height: Optional[int] = None
        self.fps: Optional[float] = None
        self.mode: Optional[dict] = None

        # --- Streaming server (same pattern as APA) ---
        self.streaming_app = Flask(__name__)
        self.streaming_server_thread = None
        self.streaming_server = None
        self.should_stop_streaming = False
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self._last_stream_encode_time = 0.0
        self._stream_interval_s = 0.0
        self.register_routes()

        # --- Segmented recording + timestamp CSV (reuse APA pattern) ---
        self.is_streaming = False
        self.is_recording = False

        self.main_encoder: Optional[H264Encoder] = None
        self.lores_encoder: Optional[H264Encoder] = None
        self.file_output = None

        self._timestamp_csv_file = None
        self._timestamp_csv_writer = None
        self._current_csv_path = None
        self._frame_id = 0
        self._csv_prev_ns = None
        self.last_frame_timestamp = None
        self._wall_mono_offset_ns: int = int((time.time() - time.monotonic()) * 1e9)

        # --- Loom tracking state ---
        self.tracker: Optional[LoomBlobDiffTracker] = None
        self.roi_json_path: Optional[str] = None
        self.roi_mask_proc: Optional[np.ndarray] = None
        self.arena_poly_src: Optional[np.ndarray] = None
        self.crossing_line_src: Optional[Dict[str, float]] = None

        self.last_center_src: Optional[Tuple[float, float]] = None  # (cx, cy) source pixels
        self.crossing_state = LoomCrossingState()

        # --- Health checks ---
        self.module_checks = {self._check_picam}

        # --- Commands ---
        self.command.set_commands({
            "start_streaming": self.start_streaming,
            "stop_streaming": self.stop_streaming,
            "get_sensor_modes": self.get_sensor_modes,
            "set_loom_roi": self.set_loom_roi,
            "loom_stimulus_start": self.loom_stimulus_start,
            "loom_stimulus_stop": self.loom_stimulus_stop,
            "loom_stimulus_test_near_screen": self.loom_stimulus_test_near_screen,
        })

        # Configure everything
        self._configure_camera()
        self._configure_loom_tracking()

        # --- Recording state required by Module abstract recording API ---
        self.recording_start_time: Optional[float] = None
        self.current_video_segment: Optional[str] = None
        self.last_video_segment: Optional[str] = None
        self.file_output = None

        self._hold_miss_count: int = 0
        self._hold_patience_frames: int = int(self.config.get("loom_tracking.patience_frames", 10))
        self._track_valid: bool = False

        self._hold_forever_when_still: bool = bool(self.config.get("loom_tracking.hold_forever_when_still", False))

        # --- Loom stimulus (local HDMI) ---
        # The renderer always runs so the grey background + photodiode box are
        # visible during habituation.  _stimulus_armed gates whether tracking
        # events (and the arm command) actually fire the looming animation.
        self._loom_stimulus = LoomStimulusController(self._build_stimulus_config())
        self._stimulus_last_restart: float = 0.0
        self._loom_stimulus.start()
        self.logger.info(
            "loom_stimulus: renderer spawned (WAYLAND_DISPLAY=%s, DISPLAY=%s)",
            os.environ.get("WAYLAND_DISPLAY"), os.environ.get("DISPLAY"),
        )

    # ---------------------------------------------------------------------
    # Config hooks
    # ---------------------------------------------------------------------
    def _resolve_roi_json_path(self) -> Optional[Path]:
        """
        Resolve the ROI JSON path from config to an absolute path.

        Resolution
        ----------
        - If config path is absolute: use it.
        - If config path is relative: resolve relative to this module file.
        - If unset/empty: return None.

        Returns
        -------
        roi_path : pathlib.Path or None
            Resolved ROI JSON file path.
        """
        lt = self.config.get("loom_tracking", {})
        p = lt.get("roi_json_path", None)
        if not p:
            return None

        roi_path = Path(str(p)).expanduser()
        if roi_path.is_absolute():
            return roi_path

        return (Path(__file__).resolve().parent / roi_path).resolve()

    def _set_default_roi(self, *, proc_w: int, proc_h: int) -> None:
        """
        Set default ROI masking/line state (full ROI, no line).

        Parameters
        ----------
        proc_w, proc_h : int
            Processed tracking frame size.
        """
        self.roi_mask_proc = np.ones((proc_h, proc_w), dtype=bool)
        self.arena_poly_src = None
        self.crossing_line_src = None
        if self.tracker is not None:
            self.tracker.set_roi_mask_proc(self.roi_mask_proc)

    def _reload_roi_for_current_geometry(self, *, src_w: int, src_h: int, proc_w: int, proc_h: int) -> None:
        """
        (Re)load ROI/line JSON and update tracker ROI mask, fail-soft if missing.

        Parameters
        ----------
        src_w, src_h : int
            Source frame size in pixels (main stream).
        proc_w, proc_h : int
            Processed tracking frame size in pixels.
        """
        roi_path = self._resolve_roi_json_path()
        if roi_path is None:
            self._set_default_roi(proc_w=proc_w, proc_h=proc_h)
            return

        if not roi_path.exists():
            # Do not error: allow running without ROI file.
            self.logger.warning(f"ROI JSON not found, using full-frame ROI: {roi_path}")
            self._set_default_roi(proc_w=proc_w, proc_h=proc_h)
            return

        try:
            roi_mask_proc, arena_poly_src, crossing_line_src = loom_load_roi_and_line(
                str(roi_path),
                src_width=src_w,
                src_height=src_h,
                proc_width=proc_w,
                proc_height=proc_h,
            )
            self.roi_mask_proc = roi_mask_proc
            self.arena_poly_src = arena_poly_src
            self.crossing_line_src = crossing_line_src
            if self.tracker is not None:
                self.tracker.set_roi_mask_proc(self.roi_mask_proc)
            self.logger.info(f"Loaded ROI JSON: {roi_path}")
        except Exception as e:
            self.logger.error(f"Failed to load ROI JSON ({roi_path}) -> default ROI: {e}")
            self._set_default_roi(proc_w=proc_w, proc_h=proc_h)

    def _get_video_filename(self) -> str:
        """
        Construct a new segment filename using the SAVIOUR facade helpers.

        Returns
        -------
        filename : str
            Segment filename including extension.
        """
        strtime = self.facade.get_utc_time(self.facade.get_segment_start_time())
        ext = self.config.get("recording.recording_filetype", "ts")
        return f"{self.facade.get_filename_prefix()}_({self.facade.get_segment_id()}_{strtime}).{ext}"

    def _fix_positioning_timestamps(self, filename: str) -> None:
        """
        Rewrap the recorded MPEG-TS to reset timestamps, improving downstream decoding.

        Parameters
        ----------
        filename : str
            Path to the .ts file to rewrap.
        """
        tmp = f"{filename[:-3]}_fmt.ts"
        try:
            subprocess.run(
                ["ffmpeg", "-i", filename, "-map", "0", "-c", "copy", "-reset_timestamps", "1", tmp],
                check=True,
            )
            os.replace(tmp, filename)
        except Exception as e:
            self.logger.error(f"ffmpeg timestamp fix failed for {filename}: {e}")

    def _start_new_recording(self) -> None:
        """
        Start recording a new segmented recording session (first segment).

        Notes
        -----
        This uses Picamera2's SplittableOutput so segments can be rotated without
        stopping the encoder.
        """
        filename = self._get_video_filename()
        self.logger.info(f"Starting recording: {filename}")
        self.current_video_segment = filename
        self.facade.add_session_file(filename)

        if not self.picam2.started:
            self.picam2.start()
            time.sleep(0.1)

        self.file_output = SplittableOutput(PyavOutput(filename, format="mpegts"))
        self.main_encoder.output = self.file_output

        self.picam2.start_encoder(self.main_encoder, name="main")
        self.recording_start_time = time.time()

        self._open_timestamp_csv(filename)

        # Reset tracking state per session (optional but recommended)
        if self.tracker is not None:
            self.tracker.reset()
        self.last_center_src = None
        self.crossing_state = LoomCrossingState()

    def _start_next_recording_segment(self) -> None:
        """
        Rotate to a new recording segment without stopping acquisition/encoder.
        """
        if self.file_output is None:
            # Defensive: if called without an active recording session, start new.
            self._start_new_recording()
            return

        self._close_timestamp_csv()

        self.last_video_segment = self.current_video_segment
        if self.last_video_segment:
            self.facade.stage_file_for_export(self.last_video_segment)

        filename = self._get_video_filename()
        self.current_video_segment = filename
        self.facade.add_session_file(filename)

        self._open_timestamp_csv(filename)
        self.file_output.split_output(PyavOutput(filename, format="mpegts"))
        self.logger.info(f"Switched to new segment: {filename}")

    def _stop_recording(self) -> bool:
        """
        Stop recording and finalize segment + sidecar files.

        Returns
        -------
        ok : bool
            True if stop was successful, else False.
        """
        try:
            # Stop encoder first so the file is finalized.
            if self.main_encoder is not None:
                self.picam2.stop_encoder(self.main_encoder)

            self._close_timestamp_csv()

            # Rewrap TS timestamps for all segments (same approach as APA)
            for f in getattr(self, "session_files", []):
                if isinstance(f, str) and f.endswith(".ts"):
                    self._fix_positioning_timestamps(f)

            # Ensure the last segment is staged for export
            if getattr(self, "current_video_segment", None):
                self.facade.stage_file_for_export(self.current_video_segment)

            return True
        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            return False

    def _build_stimulus_config(self) -> LoomStimulusConfig:
        cfg = LoomStimulusConfig(
            texture_path=str(self.config.get("loom_stimulus.texture_path", "/usr/local/src/saviour/src/modules/examples/loom_camera/loom_circle.png")),
            initial_size_cm=float(self.config.get("loom_stimulus.initial_size_cm", 6.0)),
            final_size_cm=float(self.config.get("loom_stimulus.final_size_cm", 40.0)),
            initial_pos_ndc=tuple(self.config.get("loom_stimulus.initial_pos_ndc", [0.5, 0.0])),
            final_pos_ndc=tuple(self.config.get("loom_stimulus.final_pos_ndc", [0.5, 0.0])),
            travel_time_s=float(self.config.get("loom_stimulus.travel_time_s", 5.0)),
            loom_wait_time_s=float(self.config.get("loom_stimulus.loom_wait_time_s", 0.5)),
            round_size=int(self.config.get("loom_stimulus.round_size", 5)),
            image_angle_deg=float(self.config.get("loom_stimulus.image_angle_deg", 90.0)),
            background_rgba=tuple(self.config.get("loom_stimulus.background_rgba", [0.5, 0.5, 0.5, 1.0])),
            start_monitor_index=int(self.config.get("loom_stimulus.start_monitor_index", 1)),
            flip_horizontal=bool(self.config.get("loom_stimulus.flip_horizontal", False)),
            screen_width_cm=float(self.config.get("loom_stimulus.screen_width_cm", 105.41)),
            screen_height_cm=float(self.config.get("loom_stimulus.screen_height_cm", 59.29)),
            size_correction=float(self.config.get("loom_stimulus.size_correction", 1.125)),
            photodiode_box_px=int(self.config.get("loom_stimulus.photodiode_box_px", 80)),
            photodiode_y_ndc=float(self.config.get("loom_stimulus.photodiode_y_ndc", 0.0)),
            keepalive_interval_s=float(self.config.get("loom_stimulus.keepalive_interval_s", 30.0)),
            x_offset_ndc=float(self.config.get("loom_stimulus.x_offset_ndc", 0.0)),
        )
        self.logger.info(
            "loom_stimulus config: travel_time_s=%.2f loom_wait_time_s=%.2f "
            "initial_size_cm=%.1f final_size_cm=%.1f start_monitor_index=%d",
            cfg.travel_time_s, cfg.loom_wait_time_s,
            cfg.initial_size_cm, cfg.final_size_cm, cfg.start_monitor_index,
        )
        return cfg

    # Keys that require destroying and recreating the GL window (monitor layout changes).
    _STIMULUS_RESTART_KEYS = {
        "loom_stimulus.start_monitor_index",
        "loom_stimulus.flip_horizontal",
    }

    # Keys that can be hot-patched into the running renderer via "reconfigure" IPC.
    _STIMULUS_RECONFIGURE_KEYS = {
        "loom_stimulus.texture_path", "loom_stimulus.initial_size_cm",
        "loom_stimulus.final_size_cm", "loom_stimulus.initial_pos_ndc",
        "loom_stimulus.final_pos_ndc", "loom_stimulus.travel_time_s",
        "loom_stimulus.loom_wait_time_s", "loom_stimulus.round_size",
        "loom_stimulus.image_angle_deg", "loom_stimulus.background_rgba",
        "loom_stimulus.screen_width_cm", "loom_stimulus.screen_height_cm",
        "loom_stimulus.size_correction", "loom_stimulus.photodiode_box_px",
        "loom_stimulus.photodiode_y_ndc", "loom_stimulus.keepalive_interval_s",
        "loom_stimulus.x_offset_ndc",
    }

    def _build_reconfigure_payload(self) -> dict:
        cfg = self._build_stimulus_config()
        return {
            "background_rgba":  list(cfg.background_rgba),
            "screen_width_cm":  cfg.screen_width_cm,
            "screen_height_cm": cfg.screen_height_cm,
            "size_correction":  cfg.size_correction,
            "initial_size_cm":  cfg.initial_size_cm,
            "final_size_cm":    cfg.final_size_cm,
            "initial_pos_ndc":  list(cfg.initial_pos_ndc),
            "final_pos_ndc":    list(cfg.final_pos_ndc),
            "travel_time_s":    cfg.travel_time_s,
            "loom_wait_time_s": cfg.loom_wait_time_s,
            "round_size":       cfg.round_size,
            "image_angle_deg":  cfg.image_angle_deg,
            "texture_path":     cfg.texture_path,
            "photodiode_box_px": cfg.photodiode_box_px,
            "photodiode_y_ndc": cfg.photodiode_y_ndc,
            "keepalive_interval_s": cfg.keepalive_interval_s,
            "x_offset_ndc":         cfg.x_offset_ndc,
        }

    def configure_module_special(self, updated_keys: Optional[list]):
        self._configure_loom_tracking()

        all_stimulus_keys = self._STIMULUS_RESTART_KEYS | self._STIMULUS_RECONFIGURE_KEYS
        any_stimulus_changed = updated_keys is None or bool(
            all_stimulus_keys & set(updated_keys)
        )

        if any_stimulus_changed:
            needs_restart = updated_keys is None or bool(
                self._STIMULUS_RESTART_KEYS & set(updated_keys)
            )
            if needs_restart:
                # Monitor layout changed — must destroy and recreate the GL window.
                # Armed state resets to False on restart (safe default).
                self._loom_stimulus.shutdown()
                self._loom_stimulus = LoomStimulusController(self._build_stimulus_config())
                self._loom_stimulus.start()
                self.logger.info("loom_stimulus: renderer restarted (monitor layout changed)")
            else:
                # All other param changes are hot-patched without touching the window.
                self._loom_stimulus.reconfigure(self._build_reconfigure_payload())
                self.logger.info("loom_stimulus: config hot-patched (no window restart)")

        # Keys that require full stop/reconfigure/start.
        restart_keys = {"camera.fps", "camera.width", "camera.height", "camera.bitrate_mb", "camera.sensor_mode_index", "camera.rotation"}
        # Keys that can be applied live via set_controls() without stopping.
        controls_only_keys = {
            "camera.gain", "camera.brightness", "camera.exposure_time",
            "camera.manual_exposure", "camera.ae_enable",
            "camera.lens_position", "camera.autofocus_mode",
        }
        all_camera_keys = restart_keys | controls_only_keys

        # No camera keys changed — nothing more to do.
        if updated_keys is not None and not any(k in all_camera_keys for k in updated_keys):
            return

        changed = set(updated_keys or [])
        needs_restart = bool(restart_keys & changed)
        controls_only = bool(controls_only_keys & changed) and not needs_restart

        if controls_only and self.picam2.started:
            # Apply exposure/gain/AE/AF live — no stream interruption.
            ae_enabled = bool(self.config.get("camera.ae_enable", False))
            controls: dict = {
                "AeEnable": ae_enabled,
                "Brightness": float(self.config.get("camera.brightness", 0.0)),
            }
            if not ae_enabled:
                controls["AnalogueGain"] = float(self.config.get("camera.gain", 1.0))
                controls["ExposureTime"] = int(self.config.get("camera.exposure_time", 10000))
            if self.has_autofocus:
                _AF_MODE_MAP = {"manual": 0, "auto": 1, "continuous": 2}
                af_mode = _AF_MODE_MAP.get(self.config.get("camera.autofocus_mode", "manual"), 0)
                controls["AfMode"] = af_mode
                if af_mode == 0:
                    controls["LensPosition"] = float(self.config.get("camera.lens_position", 0.0))
            self.picam2.set_controls(controls)
        elif needs_restart:
            if self.is_streaming:
                self.stop_streaming()
                time.sleep(0.5)
                self._configure_camera()
                time.sleep(0.2)
                self.start_streaming()
            else:
                was_started = self.picam2.started
                self._configure_camera()
                if was_started:
                    self.picam2.start()


    def _configure_loom_tracking(self) -> None:
        """
        Configure ROI/line geometry and the diff tracker from module config.

        Expects keys
        ------------
        loom_tracking.enabled : bool
        loom_tracking.roi_json_path : str or None
        loom_tracking.process_width : int
        loom_tracking.thr_hi : float
        loom_tracking.gap_h_px, gap_v_px, close_px, open_px, min_area_px, patience_frames, smoothing_alpha
        loom_tracking.overlay : dict (colors, thickness)
        """
        enabled = bool(self.config.get("loom_tracking.enabled", True))
        if not enabled:
            self.tracker = None
            self.roi_json_path = None
            self.roi_mask_proc = None
            self.arena_poly_src = None
            self.crossing_line_src = None
            self.last_center_src = None
            self.crossing_state = LoomCrossingState()
            return

        lt = self.config.get("loom_tracking", {})
        self.roi_json_path = lt.get("roi_json_path", None)

        self._hold_patience_frames = int(lt.get("patience_frames", 10))
        self._hold_miss_count = 0
        self._track_valid = False
        self._hold_forever_when_still = bool(lt.get("hold_forever_when_still", False))

        self.tracker = LoomBlobDiffTracker(
            process_width=int(lt.get("process_width", 256)),
            thr_hi=float(lt.get("thr_hi", 10.0)),
            gap_h_px=int(lt.get("gap_h_px", 15)),
            gap_v_px=int(lt.get("gap_v_px", 15)),
            close_px=int(lt.get("close_px", 7)),
            open_px=int(lt.get("open_px", 5)),
            min_area_px=int(lt.get("min_area_px", 50)),
            patience_frames=int(lt.get("patience_frames", 10)),
            smoothing_alpha=float(lt.get("smoothing_alpha", 0.3)),
        )

        # ROI mapping depends on current camera size; will lazy-init on first frame too.
        self.roi_mask_proc = None
        self.arena_poly_src = None
        self.crossing_line_src = None
        self.last_center_src = None
        self.crossing_state = LoomCrossingState()


    # ---------------------------------------------------------------------
    # Camera configuration (same style as APA)
    # ---------------------------------------------------------------------

    def _configure_camera(self) -> bool:
        try:
            if self.picam2.started:
                self.picam2.stop()

            self.fps = float(self.config.get("camera.fps", 30))
            self.width = int(self.config.get("camera.width", 1080))
            self.height = int(self.config.get("camera.height", 1080))

            self.lores_width = min(self.width, 640)
            self.lores_height = min(self.height, int(640 * self.height / self.width))
            self._stream_interval_s = 0.0 if self.fps <= 35 else 1.0 / float(self._STREAM_FPS)

            mode_index = max(0, min(int(self.config.get("camera.sensor_mode_index", 0)), len(self.sensor_modes) - 1))
            self.mode = self.sensor_modes[mode_index]

            max_fps = float(self.mode.get("fps", float("inf")))
            if self.fps > max_fps:
                self.logger.warning(f"fps {self.fps} clamped to mode max {max_fps:.1f}")
                self.fps = max_fps

            max_w, max_h = self.mode["size"]
            if self.width > max_w or self.height > max_h:
                self.width = min(self.width, max_w)
                self.height = min(self.height, max_h)
                self.lores_width = int(self.width / 2)
                self.lores_height = int(self.height / 2)

            exposure_time = (
                int(self.config.get("camera.exposure_time", 10000))
                if bool(self.config.get("camera.manual_exposure", False))
                else int(1_000_000 / self.fps)
            )
            ae_enabled = bool(self.config.get("camera.ae_enable", False))

            sensor = {"output_size": self.mode["size"], "bit_depth": self.mode["bit_depth"]}
            main = {"size": (self.width, self.height), "format": "RGB888"}
            lores = {"size": (self.lores_width, self.lores_height), "format": "RGB888"}
            controls = {
                "FrameRate": self.fps,
                "Brightness": float(self.config.get("camera.brightness", 0.0)),
                "AeEnable": ae_enabled,
            }
            if not ae_enabled:
                controls["AnalogueGain"] = float(self.config.get("camera.gain", 1.0))
                controls["ExposureTime"] = exposure_time

            if self.has_autofocus:
                _AF_MODE_MAP = {"manual": 0, "auto": 1, "continuous": 2}
                af_mode = _AF_MODE_MAP.get(self.config.get("camera.autofocus_mode", "manual"), 0)
                controls["AfMode"] = af_mode
                if af_mode == 0:
                    controls["LensPosition"] = float(self.config.get("camera.lens_position", 0.0))

            from libcamera import Transform
            rotation = int(self.config.get("camera.rotation", 0))
            if rotation not in (0, 90, 180, 270):
                rotation = 0
            self._rotation = rotation
            self._rotation_logged = False  # reset so post_callback logs first rotated frame
            transform = Transform()
            self.logger.info(
                f"Camera rotation config: {rotation}° "
                f"({'software via post_callback' if rotation else 'none'})"
            )

            config = self.picam2.create_video_configuration(
                main=main,
                lores=lores,
                sensor=sensor,
                controls=controls,
                transform=transform,
                buffer_count=16,
            )
            self.picam2.configure(config)

            # Hook callbacks
            self.picam2.pre_callback = self._loom_frame_precallback
            self.picam2.post_callback = self._stream_post_callback

            bitrate = int(self.config.get("camera.bitrate_mb", 2)) * 1_000_000
            self.main_encoder = H264Encoder(bitrate=bitrate)
            self.lores_encoder = H264Encoder(bitrate=max(100_000, bitrate // 10))

            # Invalidate cached timestamp layout on resolution change
            for attr in ("_ts_layout_main", "_ts_layout_lores"):
                if hasattr(self, attr):
                    delattr(self, attr)

            self.logger.info(f"Camera configured: {self.width}×{self.height} @ {self.fps}fps")
            return True
        except Exception as e:
            self.logger.error(f"Error configuring camera: {e}")
            return False


    # ---------------------------------------------------------------------
    # Timestamp helper + CSV (reuse APA style)
    # ---------------------------------------------------------------------

    def _get_frame_timestamp(self, req) -> Optional[int]:
        try:
            metadata = req.get_metadata()
            sensor_ts = metadata.get("SensorTimestamp")
            if sensor_ts is not None:
                return int(sensor_ts) + int(self._wall_mono_offset_ns)
            return metadata.get("FrameWallClock")
        except Exception as e:
            self.logger.error(f"Timestamp error: {e}")
            return None

    def _open_timestamp_csv(self, video_filename: str) -> None:
        stem = os.path.splitext(video_filename)[0]
        self._current_csv_path = f"{stem}_timestamps.csv"
        self._timestamp_csv_file = open(self._current_csv_path, "w", newline="")
        self._timestamp_csv_writer = csv.writer(self._timestamp_csv_file)
        self._timestamp_csv_writer.writerow(
            ["frame_id", "timestamp_ns", "timestamp_utc", "delta_ms", "dropped_before",
             "cx", "cy", "zone_state", "event"]
        )
        self._frame_id = 0
        self._csv_prev_ns = None
        self._wall_mono_offset_ns = int((time.time() - time.monotonic()) * 1e9)
        self.facade.add_session_file(self._current_csv_path)

    def _close_timestamp_csv(self) -> None:
        if self._timestamp_csv_file is not None:
            self._timestamp_csv_file.flush()
            os.fsync(self._timestamp_csv_file.fileno())
            self._timestamp_csv_file.close()
            self._timestamp_csv_file = None
            self._timestamp_csv_writer = None
            if self._current_csv_path:
                self.facade.stage_file_for_export(self._current_csv_path)
                self._current_csv_path = None


    # ---------------------------------------------------------------------
    # Per-frame callback: tracking + crossing events + overlays + CSV
    # ---------------------------------------------------------------------

    def _loom_frame_precallback(self, req) -> None:
        try:
            timestamp = self._get_frame_timestamp(req)

            ts_utc = delta_ms = dropped = ts_label = ""
            if timestamp is not None:
                if self.last_frame_timestamp:
                    pass
                self.last_frame_timestamp = timestamp

                dt = datetime.datetime.fromtimestamp(timestamp / 1e9, tz=datetime.timezone.utc)
                ts_utc = dt.strftime("%Y-%m-%d %H:%M:%S.%f") + "+00:00"
                ts_label = (
                    f"{self.facade.get_module_name()} "
                    f"{dt.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}+00:00"
                )

                if self._csv_prev_ns is not None and self.fps:
                    delta_ms = round((timestamp - self._csv_prev_ns) / 1e6, 3)
                    dropped = max(0, round(delta_ms / (1000.0 / float(self.fps))) - 1)
                self._csv_prev_ns = timestamp

            tracking_enabled = bool(self.config.get("loom_tracking.enabled", True))
            overlay_enabled = bool(self.config.get("loom_tracking.overlay.enabled", True))
            overlay_timestamp = bool(self.config.get("camera.overlay_timestamp", True))
            monochrome = self.config.get("camera.monochrome") is True

            cx = cy = None
            zone_state = "out"
            event_label = ""

            with MappedArray(req, "main") as m:
                _rot = getattr(self, "_rotation", 0)
                if _rot:
                    _k = _rot // 90
                    if m.array.shape[0] == m.array.shape[1] or _rot == 180:
                        m.array[:] = np.rot90(m.array, _k)
                if monochrome:
                    self._apply_grayscale(m)
                frame = m.array
                if tracking_enabled and self.tracker is not None:
                    center_proc, (sx, sy), (nx, ny) = self.tracker.detect_center(frame)
                    # Lazy-load ROI + line from JSON (or default full ROI) once we know frame + proc sizes
                    if self.roi_mask_proc is None:
                        self._reload_roi_for_current_geometry(
                            src_w=frame.shape[1],
                            src_h=frame.shape[0],
                            proc_w=nx,
                            proc_h=ny,
                        )

                    # If tracker reports a center: update last_center_src and reset hold counter.
                    if center_proc is not None:
                        cx = float(center_proc[0] * sx)
                        cy = float(center_proc[1] * sy)
                        self.last_center_src = (cx, cy)
                        self._hold_miss_count = 0
                        self._track_valid = True
                    else:
                        # No new detection: HOLD last_center_src.
                        if self.last_center_src is not None:
                            self._hold_miss_count += 1

                            if self._hold_forever_when_still:
                                # Never declare lost due to stillness
                                self._track_valid = True
                            elif self._hold_miss_count <= self._hold_patience_frames:
                                self._track_valid = True
                            else:
                                # Track lost beyond patience
                                self.last_center_src = None
                                self._hold_miss_count = 0
                                self._track_valid = False
                        else:
                            self._track_valid = False

                    next_state = loom_update_crossing_state(
                        crossing_line_src=self.crossing_line_src,
                        center_src=self.last_center_src,
                        prev=self.crossing_state,
                        track_valid=self._track_valid,
                    )

                    self.crossing_state = next_state
                    zone_state = next_state.state
                    event_label = next_state.last_event or ""

                    if event_label:
                        self.communication.send_status({
                            "type": f"loom_{event_label}",  # loom_enter / loom_leave
                            "timestamp_ns": timestamp,
                            "zone_state": zone_state,
                            "cx": cx,
                            "cy": cy,
                        })

                        if self.config.get("loom_stimulus.armed", False):
                            if event_label == "enter":
                                self._loom_stimulus.send("start")
                            elif event_label == "leave":
                                self._loom_stimulus.send("stop")

                if overlay_enabled:
                    self._loom_draw_overlays_on_frame(m)
                if overlay_timestamp and ts_label:
                    self._apply_timestamp_label(m.array, ts_label, "main")

            # Write per-frame CSV with state
            if timestamp is not None and self._timestamp_csv_writer is not None:
                self._timestamp_csv_writer.writerow([
                    self._frame_id, timestamp, ts_utc, delta_ms, dropped,
                    "" if cx is None else f"{cx:.2f}",
                    "" if cy is None else f"{cy:.2f}",
                    zone_state,
                    event_label,
                ])
                self._frame_id += 1

            # Also apply overlays to lores for preview
            # (rotation is applied later in _stream_post_callback on the free-standing
            # make_array copy, which works for non-square images). The timestamp is
            # stamped there too, after rotation, so it lands on the correctly
            # -oriented final frame rather than being baked in at the wrong edge.
            with MappedArray(req, "lores") as m:
                if monochrome:
                    self._apply_grayscale(m)
                if overlay_enabled:
                    self._loom_draw_overlays_on_frame(m, lores=True)
            self._preview_ts_label = ts_label if overlay_timestamp else None

            for msg in self._loom_stimulus.poll_status(max_messages=3):
                if msg.get("type") == "loom_stimulus_error":
                    self.logger.error("Loom stimulus renderer crashed: %s", msg.get("error"))
                self.communication.send_status({"type": "loom_stimulus_status", **msg})
            # Auto-restart the renderer if the subprocess died unexpectedly.
            # Minimum 5 s between restarts to avoid a crash-loop burning CPU.
            proc = self._loom_stimulus._proc
            if proc is not None and not proc.is_alive():
                now = time.monotonic()
                if now - self._stimulus_last_restart >= 5.0:
                    self.logger.warning("Loom stimulus process died (exit %s) — restarting (disarmed)", proc.exitcode)
                    self._stimulus_last_restart = now
                    self._stimulus_armed = False
                    self._loom_stimulus.start()


        except Exception as e:
            self.logger.error(f"Error in _loom_frame_precallback: {e}")


    def _loom_draw_overlays_on_frame(self, m: MappedArray, lores: bool = False) -> None:
        """
        Draw arena ROI, crossing line, centroid, and state text onto a frame.

        Parameters
        ----------
        m : picamera2.MappedArray
            Frame buffer to draw onto.
        lores : bool, default=False
            If True, scales overlays from main size to lores size.
        """
        h, w = m.array.shape[:2]
        if self.width is None or self.height is None:
            return

        sx = w / float(self.width)
        sy = h / float(self.height)

        overlay_cfg = self.config.get("loom_tracking.overlay", {})
        line_bgr = tuple(overlay_cfg.get("line_bgr", [0, 255, 0]))
        roi_bgr = tuple(overlay_cfg.get("roi_bgr", [255, 0, 255]))
        dot_bgr = tuple(overlay_cfg.get("dot_bgr", [0, 0, 255]))
        thickness = int(overlay_cfg.get("thickness", 2))

        # ROI outline
        if self.arena_poly_src is not None and len(self.arena_poly_src) >= 3:
            pts = self.arena_poly_src.copy()
            pts[:, 0] *= sx
            pts[:, 1] *= sy
            pts_i = np.round(pts).astype(np.int32).reshape((-1, 1, 2))
            cv2.polylines(m.array, [pts_i], True, roi_bgr, thickness, cv2.LINE_AA)

        # Line
        if self.crossing_line_src is not None and self.crossing_line_src.get("kind") == "vertical":
            x_line = int(round(float(self.crossing_line_src["x"]) * sx))
            x_line = int(np.clip(x_line, 0, w - 1))
            cv2.line(m.array, (x_line, 0), (x_line, h - 1), line_bgr, thickness, cv2.LINE_AA)

        # Centroid
        if self.last_center_src is not None:
            cx, cy = self.last_center_src
            px = int(round(cx * sx))
            py = int(round(cy * sy))
            cv2.circle(m.array, (px, py), 5, dot_bgr, -1, cv2.LINE_AA)

            # Track square
            draw_square = bool(self.config.get("loom_tracking.draw_track_square", True))
            square_size_src = int(self.config.get("loom_tracking.track_square_size_src", 150))

            if draw_square and self.last_center_src is not None and square_size_src > 0:
                cx_src, cy_src = self.last_center_src

                half = square_size_src / 2.0
                x0 = int(round(cx_src - half))
                y0 = int(round(cy_src - half))
                x1 = int(round(cx_src + half))
                y1 = int(round(cy_src + half))

                # Scale to current stream for drawing (main vs lores)
                x0 = int(round(x0 * sx))
                y0 = int(round(y0 * sy))
                x1 = int(round(x1 * sx))
                y1 = int(round(y1 * sy))

                # Clip to frame
                x0 = int(np.clip(x0, 0, w - 1))
                y0 = int(np.clip(y0, 0, h - 1))
                x1 = int(np.clip(x1, 0, w - 1))
                y1 = int(np.clip(y1, 0, h - 1))

                rect_bgr = tuple(overlay_cfg.get("rect_bgr", [0, 255, 255]))
                cv2.rectangle(m.array, (x0, y0), (x1, y1), rect_bgr, thickness, cv2.LINE_AA)

        # State label — position and size are configurable so the user can
        # tune them across different stream resolutions
        state = self.crossing_state.state
        state_color = (0, 0, 255) if state in ("entering", "in") else (0, 255, 0)
        lx = int(float(overlay_cfg.get("zone_label_x_frac", 0.01)) * w)
        ly = int(float(overlay_cfg.get("zone_label_y_frac", 0.95)) * h)
        lscale = float(overlay_cfg.get("zone_label_font_scale", 1.0))
        cv2.putText(
            m.array,
            f"Zone: {state}",
            (lx, ly),
            cv2.FONT_HERSHEY_SIMPLEX,
            lscale,
            state_color,
            2,
            cv2.LINE_AA,
        )

    def _apply_grayscale(self, m: MappedArray) -> None:
        gray = cv2.cvtColor(m.array, cv2.COLOR_BGR2GRAY)
        m.array[:] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

    # ---------------------------------------------------------------------
    # Timestamp overlay
    # ---------------------------------------------------------------------

    _TIMESTAMP_WIDTH_FRACTIONS = {"small": 0.50, "medium": 0.72, "large": 0.92}

    def _apply_timestamp_label(self, arr, timestamp: str, stream: str) -> None:
        """`arr` must already be in its final (post-rotation) orientation."""
        cache_attr = f"_ts_layout_{stream}"
        layout = getattr(self, cache_attr, None)
        if layout is None:
            h, w = arr.shape[:2]
            font   = cv2.FONT_HERSHEY_SIMPLEX
            preset = self.config.get("camera.text_size", "medium")
            frac   = self._TIMESTAMP_WIDTH_FRACTIONS.get(preset, 0.72)
            thick  = 2 if preset == "large" else 1
            ref_w, _ = cv2.getTextSize(timestamp, font, 1.0, thick)[0]
            scale  = max(0.3, (frac * w) / ref_w)
            tw, th = cv2.getTextSize(timestamp, font, scale, thick)[0]
            x = int((w - tw) / 2)
            y = th + max(4, int(h * 0.01))
            layout = (scale, thick, x, y)
            setattr(self, cache_attr, layout)
        scale, thick, x, y = layout
        cv2.putText(arr, timestamp, (x, y),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (50, 255, 50), thick)

    # ---------------------------------------------------------------------
    # Streaming (identical pattern to APA)
    # ---------------------------------------------------------------------

    def _stream_post_callback(self, req) -> None:
        try:
            now = time.monotonic()
            if now - self._last_stream_encode_time < self._stream_interval_s:
                return
            self._last_stream_encode_time = now
            frame = req.make_array("lores")
            rotation = getattr(self, "_rotation", 0)
            if rotation:
                k = rotation // 90
                # rot90 returns a non-contiguous view; putText below needs a
                # contiguous buffer, so make the copy once here.
                frame = np.ascontiguousarray(np.rot90(frame, k))
                if not getattr(self, "_rotation_logged", False):
                    self.logger.info(
                        f"Preview rotation: {rotation}° applied — "
                        f"input {frame.shape[1]}×{frame.shape[0]} "
                        f"→ output {frame.shape[1]}×{frame.shape[0]}"
                    )
                    self._rotation_logged = True
            else:
                self._rotation_logged = False

            # Timestamp is stamped here, after rotation, so it lands on the
            # correctly-oriented final frame (see comment in _loom_frame_precallback).
            ts_label = getattr(self, "_preview_ts_label", None)
            if ts_label:
                self._apply_timestamp_label(frame, ts_label, "lores")

            ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ret:
                with self.frame_lock:
                    self.latest_frame = jpeg.tobytes()
        except Exception as e:
            self.logger.error(f"Stream encode error: {e}")


    def start_streaming(self, receiver_ip=None, port=None) -> bool:
        if self.is_streaming:
            self.logger.warning("Already streaming")
            return False
        port = 8080
        try:
            if not self.picam2.started:
                self.picam2.start()
                time.sleep(0.1)
            self.should_stop_streaming = False
            self.streaming_server_thread = threading.Thread(
                target=self.run_streaming_server, args=(port,), daemon=True
            )
            self.streaming_server_thread.start()
            self.is_streaming = True
            self.communication.send_status({
                "type": "streaming_started",
                "port": port,
                "status": "success",
                "message": f"Streaming from {self.network.ip}:{port}",
            })
            return True
        except Exception as e:
            self.logger.error(f"Error starting streaming: {e}")
            return False


    def run_streaming_server(self, port: int = 8080) -> None:
        try:
            from werkzeug.serving import make_server
            self.streaming_server = make_server("0.0.0.0", port, self.streaming_app, threaded=True)
            self.streaming_server.serve_forever()
        except Exception as e:
            self.logger.error(f"Streaming server error: {e}")
            self.is_streaming = False
            self.streaming_server = None


    def generate_streaming_frames(self):
        last_frame = None
        while not self.should_stop_streaming:
            with self.frame_lock:
                frame = self.latest_frame
            if frame is None or frame is last_frame:
                time.sleep(0.005)
                continue
            last_frame = frame
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")


    def stop_streaming(self) -> bool:
        if not self.is_streaming:
            return False
        try:
            self.should_stop_streaming = True
            if self.streaming_server:
                self.streaming_server.shutdown()
                self.streaming_server = None
            if self.streaming_server_thread and self.streaming_server_thread.is_alive():
                self.streaming_server_thread.join(timeout=1.0)
            self.is_streaming = False
            self.communication.send_status({"type": "streaming_stopped", "status": "success"})
            return True
        except Exception as e:
            self.logger.error(f"Error stopping stream: {e}")
            return False


    def register_routes(self) -> None:
        @self.streaming_app.route("/")
        def index():
            return "Loom Camera Streaming Server"

        @self.streaming_app.route("/video_feed")
        def video_feed():
            return Response(
                self.generate_streaming_frames(),
                mimetype="multipart/x-mixed-replace; boundary=frame",
            )

        @self.streaming_app.route("/shutdown")
        def shutdown():
            func = request.environ.get("werkzeug.server.shutdown")
            if func is None:
                raise RuntimeError("Not running with the Werkzeug Server")
            func()
            return "Server shutting down..."

        @self.streaming_app.route("/roi", methods=["GET"])
        def roi_get():
            """
            Get current ROI/line JSON from disk.

            Returns
            -------
            json
                ROI payload if present.
            """
            roi_path = self._resolve_roi_json_path()
            if roi_path is None:
                return jsonify({"error": "loom_tracking.roi_json_path is not set"}), 400
            if not roi_path.exists():
                return jsonify({"error": f"ROI JSON not found: {str(roi_path)}"}), 404
            try:
                with open(roi_path, "r") as f:
                    payload = json.load(f)
                return jsonify(payload), 200
            except Exception as e:
                return jsonify({"error": f"Failed reading ROI JSON: {e}"}), 500

        @self.streaming_app.route("/roi", methods=["POST"])
        def roi_post():
            """
            Save ROI/line JSON to disk and hot-reload on next frame.

            Accepts either:
            - New schema: arena_polygon + crossing_line
            - Legacy schema: points + vertical_line (auto-converted)

            Returns
            -------
            json
                Status + resolved path.
            """
            roi_path = self._resolve_roi_json_path()
            if roi_path is None:
                return jsonify({"error": "loom_tracking.roi_json_path is not set"}), 400

            payload = request.get_json(silent=True)
            if payload is None:
                return jsonify({"error": "No JSON payload received"}), 400

            # Accept legacy key names
            if "arena_polygon" not in payload and "points" in payload:
                payload["arena_polygon"] = payload["points"]
            if "crossing_line" not in payload and "vertical_line" in payload:
                payload["crossing_line"] = {
                    "kind": "vertical",
                    "x": payload["vertical_line"].get("x", None),
                    "direction": "left_is_in",
                }

            # Minimal validation
            try:
                poly = payload.get("arena_polygon", None)
                if poly is None or len(poly) < 3:
                    return jsonify({"error": "arena_polygon must contain at least 3 points"}), 400

                cl = payload.get("crossing_line", None)
                if cl is not None:
                    if str(cl.get("kind", "vertical")).lower() != "vertical":
                        return jsonify({"error": "Only crossing_line.kind='vertical' supported"}), 400
                    if cl.get("x", None) is None:
                        return jsonify({"error": "crossing_line.x is required"}), 400
                    direction = str(cl.get("direction", "left_is_in")).lower()
                    if direction not in ("left_is_in", "right_is_in"):
                        return jsonify({"error": "crossing_line.direction must be left_is_in or right_is_in"}), 400
                    cl["direction"] = direction
                    cl["kind"] = "vertical"
                    payload["crossing_line"] = cl

                if "created" not in payload:
                    payload["created"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            except Exception as e:
                return jsonify({"error": f"Invalid payload: {e}"}), 400

            try:
                roi_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = roi_path.with_suffix(roi_path.suffix + ".tmp")
                with open(tmp_path, "w") as f:
                    json.dump(payload, f, indent=2)
                os.replace(tmp_path, roi_path)
            except Exception as e:
                return jsonify({"error": f"Failed to save ROI JSON: {e}"}), 500

            # Hot reload on next pre_callback
            self.roi_mask_proc = None
            self.arena_poly_src = None
            self.crossing_line_src = None

            self.communication.send_status({"type": "loom_roi_updated", "roi_path": str(roi_path)})

            return jsonify({"status": "ok", "roi_path": str(roi_path)}), 200

        @self.streaming_app.route("/roi/snapshot.jpg", methods=["GET"])
        def roi_snapshot():
            """
            Return a single JPEG snapshot from the latest preview frame.

            Notes
            -----
            Uses the same bytes as the MJPEG stream producer. Useful for ROI editor.
            """
            with self.frame_lock:
                jpeg = self.latest_frame
            if jpeg is None:
                return ("No frame available", 503)
            return (jpeg, 200, {"Content-Type": "image/jpeg"})

    # ---------------------------------------------------------------------
    # Sensor modes + health/checks + lifecycle
    # ---------------------------------------------------------------------
    @command()
    def set_loom_roi(self, payload: dict) -> dict:
        """
        Save ROI/line JSON payload sent by the controller UI and hot-reload.

        Parameters
        ----------
        payload : dict
            JSON-like dictionary containing arena_polygon/points and crossing_line/vertical_line.

        Returns
        -------
        result : dict
            Status and resolved path.
        """
        roi_path = self._resolve_roi_json_path()
        if roi_path is None:
            return {"status": "error", "error": "loom_tracking.roi_json_path is not set"}

        if not isinstance(payload, dict):
            return {"status": "error", "error": "payload must be a dict"}

        # Accept legacy key names
        if "arena_polygon" not in payload and "points" in payload:
            payload["arena_polygon"] = payload["points"]
        if "crossing_line" not in payload and "vertical_line" in payload:
            payload["crossing_line"] = {
                "kind": "vertical",
                "x": payload["vertical_line"].get("x", None),
                "direction": "left_is_in",
            }

        # Minimal validation
        poly = payload.get("arena_polygon", None)
        if poly is None or len(poly) < 3:
            return {"status": "error", "error": "arena_polygon must contain at least 3 points"}

        cl = payload.get("crossing_line", None)
        if cl is not None:
            if str(cl.get("kind", "vertical")).lower() != "vertical":
                return {"status": "error", "error": "Only crossing_line.kind='vertical' supported"}
            if cl.get("x", None) is None:
                return {"status": "error", "error": "crossing_line.x is required"}
            direction = str(cl.get("direction", "left_is_in")).lower()
            if direction not in ("left_is_in", "right_is_in"):
                return {"status": "error", "error": "crossing_line.direction must be left_is_in or right_is_in"}
            cl["direction"] = direction
            cl["kind"] = "vertical"
            payload["crossing_line"] = cl

        if "created" not in payload:
            payload["created"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        try:
            roi_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = roi_path.with_suffix(roi_path.suffix + ".tmp")
            with open(tmp_path, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, roi_path)
        except Exception as e:
            return {"status": "error", "error": f"Failed to save ROI JSON: {e}"}

        # Hot reload
        self.roi_mask_proc = None
        self.arena_poly_src = None
        self.crossing_line_src = None

        self.communication.send_status({"type": "loom_roi_updated", "roi_path": str(roi_path)})
        return {"status": "ok", "roi_path": str(roi_path)}

    @command()
    def get_sensor_modes(self):
        if not self.sensor_modes:
            return {"sensor_modes": []}
        max_area = max(m["crop_limits"][2] * m["crop_limits"][3] for m in self.sensor_modes)
        enriched = []
        for i, mode in enumerate(self.sensor_modes):
            crop = mode["crop_limits"]
            pct = round(100 * crop[2] * crop[3] / max_area)
            fov = "Full FoV" if pct >= 100 else f"Partial FoV ({pct}%)"
            w, h = mode["size"]
            fps = mode["fps"]
            enriched.append({
                "index": i,
                "size": [w, h],
                "fps": round(fps, 1),
                "bit_depth": mode["bit_depth"],
                "crop_limits": list(crop),
                "format": str(mode["format"]),
                "label": f"Mode {i}: {w}×{h} @ {fps:.0f}fps — {fov}",
            })
        return {
            "sensor_modes": enriched,
            "sensor_model": self.sensor_model,
            "has_autofocus": self.has_autofocus,
        }


    @check()
    def _check_picam(self) -> tuple:
        if not self.picam2:
            return False, "No picam2 object"
        return True, "picam2 initialised"

    @command()
    def loom_stimulus_start(self) -> dict:
        """Manually fire the loom stimulus immediately."""
        self._loom_stimulus.send("start")
        self.logger.info("loom_stimulus: manual start")
        return {"status": "started"}

    @command()
    def loom_stimulus_stop(self) -> dict:
        """Manually abort any in-progress loom stimulus."""
        self._loom_stimulus.send("abort")
        self.logger.info("loom_stimulus: manual stop")
        return {"status": "stopped"}

    @command()
    def loom_stimulus_test_near_screen(self, duration_s: float = 2.0) -> dict:
        """Flash the near TV briefly so the user can confirm GL reaches it."""
        self._loom_stimulus._cmd_q.put(("test_near_screen", {"duration_s": duration_s}))
        self.logger.info("loom_stimulus: near screen test (duration=%.1fs)", duration_s)
        return {"status": "test_started", "duration_s": duration_s}

    def start(self) -> bool:
        try:
            if not super().start():
                return False
            self.start_streaming()
            return True
        except Exception as e:
            self.logger.error(f"Error starting module: {e}")
            return False


    def stop(self) -> bool:
        try:
            if self.is_streaming:
                self.stop_streaming()
            if self.tracker is not None:
                self.tracker.reset()
            if hasattr(self, "_loom_stimulus") and self._loom_stimulus is not None:
                self._loom_stimulus.shutdown()

            return super().stop()
        except Exception as e:
            self.logger.error(f"Error stopping module: {e}")
            return False

def main():
    camera = LoomCameraModule()
    camera.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        camera.stop()


if __name__ == "__main__":
    main()
