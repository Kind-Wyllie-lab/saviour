#!/usr/bin/env python3
"""
SAVIOUR System - APA Camera Module

Top-mounted camera module for the Active Place Avoidance (APA) test rig.
Built on CameraBase (src/modules/camera_base.py), which provides Picamera2
lifecycle, MJPEG streaming, segmented recording, and the timestamp-CSV
sidecar. This file adds APA-specific overlays (circular mask, shock zone)
and Hailo-accelerated (or blob-diff fallback) rat detection.

Inference runs on a Hailo-8L AI accelerator (Raspberry Pi AI Kit).
No PyTorch / CUDA required.  To use a custom model, export ratnet.pt → ONNX
→ compile to HEF with the Hailo DFC, then set object_detection.model_path.

Author: Andrew SG
"""

import contextlib
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.request
from collections import deque
from typing import NamedTuple

import cv2
import numpy as np
from picamera2 import MappedArray

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.camera_base import CameraBase

# Detection + the on-chip-NMS picamera2 wrapper are shared with hailo_camera.
from modules.hailo_infer import MODEL_DIR, Detection, HailoDetector
from modules.module import check, command

# HEFs uploaded from the web UI (fetch_hef). Outside the source tree so a
# code update never touches them; MODEL_DIR holds the bundled/zoo HEFs.
USER_HEF_DIR = "/var/lib/saviour/hailo_models"
HAILO_DEVICE = "/dev/hailo0"
_HEF_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.hef$")
MAX_HEF_BYTES = 256 * 1024 * 1024

__all__ = ["Detection", "HailoDetector"]  # re-exported for existing importers


class TrackSnapshot(NamedTuple):
    """One tracking result, published by the inference worker and read
    without a lock by the capture and preview threads (a single attribute
    assignment swaps it)."""
    seq: int                       # increments per result
    det_cx: int | None             # tracked position, main-stream px; None = no track
    det_cy: int | None
    in_zone: bool
    conf: float | None             # detection accepted on the source frame
    det: Detection | None          # for the overlay's label
    frame_id: int                  # CSV frame_id of the frame it was computed on


# ---------------------------------------------------------------------------
# Hailo raw inference backend (hailo_platform direct API)
# ---------------------------------------------------------------------------

class HailoRawDetector:
    """
    Uses hailo_platform directly for models that output raw YOLOv8 tensors
    (DFL-encoded box distributions + un-postprocessed scores) rather than the
    post-processed hailo-all detections HailoDetector above expects.

    Ported from the refactor/ai_apa branch (2026-08-18), where it backed a
    custom rats_yolov8n.hef compiled without Hailo's own NMS postprocessing
    step baked in. This repo's own tools/convert_to_hailo.py deliberately
    does bake NMS in (see docs/readthedocs/hailo_model_deployment.md), so a
    model produced by that pipeline should use "hailo", not "hailo_raw" — this
    backend exists for a .hef obtained/compiled some other way that only
    outputs raw tensors.

    Keeps InferVStreams and network_group.activate() contexts open for the
    object lifetime via an ExitStack; call close() to release the device.
    """

    INPUT_SIZE = 416

    def __init__(self, hef_path: str, threshold: float = 0.4, iou_thresh: float = 0.45):
        import hailo_platform as hpf
        self._threshold  = threshold
        self._iou_thresh = iou_thresh

        self._target = hpf.VDevice()
        hef = hpf.HEF(hef_path)
        cfg_params = hpf.ConfigureParams.create_from_hef(
            hef, interface=hpf.HailoStreamInterface.PCIe
        )
        network_groups           = self._target.configure(hef, cfg_params)
        self._network_group      = network_groups[0]
        self._ng_params          = self._network_group.create_params()
        self._input_vstream_info = hef.get_input_vstream_infos()[0]

        in_params  = hpf.InputVStreamParams.make_from_network_group(
            self._network_group, quantized=False, format_type=hpf.FormatType.FLOAT32
        )
        out_params = hpf.OutputVStreamParams.make_from_network_group(
            self._network_group, quantized=False, format_type=hpf.FormatType.FLOAT32
        )

        self._stack    = contextlib.ExitStack()
        self._pipeline = self._stack.enter_context(
            hpf.InferVStreams(self._network_group, in_params, out_params)
        )
        self._stack.enter_context(self._network_group.activate(self._ng_params))

        self._anchors = self._build_anchors()
        self.last_max_score: float = 0.0

    @staticmethod
    def _build_anchors() -> np.ndarray:
        anchors = []
        for stride in [8, 16, 32]:
            gs = HailoRawDetector.INPUT_SIZE // stride
            for gy in range(gs):
                for gx in range(gs):
                    anchors.append((gx + 0.5, gy + 0.5, stride))
        return np.array(anchors, dtype=np.float32)

    def detect(self, frame: np.ndarray, labels: list[str]) -> list[Detection]:
        orig_h, orig_w = frame.shape[:2]
        rgb = cv2.cvtColor(
            cv2.resize(frame, (self.INPUT_SIZE, self.INPUT_SIZE)),
            cv2.COLOR_BGR2RGB,
        )
        inp = np.expand_dims(rgb.astype(np.float32) / 255.0, axis=0)
        raw = self._pipeline.infer({self._input_vstream_info.name: inp})
        dets = []
        for x1, y1, x2, y2, conf in self._decode(list(raw.values()), orig_w, orig_h):
            dets.append(Detection(category=0, conf=conf, box=(x1, y1, x2 - x1, y2 - y1)))
        return dets

    def _decode(self, outputs, orig_w: int, orig_h: int):
        boxes_raw = scores_raw = None
        for out in outputs:
            out = np.squeeze(out)
            if out.ndim == 2 and out.shape[1] == 64:
                boxes_raw = out
            elif out.ndim == 2 and out.shape[1] == 1:
                scores_raw = out[:, 0]
            elif out.ndim == 1:
                scores_raw = out
        if boxes_raw is None or scores_raw is None:
            return []

        self.last_max_score = float(scores_raw.max())
        keep = np.where(scores_raw >= self._threshold)[0]
        if len(keep) == 0:
            return []

        boxes, confs = [], []
        for idx in keep:
            score = float(scores_raw[idx])
            ax, ay, stride = self._anchors[idx]
            l, t, r, b = self._dfl(boxes_raw[idx])
            s = self.INPUT_SIZE
            x1 = int(max(0,      (ax - l) * stride / s * orig_w))
            y1 = int(max(0,      (ay - t) * stride / s * orig_h))
            x2 = int(min(orig_w, (ax + r) * stride / s * orig_w))
            y2 = int(min(orig_h, (ay + b) * stride / s * orig_h))
            boxes.append([x1, y1, x2, y2])
            confs.append(score)

        if not boxes:
            return []
        nms = cv2.dnn.NMSBoxes(boxes, confs, self._threshold, self._iou_thresh)
        results = []
        for i in nms:
            idx = i[0] if isinstance(i, (list, np.ndarray)) else i
            results.append(boxes[idx] + [confs[idx]])
        return results

    @staticmethod
    def _dfl(box: np.ndarray):
        edges = []
        for i in range(4):
            b = box[i * 16:(i + 1) * 16].astype(np.float32)
            b -= b.max()
            e = np.exp(b)
            edges.append(float(np.dot(e / e.sum(), np.arange(16))))
        return edges

    def close(self) -> None:
        self._stack.close()


# ---------------------------------------------------------------------------
# Blob-differencing tracker
# ---------------------------------------------------------------------------

class BlobTracker:
    """
    Frame-differencing blob tracker — no neural network required.

    Computes the absolute pixel difference between consecutive frames, applies
    morphological bridge-filling to merge fragmented blobs, then finds the
    largest connected component above a minimum area.  Centroid position is
    exponentially smoothed and held for `patience_frames` after the blob is
    lost.

    Compatible with the HailoDetector.detect() interface so the calling code
    in the module callback is unchanged.  The returned Detection always has
    conf=1.0 and a square bounding box centred on the smoothed centroid.

    The circular arena mask is already applied to the frame before detect() is
    called, so the blacked-out region outside the arena contributes zero diff
    and acts as a natural ROI — no separate ROI mask is needed.
    """

    def __init__(self,
                 process_width: int = 256,
                 thr_hi: float = 5.0,
                 gap_h_px: int = 15,
                 gap_v_px: int = 15,
                 close_px: int = 7,
                 open_px: int = 5,
                 min_area_px: int = 50,
                 patience_frames: int = 10,
                 smoothing_alpha: float = 0.3,
                 track_square_size: int = 150):
        self._process_width   = process_width
        self._thr_hi          = thr_hi
        self._min_area        = min_area_px
        self._patience        = patience_frames
        self._alpha           = smoothing_alpha
        self._square          = track_square_size

        def _rect(w, h): return cv2.getStructuringElement(cv2.MORPH_RECT, (w, h))
        def _ellipse(s): return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (s, s))

        self._kern_h     = _rect(max(1, gap_h_px), 1)     if gap_h_px  > 1 else None
        self._kern_v     = _rect(1, max(1, gap_v_px))     if gap_v_px  > 1 else None
        self._kern_close = _ellipse(max(1, close_px))     if close_px  > 1 else None
        self._kern_open  = _ellipse(max(1, open_px))      if open_px   > 1 else None

        self._prev_gray:    np.ndarray | None          = None
        self._last_center:  tuple | None               = None
        self._miss_count:   int                           = 0

    def reset(self) -> None:
        """Clear temporal state.  Call between recording sessions."""
        self._prev_gray   = None
        self._last_center = None
        self._miss_count  = 0

    def detect(self, frame: np.ndarray, labels: list) -> list[Detection]:
        """
        Process one BGR (or grayscale) frame.
        Returns a one-element list with the rat's Detection, or [] if not found.
        """
        orig_h, orig_w = frame.shape[:2]

        # Downsample for speed, convert to grayscale
        proc_w = min(self._process_width, orig_w)
        proc_h = max(1, int(round(proc_w * orig_h / orig_w)))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        proc = cv2.resize(gray, (proc_w, proc_h), interpolation=cv2.INTER_AREA)

        if self._prev_gray is None or self._prev_gray.shape != proc.shape:
            self._prev_gray = proc
            return []

        # Absolute frame difference → threshold
        diff = np.abs(proc.astype(np.float32) - self._prev_gray.astype(np.float32))
        self._prev_gray = proc
        mask = (diff >= self._thr_hi).astype(np.uint8)

        # Bridge-fill horizontal and vertical gaps, then morphological close/open
        for kern, op in (
            (self._kern_h,     cv2.MORPH_CLOSE),
            (self._kern_v,     cv2.MORPH_CLOSE),
            (self._kern_close, cv2.MORPH_CLOSE),
            (self._kern_open,  cv2.MORPH_OPEN),
        ):
            if kern is not None:
                mask = cv2.morphologyEx(mask, op, kern)

        # Largest connected component above area threshold
        n_labels, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        best_label, best_area = 0, 0
        for lab in range(1, n_labels):
            area = int(stats[lab, cv2.CC_STAT_AREA])
            if area >= self._min_area and area > best_area:
                best_area = area
                best_label = lab

        scale_x = orig_w / proc_w
        scale_y = orig_h / proc_h

        if best_label > 0:
            cx = float(centroids[best_label][0]) * scale_x
            cy = float(centroids[best_label][1]) * scale_y
            if self._last_center is not None and self._alpha > 0:
                cx = (1 - self._alpha) * self._last_center[0] + self._alpha * cx
                cy = (1 - self._alpha) * self._last_center[1] + self._alpha * cy
            self._last_center = (cx, cy)
            self._miss_count  = 0
        else:
            if self._last_center is not None:
                self._miss_count += 1
                if self._miss_count > self._patience:
                    self._last_center = None
                    self._miss_count  = 0
            return []

        cx, cy  = self._last_center
        half    = self._square / 2.0
        box     = (int(cx - half), int(cy - half), self._square, self._square)
        return [Detection(category=0, conf=1.0, box=box)]

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Module
# ---------------------------------------------------------------------------

class APACameraModule(CameraBase):
    CONFIG_FILENAME = "apa_camera_config.json"
    # det_cx/det_cy: tracked (filtered) centroid, main-stream px; det_conf:
    # confidence of the detection accepted on this frame, blank when the
    # position is held over from an earlier frame (within lost_timeout_s).
    # det_frame_id: the frame those values were computed on. Tracking runs on
    # its own thread at whatever rate inference allows, so a row repeats the
    # latest result until a new one lands; det_conf is only on the first row
    # after each new result.
    CSV_EXTRA_COLUMNS = ["det_cx", "det_cy", "in_zone", "det_conf", "det_frame_id"]
    _DEFAULT_BITRATE_MB = 2

    def __init__(self, module_type="apa_camera"):
        super().__init__(module_type)

        # ── APA: inference worker ───────────────────────────────────────────
        # Detection used to run inside the capture callback, on every frame:
        # with the rat HEF on a Hailo-8L at 1000x1000 / 25 fps that callback
        # sat at ~79% of a core and ~13% of frames were dropped (2026-11-10,
        # plans/apa-camera-async-detection.md). Now the callback only hands
        # the newest frame to this worker (one-slot, drop-oldest queue) and
        # reads the latest TrackSnapshot back.
        # _det_lock is held around detect() and around any detector swap or
        # close: closing HailoRT under an in-flight run() is a hard SIGABRT.
        self._det_lock = threading.Lock()
        self._infer_q: queue.Queue = queue.Queue(maxsize=1)
        self._infer_stop = threading.Event()
        self._infer_worker: threading.Thread | None = None
        self._infer_every_n = 1
        self._infer_counter = 0
        self._track_snapshot: TrackSnapshot | None = None
        self._snapshot_seq = 0
        self._csv_snapshot_seq = -1          # last snapshot whose conf went into the CSV
        self._infer_latency_s: deque = deque(maxlen=250)
        self._infer_done_t: deque = deque(maxlen=250)
        self._infer_handed = 0
        self._infer_stats_t = time.monotonic()

        # ── APA: detection state ────────────────────────────────────────────
        self.detector: HailoDetector | None = None
        self._detector_backend: str | None = None
        # Set when object detection is enabled in config but the Hailo
        # detector failed to initialise (no AI HAT / driver / HEF). Unlike
        # hailo_camera -- which is meant to fall back to a plain camera --
        # the APA rig's shock-zone logic is the point, so a missing
        # accelerator is a genuine fault. Folded into self.hardware_fault
        # (CameraBase's camera-sensor fault takes precedence).
        self._inference_fault: str | None = None
        self._labels: list[str] = ["rat"]
        self.threshold = 0.55
        # ── APA: position tracking (one animal per trial) ───────────────────
        # _update_track() turns each frame's detections into one smoothed
        # position: the zone test, CSV columns and overlay all use it.
        self._track_pos: tuple[float, float] | None = None
        self._track_det: Detection | None = None     # for the overlay's label/conf
        self._track_seen_t = 0.0                     # monotonic time of last accepted detection
        self._jump_cand: tuple[float, float, int] | None = None  # far detection: (cx, cy, frames seen)
        self._frame_conf: float | None = None        # detection accepted on the current frame
        self.smoothing = 0.5
        self.max_jump_frac = 0.15
        self.jump_confirm_frames = 3
        self.lost_timeout_s = 0.5
        self._prev_in_zone: bool = False
        self._last_zone_state_t = 0.0

        # (mask/shock-zone geometry is computed per-frame from the current array shape)
        # Each overlay has independent *_display (livestream) and *_record
        # (burned into the recorded main stream) switches.
        self.mask_radius = None
        self.mask_center_x_offset = 0
        self.mask_center_y_offset = 0
        self.mask_display = True
        self.mask_record = True
        self._mask_cache: tuple | None = None   # (key, uint8 mask image)
        self.shock_zone_display = True
        self.shock_zone_record = False
        self.track_display = True
        self.track_record = False
        self._detector_sig: tuple | None = None
        self.shock_zone_angle_span = 0
        self.shock_zone_start_angle = 0
        self.shock_zone_inner_offset = 0
        self.shock_zone_color = (0, 0, 255)
        self.shock_zone_thickness = 2

        # ── Apply APA config ─────────────────────────────────────────────────
        self._configure_mask_and_shock_zone()
        self._configure_object_detection()
        self._start_inference_worker()


    # -----------------------------------------------------------------------
    # Config change handler
    # -----------------------------------------------------------------------

    def _configure_module_extra(self, updated_keys) -> None:
        self._configure_mask_and_shock_zone()
        self._configure_object_detection()


    # -----------------------------------------------------------------------
    # Object detection config
    # -----------------------------------------------------------------------

    def _sync_hardware_fault(self) -> None:
        """Fold the inference-accelerator state into self.hardware_fault,
        without masking a camera-sensor fault (CameraBase owns that and only
        sets hardware_fault when picam2 is None)."""
        if self.picam2 is not None:
            self.hardware_fault = self._inference_fault

    def _configure_object_detection(self):
        self._labels = self.config.get("object_detection.labels", ["rat"])
        self.threshold = self.config.get("object_detection.threshold", 0.55)
        self.smoothing = min(0.95, max(0.0, float(self.config.get("object_detection.smoothing", 0.5))))
        self.max_jump_frac = float(self.config.get("object_detection.max_jump_frac", 0.15))
        self.jump_confirm_frames = max(1, int(self.config.get("object_detection.jump_confirm_frames", 3)))
        self.lost_timeout_s = float(self.config.get("object_detection.lost_timeout_s", 0.5))
        self._infer_every_n = max(1, int(self.config.get("object_detection.infer_every_n", 1)))
        with self._det_lock:
            self._configure_detector_locked()

    def _configure_detector_locked(self):
        """Build, swap or close the detector. Caller holds _det_lock, so the
        inference worker is never inside detect() while this runs."""
        if not self.config.get("object_detection.enabled", False):
            if self.detector is not None:
                self.detector.close()
                self.detector = None
                self._detector_backend = None
            self._detector_sig = None
            self._track_snapshot = None   # don't leave a stale position on screen
            self._inference_fault = None  # nothing expected
            self._sync_hardware_fault()
            return

        new_backend = self.config.get("object_detection.backend", "hailo")
        model_path = self.config.get(
            "object_detection.model_path",
            "/usr/local/src/saviour/hailo_models/apa_rats_yolov8_nms.hef"
        )
        # Rebuild whenever anything the detector is constructed from changes
        # (not just the backend): a new HEF, blob-tracker tuning, or the
        # raw backend's threshold, which it only takes at init.
        if new_backend == "blob":
            sig = (new_backend, json.dumps(self.config.get("blob_tracker", {}), sort_keys=True))
        elif new_backend == "hailo_raw":
            sig = (new_backend, model_path, self.threshold)
        else:
            sig = (new_backend, model_path)
        if self.detector is not None and self._detector_sig == sig:
            if hasattr(self.detector, "set_threshold"):
                self.detector.set_threshold(self.threshold)
            return  # same detector already running

        if self.detector is not None:
            self.detector.close()
            self.detector = None

        self._detector_backend = new_backend
        self._detector_sig = sig

        if new_backend == "blob":
            bt = self.config.get("blob_tracker", {})
            self.detector = BlobTracker(
                process_width     = bt.get("process_width",     256),
                thr_hi            = bt.get("thr_hi",            5.0),
                gap_h_px          = bt.get("gap_h_px",          15),
                gap_v_px          = bt.get("gap_v_px",          15),
                close_px          = bt.get("close_px",          7),
                open_px           = bt.get("open_px",           5),
                min_area_px       = bt.get("min_area_px",       50),
                patience_frames   = bt.get("patience_frames",   10),
                smoothing_alpha   = bt.get("smoothing_alpha",   0.3),
                track_square_size = bt.get("track_square_size", 150),
            )
            self.logger.info("Blob tracker detector ready")
            self._inference_fault = None  # CPU tracker, no accelerator needed
            self._sync_hardware_fault()
            return

        try:
            if new_backend == "hailo_raw":
                self.detector = HailoRawDetector(model_path, threshold=self.threshold)
                self.logger.info(f"Hailo raw detector ready: {model_path}")
            else:
                self.detector = HailoDetector(model_path, threshold=self.threshold)
                self.logger.info(f"Hailo detector ready: {model_path}")
            self._inference_fault = None
        except Exception as e:
            self.logger.error(f"Failed to initialise Hailo detector: {e}")
            self.detector = None
            self._detector_backend = None
            self._detector_sig = None
            self._inference_fault = (
                f"Hailo AI HAT unavailable for object detection: {e}"
            )
        self._sync_hardware_fault()


    @check()
    def _check_object_detector(self) -> tuple[bool, str]:
        """Blocks readiness only when object detection is configured for a
        Hailo backend that failed to come up -- a `blob` (CPU) backend or
        detection-disabled leaves _inference_fault None and passes."""
        if self._inference_fault:
            return False, self._inference_fault
        return True, "Object detector OK"


    # -----------------------------------------------------------------------
    # Recording — reset detector state on each new session
    # -----------------------------------------------------------------------

    def _start_new_recording(self) -> bool:
        result = super()._start_new_recording()
        with self._det_lock:
            if hasattr(self.detector, 'reset'):
                self.detector.reset()
        # frame_id restarts at 0 with the new segment; a snapshot from before
        # would carry an id from the old numbering into the new CSV.
        self._track_snapshot = None
        return result


    # -----------------------------------------------------------------------
    # Per-frame hooks
    # -----------------------------------------------------------------------

    def _process_main_frame(self, m: MappedArray, timing) -> dict:
        """Capture thread: no inference here (see the worker in __init__).
        Hand the frame over, then overlay and fill the CSV from the latest
        published TrackSnapshot."""
        if self.mask_record:
            self._apply_mask(m)

        tracking = self.config.get("object_detection.enabled", False)
        snap = None
        if tracking:
            self._infer_counter += 1
            if self._infer_counter % self._infer_every_n == 0:
                self._hand_to_worker(m.array, timing.timestamp_ns, self._frame_id)
            snap = self._track_snapshot

        # Recorded overlays go on after the hand-off so they never feed detection.
        if self.shock_zone_record:
            self._apply_shock_zone(m)
        if tracking and self.track_record:
            self._draw_detections(m)

        if snap is None:
            return {"det_cx": "", "det_cy": "", "in_zone": "", "det_conf": "",
                    "det_frame_id": ""}
        conf = ""
        if snap.seq != self._csv_snapshot_seq:
            self._csv_snapshot_seq = snap.seq
            if snap.conf is not None:
                conf = round(snap.conf, 3)
        tracked = snap.det_cx is not None
        return {
            "det_cx": snap.det_cx if tracked else "",
            "det_cy": snap.det_cy if tracked else "",
            "in_zone": int(snap.in_zone) if tracked else "",
            "det_conf": conf,
            "det_frame_id": snap.frame_id,
        }

    def _hand_to_worker(self, frame: np.ndarray, timestamp_ns: int, frame_id: int) -> None:
        """Copy the frame (tracking only looks inside the arena mask, whether
        or not the mask is burned into the recording) and put it in the
        one-slot queue, replacing any frame the worker hasn't picked up."""
        work = frame.copy() if self.mask_record else self._masked_copy(frame)
        if work is frame:
            work = frame.copy()
        try:
            self._infer_q.get_nowait()
        except queue.Empty:
            pass
        try:
            self._infer_q.put_nowait((work, timestamp_ns, frame_id))
            self._infer_handed += 1
        except queue.Full:
            pass

    def _track_frame(self, frame: np.ndarray, timestamp_ns: int, frame_id: int) -> None:
        """Worker thread: detect, update the track, test the zone, send the
        zone events, publish a TrackSnapshot. Event timestamps are those of
        the frame that was analysed, not of when the result came out."""
        dets = self._detect_objects(frame)
        self._update_track(dets, time.monotonic())
        det_cx = det_cy = None
        in_zone = False
        if self._track_pos is not None:
            det_cx, det_cy = (int(v) for v in self._track_pos)
            in_zone = bool(self._is_in_shock_zone(det_cx, det_cy))

        # Losing the animal counts as leaving the zone, so a stale
        # "entered" can't stand while nothing is tracked.
        if in_zone != self._prev_in_zone:
            event = {
                "type": "zone_entered" if in_zone else "zone_exited",
                "timestamp_ns": timestamp_ns,
            }
            if det_cx is None:
                event["reason"] = "lost"
            self.communication.send_status(event)
            self._prev_in_zone = in_zone

        # Periodic report on top of the edges: the controller holds an
        # automated shock on only while these keep arriving, so a camera
        # that crashes mid-zone can't leave it running.
        now = time.monotonic()
        if now - self._last_zone_state_t >= self._ZONE_STATE_PERIOD_S:
            self._last_zone_state_t = now
            self.communication.send_status({
                "type": "zone_state",
                "in_zone": in_zone,
                "tracking": det_cx is not None,
                "det_cx": det_cx,
                "det_cy": det_cy,
                "det_conf": round(float(self._track_det.conf), 3)
                            if self._track_det is not None else None,
                "infer_hz": self._infer_rate_hz(now),
                "timestamp_ns": timestamp_ns,
            })

        self._snapshot_seq += 1
        self._track_snapshot = TrackSnapshot(
            seq=self._snapshot_seq, det_cx=det_cx, det_cy=det_cy, in_zone=in_zone,
            conf=float(self._frame_conf) if self._frame_conf is not None else None,
            det=self._track_det if det_cx is not None else None,
            frame_id=frame_id,
        )

    # -----------------------------------------------------------------------
    # Inference worker
    # -----------------------------------------------------------------------

    _INFER_STATS_PERIOD_S = 60.0

    def _start_inference_worker(self) -> None:
        if self._infer_worker and self._infer_worker.is_alive():
            return
        self._infer_stop.clear()
        self._infer_worker = threading.Thread(
            target=self._inference_worker, daemon=True, name="apa-infer")
        self._infer_worker.start()

    def _stop_inference_worker(self, timeout: float = 3.0) -> bool:
        """Signal and join the worker. Must return True before the detector is
        closed at module stop. False if it didn't stop in time."""
        self._infer_stop.set()
        worker = self._infer_worker
        if worker is None:
            return True
        worker.join(timeout)
        if worker.is_alive():
            return False
        self._infer_worker = None
        return True

    def _inference_worker(self) -> None:
        self.logger.info("APA inference worker started")
        while not self._infer_stop.is_set():
            try:
                frame, timestamp_ns, frame_id = self._infer_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if not self.config.get("object_detection.enabled", False):
                continue
            t0 = time.monotonic()
            try:
                self._track_frame(frame, timestamp_ns, frame_id)
            except Exception as e:
                self.logger.error(f"APA tracking error: {e}")
            t1 = time.monotonic()
            self._infer_latency_s.append(t1 - t0)
            self._infer_done_t.append(t1)
            self._maybe_log_infer_stats(t1)
        self.logger.info("APA inference worker stopped")

    def _infer_rate_hz(self, now: float, window_s: float = 5.0) -> float:
        recent = [t for t in self._infer_done_t if now - t <= window_s]
        if len(recent) < 2:
            return 0.0
        return round((len(recent) - 1) / max(1e-6, recent[-1] - recent[0]), 1)

    def _maybe_log_infer_stats(self, now: float) -> None:
        if now - self._infer_stats_t < self._INFER_STATS_PERIOD_S:
            return
        self._infer_stats_t = now
        lat = sorted(self._infer_latency_s)
        if not lat:
            return
        p50 = lat[len(lat) // 2] * 1000
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] * 1000
        self.logger.info(
            f"APA tracking: {self._infer_rate_hz(now)} Hz, "
            f"track step p50 {p50:.0f} ms / p95 {p95:.0f} ms, "
            f"{self._infer_handed} frames handed over since last report")
        self._infer_handed = 0

    def _process_lores_frame(self, m: MappedArray, timing) -> None:
        if self.mask_display:
            self._apply_mask(m)
        if self.shock_zone_display:
            self._apply_shock_zone(m)
        if self.track_display and self.config.get("object_detection.enabled", False):
            self._draw_detections(m)

    def _process_main_preview_frame(self, m, timing) -> None:
        """High-quality livestream: a copy of the recorded frame, so it already
        carries every *_record overlay. Add only the display-only ones (a
        recorded overlay can't be taken back off the preview)."""
        if self.mask_display and not self.mask_record:
            self._apply_mask(m)
        if self.shock_zone_display and not self.shock_zone_record:
            self._apply_shock_zone(m)
        if (self.track_display and not self.track_record
                and self.config.get("object_detection.enabled", False)):
            self._draw_detections(m)

    def _apply_framerate(self, arr, framerate: str, stream: str = "main") -> None:
        """Two-line preview overlay: actual fps + the configured stream cap.

        Overrides CameraBase's single-line version — same call signature, so
        the shared _stream_post_callback's self._apply_framerate(...) call
        picks this up automatically via inheritance.
        """
        stream_fps = self._STREAM_FPS if self._stream_interval_s > 0 else None
        height, width = arr.shape[:2]
        font   = cv2.FONT_HERSHEY_SIMPLEX
        scale  = max(0.2, height * 0.02 / 18)
        margin = max(4, int(height * 0.01))
        color  = (50, 255, 50)

        lines = [f"{framerate}fps rec", f"~{stream_fps}fps stream"] if stream_fps is not None else [f"{framerate}fps"]

        _, th = cv2.getTextSize(lines[0], font, scale, 1)[0]
        line_h = th + margin
        y = height - margin
        for line in reversed(lines):
            tw, _ = cv2.getTextSize(line, font, scale, 1)[0]
            x = int((width - tw) / 2)
            cv2.putText(arr, line, (x, y), font, scale, color, 1)
            y -= line_h


    # -----------------------------------------------------------------------
    # Object detection
    # -----------------------------------------------------------------------

    # Candidates considered per frame: the track takes the nearest one, so a
    # brighter false positive elsewhere doesn't steal it.
    _MAX_CANDIDATES = 5
    # zone_state report rate while tracking (the controller's auto shocking
    # treats a report older than apa.auto_shock.zone_state_timeout_s as stale).
    _ZONE_STATE_PERIOD_S = 0.2

    def _detect_objects(self, frame: np.ndarray) -> list[Detection]:
        """Detections on this frame, most confident first (at most
        _MAX_CANDIDATES); [] when there's no detector or inference fails."""
        if self.detector is None:
            return []
        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)
        if frame.ndim != 3 or frame.shape[2] != 3:
            return []
        with self._det_lock:
            detector = self.detector
            if detector is None:
                return []
            try:
                return detector.detect(frame, self._labels)[: self._MAX_CANDIDATES]
            except Exception as e:
                self.logger.error(f"Hailo inference error: {e}")
                return []

    @staticmethod
    def _centre(det: Detection) -> tuple[float, float]:
        x, y, w, h = det.box
        return x + w / 2, y + h / 2

    def _update_track(self, dets: list[Detection], now: float) -> None:
        """Fold this frame's detections into the single tracked position.

        - With a track: take the nearest detection within the jump gate
          (max_jump_frac × the frame's short side) and smooth towards it
          (position = smoothing × old + (1 − smoothing) × new).
        - Starting a track, or jumping outside the gate, needs the most
          confident detection to be seen near the same place
          jump_confirm_frames frames running: a one-frame false positive is
          ignored, while a real relocation (or the animal reappearing) is
          picked up.
        - No accepted detection for lost_timeout_s clears the track, so the
          position, CSV columns and zone state don't freeze on the last
          sighting.
        """
        self._frame_conf = None
        if self._track_pos is not None and now - self._track_seen_t > self.lost_timeout_s:
            self._track_pos = self._track_det = None
            self._jump_cand = None
        if not dets:
            return

        gate = self.max_jump_frac * min(self.width or 1, self.height or 1)
        if self._track_pos is not None:
            tx, ty = self._track_pos
            nearest = min(dets, key=lambda d: np.hypot(self._centre(d)[0] - tx,
                                                       self._centre(d)[1] - ty))
            nx, ny = self._centre(nearest)
            if np.hypot(nx - tx, ny - ty) <= gate:
                self._jump_cand = None
                self._accept(nearest, now, reset=False)
                return

        # No track, or every candidate is a jump: count the top one towards
        # (re)acquiring.
        cx, cy = self._centre(dets[0])
        if self._jump_cand is not None and np.hypot(cx - self._jump_cand[0],
                                                    cy - self._jump_cand[1]) <= gate:
            count = self._jump_cand[2] + 1
        else:
            count = 1
        self._jump_cand = (cx, cy, count)
        if count >= self.jump_confirm_frames:
            self._jump_cand = None
            self._accept(dets[0], now, reset=True)

    def _accept(self, det: Detection, now: float, reset: bool) -> None:
        cx, cy = self._centre(det)
        if reset or self._track_pos is None:
            self._track_pos = (cx, cy)
        else:
            a = self.smoothing
            px, py = self._track_pos
            self._track_pos = (a * px + (1 - a) * cx, a * py + (1 - a) * cy)
        self._track_det = det
        self._track_seen_t = now
        self._frame_conf = det.conf


    def _draw_detections(self, m: MappedArray) -> None:
        try:
            # The worker owns the tracker; draw its last published result.
            snap = self._track_snapshot
            if snap is None or snap.det_cx is None or snap.det is None:
                return
            det = snap.det
            # Tracked (smoothed) position, in main-stream pixel space
            cx, cy = snap.det_cx, snap.det_cy
            in_zone = snap.in_zone
            color = (0, 0, 255) if in_zone else (0, 255, 0)

            # Scale to the current frame's dimensions for drawing
            fh, fw = m.array.shape[:2]
            sx = fw / self.width  if self.width  else 1.0
            sy = fh / self.height if self.height else 1.0
            draw_cx = int(cx * sx)
            draw_cy = int(cy * sy)

            cv2.circle(m.array, (draw_cx, draw_cy), 5, color, -1)

            if in_zone:
                # Scaled to the frame (a fixed 2.0 filled the 640 px preview),
                # bottom-left: the other overlays use the top and bottom-centre.
                scale = max(0.4, fh * 0.001)
                margin = max(6, int(fh * 0.02))
                cv2.putText(m.array, "IN SHOCK ZONE", (margin, fh - margin),
                            cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 255),
                            max(1, round(scale * 2)), cv2.LINE_AA)

            lbl = (f"{self._labels[det.category]}" if det.category < len(self._labels)
                   else f"cls{det.category}")
            cv2.putText(m.array, f"{lbl} ({det.conf:.2f})", (draw_cx + 10, draw_cy - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        except Exception as e:
            self.logger.error(f"Error in _draw_detections: {e}")


    # -----------------------------------------------------------------------
    # APA geometry — mask and shock zone
    # -----------------------------------------------------------------------

    def _configure_mask_and_shock_zone(self):
        try:
            self.mask_radius          = self.config.get("mask.mask_radius")
            self.mask_center_x_offset = self.config.get("mask.mask_center_x_offset") or 0
            self.mask_center_y_offset = self.config.get("mask.mask_center_y_offset") or 0
            self.mask_display         = bool(self.config.get("mask.mask_display", True))
            self.mask_record          = bool(self.config.get("mask.mask_record", True))
            self._mask_cache          = None

            self.shock_zone_display      = bool(self.config.get("shock_zone.shock_zone_display", True))
            self.shock_zone_record       = bool(self.config.get("shock_zone.shock_zone_record", False))
            self.track_display = bool(self.config.get("object_detection.overlay_display", True))
            self.track_record  = bool(self.config.get("object_detection.overlay_record", False))
            self.shock_zone_angle_span   = self.config.get("shock_zone.shock_zone_angle_span_deg")
            self.shock_zone_start_angle  = self.config.get("shock_zone.shock_zone_start_angle_deg") - 90
            self.shock_zone_inner_offset = self.config.get("shock_zone.shock_zone_inner_offset")
            color = self.config.get("shock_zone.shock_zone_color")
            self.shock_zone_color     = list(color.values()) if isinstance(color, dict) else color
            self.shock_zone_thickness = self.config.get("shock_zone.shock_zone_line_thickness")
        except Exception as e:
            self.logger.error(f"Error configuring mask/shock zone: {e}")


    def _mask_image(self, h: int, w: int) -> np.ndarray | None:
        """The arena-circle mask for an h×w frame, cached per size and
        geometry (lores and main differ, so the last one built is kept)."""
        if self.mask_radius is None:
            return None
        key = (h, w, self.mask_radius, self.mask_center_x_offset, self.mask_center_y_offset)
        if self._mask_cache is not None and self._mask_cache[0] == key:
            return self._mask_cache[1]
        r = int(0.5 * self.mask_radius * min(h, w))
        if r <= 0:
            return None
        img = np.zeros((h, w), dtype="uint8")
        cv2.circle(img, (w // 2 + self.mask_center_x_offset,
                         h // 2 + self.mask_center_y_offset), r, 255, -1)
        self._mask_cache = (key, img)
        return img

    def _apply_mask(self, m: MappedArray) -> None:
        mask_img = self._mask_image(*m.array.shape[:2])
        if mask_img is not None:
            m.array[:] = cv2.bitwise_and(m.array, m.array, mask=mask_img)

    def _masked_copy(self, frame: np.ndarray) -> np.ndarray:
        mask_img = self._mask_image(*frame.shape[:2])
        if mask_img is None:
            return frame
        return cv2.bitwise_and(frame, frame, mask=mask_img)


    def _apply_shock_zone(self, m: MappedArray) -> None:
        if self.mask_radius is None:
            return
        h, w    = m.array.shape[:2]
        cx      = w // 2 + self.mask_center_x_offset
        cy      = h // 2 + self.mask_center_y_offset
        outer_r = int(0.5 * self.mask_radius * min(h, w))
        if outer_r <= 0:
            return
        inner_r     = max(0, int(self.shock_zone_inner_offset * outer_r))
        start_angle = self.shock_zone_start_angle - (self.shock_zone_angle_span * 0.5)
        end_angle   = start_angle + self.shock_zone_angle_span
        sr, er  = np.radians(start_angle), np.radians(end_angle)
        color   = self.shock_zone_color
        thick   = max(1, self.shock_zone_thickness)
        cv2.ellipse(m.array, (cx, cy), (outer_r, outer_r), 0, start_angle, end_angle, color, thick)
        cv2.ellipse(m.array, (cx, cy), (inner_r, inner_r), 0, start_angle, end_angle, color, thick)
        for angle in (sr, er):
            ox = int(cx + outer_r * np.cos(angle))
            oy = int(cy + outer_r * np.sin(angle))
            ix = int(cx + inner_r * np.cos(angle))
            iy = int(cy + inner_r * np.sin(angle))
            cv2.line(m.array, (ox, oy), (ix, iy), color, thick)


    def _is_in_shock_zone(self, cx: int, cy: int) -> bool:
        """Check whether (cx, cy) — in main-stream pixel coordinates — lies in the shock zone."""
        if self.mask_radius is None or self.width is None or self.height is None:
            return False
        frame_cx    = self.width  // 2 + self.mask_center_x_offset
        frame_cy    = self.height // 2 + self.mask_center_y_offset
        outer_r     = int(0.5 * self.mask_radius * min(self.height, self.width))
        inner_r     = max(0, int(self.shock_zone_inner_offset * outer_r))
        start_angle = self.shock_zone_start_angle - (self.shock_zone_angle_span * 0.5)

        dx, dy = cx - frame_cx, cy - frame_cy
        r      = np.hypot(dx, dy)
        theta  = np.degrees(np.arctan2(dy, dx)) % 360

        if not (inner_r <= r <= outer_r):
            return False
        # Modulo, so an arc that crosses 0°/360° (e.g. a zone centred on 0°)
        # works; the raw start/end comparison missed those. bool(): numpy's
        # bool_ isn't JSON serialisable, and zone_state carries this value.
        return bool((theta - start_angle) % 360 <= self.shock_zone_angle_span)


    # -----------------------------------------------------------------------
    # HEF models — pick an on-device file or fetch an uploaded one
    # -----------------------------------------------------------------------

    @command()
    def list_hefs(self) -> dict:
        """On-device HEFs for the config card's model picker: the bundled /
        model-zoo ones in MODEL_DIR plus uploads in USER_HEF_DIR, and whether
        a Hailo accelerator is fitted at all."""
        hefs = []
        for source, folder in (("bundled", MODEL_DIR), ("uploaded", USER_HEF_DIR)):
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for name in names:
                path = os.path.join(folder, name)
                if name.endswith(".hef") and os.path.isfile(path):
                    hefs.append({"name": name, "path": path, "source": source,
                                 "size_bytes": os.path.getsize(path)})
        return {
            "hailo_present": os.path.exists(HAILO_DEVICE),
            "hefs": hefs,
            "current": self.config.get("object_detection.model_path", ""),
        }

    @command()
    def fetch_hef(self, controller_url: str = "", filename: str = "") -> dict:
        """Download an uploaded HEF from the controller (GET
        /hailo_models/<filename>, staged there by the web UI's upload) into
        USER_HEF_DIR. Returns the refreshed list_hefs() plus `fetched`."""
        if not _HEF_NAME_RE.match(filename or ""):
            return {**self.list_hefs(), "error": f"Invalid HEF filename: {filename!r}"}
        if not controller_url:
            return {**self.list_hefs(), "error": "controller_url is required"}
        url = f"{controller_url.rstrip('/')}/hailo_models/{filename}"
        dest = os.path.join(USER_HEF_DIR, filename)
        tmp = dest + ".part"
        try:
            os.makedirs(USER_HEF_DIR, exist_ok=True)
            with urllib.request.urlopen(url, timeout=30) as resp, open(tmp, "wb") as f:
                total = 0
                while chunk := resp.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_HEF_BYTES:
                        raise ValueError("HEF larger than 256 MB")
                    f.write(chunk)
            os.replace(tmp, dest)
        except Exception as e:
            with contextlib.suppress(OSError):
                os.remove(tmp)
            self.logger.error(f"fetch_hef {filename} failed: {e}")
            return {**self.list_hefs(), "error": f"Download failed: {e}"}
        self.logger.info(f"Fetched HEF {filename} ({total // 1024} KiB) to {dest}")
        return {**self.list_hefs(), "fetched": dest}


    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def stop(self) -> bool:
        try:
            if self._stop_inference_worker():
                with self._det_lock:
                    if self.detector is not None:
                        self.detector.close()
                        self.detector = None
            else:
                # Closing HailoRT under an in-flight run() aborts the process;
                # leave the device to the OS at exit instead.
                self.logger.error("APA inference worker did not stop; "
                                  "not closing the detector")
            return super().stop()
        except Exception as e:
            self.logger.error(f"Error stopping module: {e}")
            return False


def main():
    camera = APACameraModule()
    camera.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        camera.stop()


if __name__ == '__main__':
    main()
