#!/usr/bin/env python3
"""
SAVIOUR System - Hailo AI Camera Module

Built on CameraBase (src/modules/camera_base.py): normal segmented recording +
MJPEG stream + timestamp-CSV sidecar, unchanged. This variant adds a live
inference overlay on the MJPEG preview only — a stock pre-compiled HEF from the
Hailo model zoo. Inference runs on its OWN thread (_inference_worker), NOT the
libcamera capture callback: that thread also feeds the H264 encoder, and a
synchronous detect() there stalls capture + the encoder — the sync-client
frame-drop bug (docs/hailo-inference-sweep-2026-09-08.md,
docs/CHANGELOG.md, 2026-09-08). The capture thread hands the worker every
Nth frame (drop-oldest) and draws the result the worker last published.

Deliberately generic and demo-oriented:
  - No training, no DFC conversion. Models come from `download_hefs.sh`
    (Hailo model-zoo S3), curated in `modules/hailo_infer.CURATED_MODELS`.
  - `hailo.model` picks one; the "AI" tab in the camera config card is a
    dropdown over the curated list, grouped by category.
  - Or upload your own detection HEF from the AI tab (NMS compiled in, as
    `tools/convert_to_hailo.py` builds them): it lands in USER_HEF_DIR,
    `hailo.model` is then its filename, and its class names are the
    matching `hailo.custom_labels` entry ({"hef": name, "labels": [...]}).
    A list rather than a dict keyed by filename, since config keys are
    dot-separated paths and every filename has a dot in it.
  - Or upload a single-view Lightning Pose model (tools/convert_lp_pose_to_hailo.py
    builds the .hef and its <name>.json sidecar; upload both from the AI
    tab). Then EVERY frame's lores image goes to the model (every
    `hailo.lp_infer_every_n`th, default 1), not just previewed frames, and
    the keypoints land in the timestamp CSV as kp_<name>_x/_y/_conf, in
    recorded-video pixels, on the row of the frame that was actually
    inferred (rows are held back until their result arrives; a frame not
    inferred, dropped because the worker fell behind, or whose result is
    later than _LP_RESULT_TIMEOUT_S gets empty values, never a carried-forward
    result). Decode: modules/lp_decode.py. An LP model that fails to load, or
    whose preview stream doesn't share the recorded stream's aspect (or the
    training frames' aspect), blocks readiness: the keypoints are the point.
  - No Hailo device / missing HEF → runs as a plain camera (overlay shows why).
  - Recording is untouched *pixel-wise*: the overlay is on the lores/preview
    stream, the recorded "main" stream never sees it. A synchronized detection
    sidecar is a deliberate later step (see CLAUDE.md).

Inference load CAN cost recorded frames, indirectly.
  The inference + draw work runs on the same Pi as the H264 encoder, and
  under load the encoder drops handed frames (a CSV row is written, no
  container frame) and the capture cadence gets jittery. This shows up as
  the sync *client* camera lagging the sync server by a few frames in
  Post-Process compose. Measured on the desk rig (`tools/framesync_sweep.py`,
  30 fps, 60 s, sync client): `hailo.infer_every_n=1` (inference on EVERY
  frame) → ~6 capture drops + 1 encoder drop, gap-CV 0.056; `infer_every_n≥2`
  → indistinguishable from inference off (0 drops, gap-CV ~0.0002). The
  sync *server* stays clean at every setting.
  Mitigations, cheapest first: raise `hailo.infer_every_n` (2 is the file
  default and already clean at 30 fps); set `hailo.infer_enabled=false` for a
  recording where the live overlay isn't needed; or run the camera free-run
  (`camera.sync_mode="none"`) so there is no phase-nudging for the encoder
  jitter to compound with. ALWAYS check `framesync_report.json` (or
  `tools/analyse_framesync.py`) after changing model / infer_every_n / fps /
  sensor mode, before trusting a multi-camera session's alignment.
  See plans/multicam-frame-alignment-and-sync-provenance.md item 3.

Author: Andrew SG
"""

import collections
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time

import cv2
import numpy as np
from picamera2 import MappedArray

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.camera_base import CameraBase
from modules.hailo_infer import (
    COCO_SKELETON,
    CURATED_MODELS,
    DEFAULT_MODEL,
    HEF_NAME_RE,
    MODEL_DIR,
    USER_HEF_DIR,
    HailoDepthEstimator,
    HailoDetector,
    HailoLPPoseDetector,
    HailoPoseDetector,
    HailoSegDetector,
    download_hef,
    labels_for,
)
from modules.lp_decode import (
    DECODE_METHODS,
    LP_MODEL_TYPE,
    LPModelError,
    LPModelMeta,
    load_meta,
    validate_meta,
)
from modules.module import check, command

# One distinct BGR colour per class index, cycled — enough for COCO's 80.
_PALETTE = [
    (0, 255, 0), (0, 165, 255), (255, 128, 0), (0, 0, 255), (255, 0, 255),
    (255, 255, 0), (128, 0, 255), (0, 255, 255), (200, 200, 200), (0, 128, 255),
]
HAILO_DEVICE = "/dev/hailo0"
DOWNLOAD_HEFS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "download_hefs.sh")


def _hailo_device_present() -> bool:
    """The Hailo's character device exists, i.e. its driver bound."""
    return os.path.exists(HAILO_DEVICE)

_KP_COLOUR = (0, 255, 255)      # keypoints
_BONE_COLOUR = (0, 255, 0)      # skeleton edges
# A held-back CSV row is released with empty keypoints if its frame's result
# hasn't arrived by then (the worker fell over or is far behind).
_LP_RESULT_TIMEOUT_S = 1.0
# How long closing a CSV (segment rotation, stop) waits for in-flight results.
_LP_CLOSE_WAIT_S = 0.3
# Allowed relative aspect difference between the preview (inference) stream,
# the recorded stream and the model's training frames: covers rounding the
# preview size to even pixels, nothing more.
_LP_ASPECT_TOL = 0.01


def sidecar_path(hef_path: str) -> str:
    """<name>.json next to <name>.hef: an uploaded model's metadata."""
    return os.path.splitext(hef_path)[0] + ".json"


class HailoCameraModule(CameraBase):
    CONFIG_FILENAME = "hailo_camera_config.json"

    def __init__(self, module_type="hailo_camera"):
        super().__init__(module_type)
        self._det_lock = threading.Lock()
        self._hef_fetch: threading.Thread | None = None
        self._hef_fetch_error: str | None = None
        self._rebuild_lock = threading.Lock()   # serialises rebuilds; not held per-frame
        self.detector = None                 # HailoDetector | HailoPoseDetector | None
        self._detector_error: str | None = None
        self._model_key = DEFAULT_MODEL
        self._task = "detection"             # "detection" | "pose"
        self._labels: list[str] = labels_for(DEFAULT_MODEL)
        self._max_labels = 40
        self._infer_error_logged = False
        self._rebuilding = False
        # Inference throttle: hand the net every Nth streamed frame (the
        # worker below redraws the cached result on the frames in between).
        self._infer_every_n = 2
        self._infer_counter = 0
        self._last_results: list = []
        self._last_summary = ""

        # Inference runs on its OWN thread, not the libcamera capture-callback
        # thread that _process_lores_frame is called on — that thread also
        # feeds the H264 encoder, and a synchronous detect() there stalls
        # capture + the encoder (the sync-client frame-drop bug, quantified in
        # docs/hailo-inference-sweep-2026-09-08.md; design in
        # docs/CHANGELOG.md, 2026-09-08). The capture thread now only
        # copies the frame into _infer_q (drop-oldest, maxsize 1) and draws
        # the last published result; the worker does the detect().
        self._infer_q: queue.Queue = queue.Queue(maxsize=1)
        self._infer_stop = threading.Event()
        self._infer_worker: threading.Thread | None = None
        # detect() wall-time samples (ms). Feeds a ~30 s p50/p95/p99/max log
        # line and the _check_hailo message -- the "how much headroom did the
        # worker thread buy / would a faster HAT help" measurement
        # (docs/CHANGELOG.md, 2026-09-08).
        self._infer_ms: collections.deque = collections.deque(maxlen=600)
        self._infer_ms_logged_at = 0.0

        # Lightning Pose (task "lp_pose"): per-frame keypoints into the CSV.
        self._lp_selected = False            # the chosen model is an LP model
        self._lp_every_n = 1
        self._lp_seq = 0                     # frame counter, capture thread
        self._lp_last = None                 # (seq, timestamp_ns, (K,3) main px)
        self._lp_main_rot_k = 0
        self._lp_shapes = None               # (main hw, lores hw, rot) last validated
        self._lp_fov_error: str | None = None
        self._lp_columns_next: list = []     # CSV_EXTRA_COLUMNS for the next CSV
        self._csv_kp_names: tuple = ()       # keypoints of the open CSV's columns
        # seq -> {"row", "state", "t", "kp", "names"}; insertion (= frame) order
        self._pending: collections.OrderedDict = collections.OrderedDict()
        self._pending_lock = threading.Lock()
        self._decode_ms: collections.deque = collections.deque(maxlen=600)
        self._lp_counts = dict.fromkeys(
            ("inferred", "skipped", "dropped", "late", "failed"), 0)
        self._lp_counts_logged = dict(self._lp_counts)
        self.CSV_EXTRA_COLUMNS = []

        # CameraBase.__init__ runs _configure_camera(), not
        # configure_module_special(), so build the detector explicitly here —
        # same pattern as habitat_camera's _configure_habitat_motion() call.
        # Synchronous on first construction (nothing is streaming yet); every
        # later rebuild goes through _rebuild_detector_async().
        self._build_detector()
        self._start_inference_worker()

    # ── config ───────────────────────────────────────────────────────────────

    def _infer_enabled(self) -> bool:
        """`hailo.infer_enabled` (default true). When false the module skips
        loading a HEF and runs the preview with no inference — used to
        characterise how much the inference thread costs the H264 encoder
        (see plans/multicam-frame-alignment-and-sync-provenance.md)."""
        return bool(self.config.get("hailo.infer_enabled", True))

    def _configure_module_extra(self, updated_keys) -> None:
        hailo_keys = (None if updated_keys is None
                      else {k for k in updated_keys if k.startswith("hailo.")})
        if hailo_keys is not None and not hailo_keys:
            return
        if (hailo_keys is None or "hailo.model" in hailo_keys
                or "hailo.infer_enabled" in hailo_keys):
            # Loading a HEF onto the Hailo device takes a few seconds and the
            # 8L won't hold two VDevices at once, so do it off the config-set
            # handler thread and drop to a plain-camera preview while it swaps.
            # Toggling infer_enabled goes the same route (load or tear down).
            self._rebuild_detector_async()
        else:
            # threshold / infer_every_n / max_labels / max_detections don't
            # need a HEF reload — apply them in place.
            self._apply_light_config()

    def _lp_decode_method(self) -> str:
        m = self.config.get("hailo.lp_decode", "exact")
        if m not in DECODE_METHODS:
            self.logger.warning(f"hailo.lp_decode {m!r} unknown — using exact")
            m = "exact"
        return m

    def _apply_light_config(self) -> None:
        self._lp_every_n = max(1, int(self.config.get("hailo.lp_infer_every_n", 1)))
        det = self.detector
        if isinstance(det, HailoLPPoseDetector):
            det.decode_method = self._lp_decode_method()
        self._infer_every_n = max(1, int(self.config.get("hailo.infer_every_n", 2)))
        self._max_labels = int(self.config.get("hailo.max_labels", 40))
        threshold = float(self.config.get("hailo.threshold", 0.4))
        max_det = max(1, int(self.config.get("hailo.max_detections", 12)))
        with self._det_lock:
            # An uploaded model's class names (hailo.custom_labels) apply
            # without a reload too.
            if self._model_key not in CURATED_MODELS:
                self._labels = self._uploaded_labels(self._model_key)
            if self.detector is not None:
                self.detector.set_threshold(threshold)
                if hasattr(self.detector, "set_max_det"):
                    self.detector.set_max_det(max_det)

    def _fetch_stock_hefs(self, hef_path: str) -> str:
        """A stock model-zoo HEF is missing (download_hefs.sh failed or never
        ran when the module was set up, usually because the Hailo driver
        wasn't up yet). Run the script in the background, once per process,
        and rebuild when it's done. Returns the status line to show."""
        if self._hef_fetch is not None and self._hef_fetch.is_alive():
            return "downloading the stock Hailo models…"
        if self._hef_fetch_error:
            return (f"HEF not found: {hef_path} — automatic download failed "
                    f"({self._hef_fetch_error}); run download_hefs.sh by hand")
        if not _hailo_device_present():
            return ("HEF not found and the Hailo driver isn't loaded (no "
                    "/dev/hailo*) — reboot this module; it downloads its "
                    "models itself once the Hailo is up")

        def _run() -> None:
            self.logger.info("Stock HEFs missing — running download_hefs.sh")
            try:
                r = subprocess.run(["bash", DOWNLOAD_HEFS], capture_output=True,
                                   text=True, timeout=1200, check=False)
                if r.returncode != 0:
                    lines = (r.stderr or r.stdout or "").strip().splitlines()
                    self._hef_fetch_error = (lines[-1] if lines
                                             else f"exit {r.returncode}")[-200:]
                    self.logger.warning(
                        f"download_hefs.sh failed: {self._hef_fetch_error}")
                else:
                    self.logger.info("Stock HEFs downloaded")
            except Exception as e:  # timeout, bash missing …
                self._hef_fetch_error = str(e)[-200:]
                self.logger.warning(f"download_hefs.sh failed: {e}")
            self._rebuild_detector_async()

        self._hef_fetch = threading.Thread(target=_run, daemon=True,
                                           name="hailo-hef-download")
        self._hef_fetch.start()
        return "downloading the stock Hailo models…"

    def _rebuild_detector_async(self) -> None:
        threading.Thread(target=self._build_detector, args=(True,),
                         daemon=True, name="hailo-rebuild").start()

    # ── inference worker thread ──────────────────────────────────────────────

    def _start_inference_worker(self) -> None:
        if self._infer_worker and self._infer_worker.is_alive():
            return
        self._infer_stop.clear()
        self._infer_worker = threading.Thread(
            target=self._inference_worker, daemon=True, name="hailo-infer")
        self._infer_worker.start()

    def _stop_inference_worker(self, timeout: float = 3.0) -> bool:
        """Signal + join the worker. MUST return True before any
        detector.close() (module stop) — closing HailoRT under an in-flight
        run() is a hard SIGABRT. A live swap via _build_detector does NOT
        need this: it closes the old device under _det_lock, which the worker
        also holds around detect(), so the swap already waits for the current
        frame. Returns False if the worker did not stop in `timeout` (caller
        must then NOT close the detector)."""
        self._infer_stop.set()
        w = self._infer_worker
        if w and w.is_alive():
            w.join(timeout=timeout)
            if w.is_alive():
                self.logger.warning(
                    "hailo inference worker did not stop in %.1fs — "
                    "leaving the Hailo device open to avoid a close-mid-run abort",
                    timeout)
                return False
        self._infer_worker = None
        return True

    def _inference_worker(self) -> None:
        """Pull the freshest handed lores frame and run detect() on it, off
        the capture thread. Publishes self._last_results for
        _process_lores_frame to draw. _det_lock is held only around detect(),
        so _build_detector's close() still serialises against it."""
        self.logger.info("Hailo inference worker started")
        while not self._infer_stop.is_set():
            try:
                frame = self._infer_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if isinstance(frame, tuple):
                self._infer_lp(*frame)
                self._maybe_log_infer_timing()
                continue
            with self._det_lock:
                detector = self.detector
                labels = self._labels
                if detector is None:
                    self._last_results = []
                    continue
                t0 = time.monotonic()
                try:
                    results = detector.detect(frame, labels)
                except Exception as e:  # noqa: BLE001
                    if not self._infer_error_logged:
                        self.logger.error(f"Hailo inference failed: {e}")
                        self._infer_error_logged = True
                    self._last_results = []
                    continue
                self._infer_ms.append((time.monotonic() - t0) * 1000.0)
            self._last_results = results
            self._maybe_log_infer_timing()
        self.logger.info("Hailo inference worker stopped")

    def _infer_lp(self, seq: int, timestamp_ns: int, frame, out_size) -> None:
        """One LP frame: Hailo run under _det_lock (a model swap closes the
        device under the same lock), CPU decode outside it. The result is
        stamped with the frame it came from (seq, timestamp_ns), never the
        frame that's current when it arrives."""
        with self._det_lock:
            det = self.detector
            if not isinstance(det, HailoLPPoseDetector):
                self._lp_resolve(seq, "failed")
                return
            if getattr(self, "_cb_monochrome", False):
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            t0 = time.monotonic()
            try:
                raw = det.infer(frame)
            except Exception as e:  # noqa: BLE001
                if not self._infer_error_logged:
                    self.logger.error(f"Hailo LP inference failed: {e}")
                    self._infer_error_logged = True
                self._lp_counts["failed"] += 1
                self._lp_resolve(seq, "failed")
                return
            self._infer_ms.append((time.monotonic() - t0) * 1000.0)
        t1 = time.monotonic()
        try:
            kp = det.decode(raw, out_size)
        except Exception as e:  # noqa: BLE001
            if not self._infer_error_logged:
                self.logger.error(f"LP heatmap decode failed: {e}")
                self._infer_error_logged = True
            self._lp_counts["failed"] += 1
            self._lp_resolve(seq, "failed")
            return
        self._decode_ms.append((time.monotonic() - t1) * 1000.0)
        self._lp_counts["inferred"] += 1
        self._lp_last = (seq, timestamp_ns, kp, det.meta)
        self._lp_resolve(seq, "done", kp, det.meta.keypoint_names)

    @staticmethod
    def _pcts(samples) -> dict | None:
        s = sorted(samples)
        if len(s) < 30:
            return None

        def pct(p):
            return s[min(len(s) - 1, int(p / 100.0 * len(s)))]
        return {"n": len(s), "p50": pct(50), "p95": pct(95),
                "p99": pct(99), "max": s[-1]}

    def _infer_timing_stats(self) -> dict | None:
        """p50/p95/p99/max of the last detect() calls (ms), or None if too
        few samples yet."""
        s = sorted(self._infer_ms)
        if len(s) < 30:
            return None
        def pct(p):
            return s[min(len(s) - 1, int(p / 100.0 * len(s)))]
        return {"n": len(s), "p50": pct(50), "p95": pct(95),
                "p99": pct(99), "max": s[-1]}

    def _maybe_log_infer_timing(self, interval_s: float = 30.0) -> None:
        now = time.monotonic()
        if now - self._infer_ms_logged_at < interval_s:
            return
        st = self._infer_timing_stats()
        if st is None:
            return
        self._infer_ms_logged_at = now
        if self._task != "lp_pose":
            self.logger.info(
                "hailo detect(): p50=%.1f p95=%.1f p99=%.1f max=%.1f ms  "
                "(n=%d, model=%s, infer_every_n=%d)",
                st["p50"], st["p95"], st["p99"], st["max"], st["n"],
                self._model_key, self._infer_every_n)
            return
        # Lightning Pose: timing plus what happened to every frame since the
        # last line; frames that weren't inferred as configured get WARNING.
        dec = self._pcts(self._decode_ms)
        counts = dict(self._lp_counts)
        delta = {k: counts[k] - self._lp_counts_logged.get(k, 0) for k in counts}
        self._lp_counts_logged = counts
        dm = getattr(self.detector, "decode_method", "?")
        d50, d95 = (dec["p50"], dec["p95"]) if dec else (float("nan"), float("nan"))
        msg = (f"LP: hailo run p50={st['p50']:.1f} p95={st['p95']:.1f} ms, decode ({dm}) "
               f"p50={d50:.1f} p95={d95:.1f} ms; last {interval_s:.0f}s: "
               f"{delta['inferred']} inferred, {delta['skipped']} skipped (every "
               f"{self._lp_every_n}), {delta['dropped']} dropped (worker behind), "
               f"{delta['late']} late, {delta['failed']} failed")
        if delta["dropped"] or delta["late"] or delta["failed"]:
            self.logger.warning(msg)
        else:
            self.logger.info(msg)

    def _hef_path(self, spec: dict) -> str:
        # An uploaded model's spec carries an absolute path, which join keeps.
        return os.path.join(MODEL_DIR, spec["hef"])

    def _uploaded_labels(self, filename: str) -> list[str]:
        """Class names for an uploaded HEF, from `hailo.custom_labels`.
        Unnamed classes (or none at all) draw as their class index."""
        for entry in self.config.get("hailo.custom_labels", []) or []:
            if isinstance(entry, dict) and entry.get("hef") == filename:
                return [str(name).strip() for name in entry.get("labels") or []]
        return []

    def _model_spec(self, model_key: str) -> tuple[str, dict]:
        """(key, spec) for `hailo.model`: a CURATED_MODELS key, or the
        filename of an uploaded HEF in USER_HEF_DIR: a Lightning Pose model
        if a <name>.json sidecar sits next to it (spec["meta"], or
        spec["meta_error"] when the sidecar is invalid), else a detector with
        NMS compiled in. Anything else falls back to DEFAULT_MODEL."""
        if model_key in CURATED_MODELS:
            return model_key, CURATED_MODELS[model_key]
        if HEF_NAME_RE.match(str(model_key or "")):
            hef = os.path.join(USER_HEF_DIR, model_key)
            if os.path.exists(sidecar_path(hef)):
                spec = {"hef": hef, "task": "lp_pose", "labels": [], "uploaded": True}
                try:
                    spec["meta"] = load_meta(sidecar_path(hef))
                except LPModelError as e:
                    spec["meta_error"] = str(e)
                return model_key, spec
            return model_key, {
                "hef": hef,
                "task": "detection",
                "labels": self._uploaded_labels(model_key),
                "uploaded": True,
            }
        self.logger.warning(f"hailo.model '{model_key}' not in the curated list — using {DEFAULT_MODEL}")
        return DEFAULT_MODEL, CURATED_MODELS[DEFAULT_MODEL]

    def _build_detector(self, swap: bool = False) -> None:
        with self._rebuild_lock:
            if not self._infer_enabled():
                # Tear down any live detector and run as a plain camera. The
                # close() is under _det_lock for the same reason the swap path
                # below is — no frame may be mid-run() on `old`.
                with self._det_lock:
                    old, self.detector = self.detector, None
                    self._detector_error = "disabled (hailo.infer_enabled=false)"
                    self._model_key = self.config.get("hailo.model", DEFAULT_MODEL)
                    self._rebuilding = False
                if old is not None:
                    try:
                        old.close()
                    except Exception:
                        pass
                self._set_lp_columns(None)
                self.logger.info(
                    "Hailo inference disabled by config "
                    "(hailo.infer_enabled=false) — recording as a plain camera"
                )
                return

            model_key = self.config.get("hailo.model", DEFAULT_MODEL)
            threshold = float(self.config.get("hailo.threshold", 0.4))
            self._max_labels = int(self.config.get("hailo.max_labels", 40))
            self._infer_every_n = max(1, int(self.config.get("hailo.infer_every_n", 2)))
            self._lp_every_n = max(1, int(self.config.get("hailo.lp_infer_every_n", 1)))
            max_det = max(1, int(self.config.get("hailo.max_detections", 12)))

            model_key, spec = self._model_spec(model_key)
            self._lp_selected = spec.get("task") == "lp_pose"

            # Release the current device handle BEFORE opening a new one — the
            # Hailo-8L can't hold two VDevices. The close() MUST happen under
            # _det_lock: _process_lores_frame holds that lock for the whole
            # inference call, so taking it here guarantees no frame is mid-run()
            # on `old` when we tear it down (closing a device out from under an
            # in-flight run() aborts the HailoRT pipeline and the teardown
            # self-joins a thread -> std::system_error "Resource deadlock
            # avoided" -> SIGABRT).
            if swap:
                with self._det_lock:
                    old, self.detector = self.detector, None
                    self._detector_error = "loading model…"
                    self._rebuilding = True
                    if old is not None:
                        try:
                            old.close()
                        except Exception:
                            pass

            task = spec.get("task", "detection")
            hef_path = self._hef_path(spec)
            new_detector = None
            error = None
            if not os.path.exists(hef_path) and spec.get("uploaded"):
                error = f"HEF not found: {hef_path} — upload it again from the AI tab"
            elif not os.path.exists(hef_path):
                error = self._fetch_stock_hefs(hef_path)
            elif spec.get("meta_error"):
                error = f"LP model metadata: {spec['meta_error']}"
            elif task == "lp_pose" and (fov := self._lp_static_fov_error(spec["meta"])):
                error = fov
            else:
                try:
                    if task == "lp_pose":
                        new_detector = HailoLPPoseDetector(
                            hef_path, spec["meta"], self._lp_decode_method())
                    elif task == "pose":
                        new_detector = HailoPoseDetector(hef_path, threshold=threshold)
                    elif task == "segmentation":
                        new_detector = HailoSegDetector(hef_path, threshold=threshold,
                                                       max_det=max_det)
                    elif task == "depth":
                        new_detector = HailoDepthEstimator(hef_path, threshold=threshold)
                    else:
                        new_detector = HailoDetector(hef_path, threshold=threshold)
                except Exception as e:  # no Hailo device, driver missing, arch mismatch …
                    error = f"{type(e).__name__}: {e}"
                    if not _hailo_device_present():
                        error = ("Hailo driver not loaded (no /dev/hailo*) — reboot "
                                 "this module; a newly installed driver needs one")

            meta = new_detector.meta if isinstance(new_detector, HailoLPPoseDetector) else None
            with self._det_lock:
                old = self.detector if not swap else None
                self.detector = new_detector
                self._detector_error = error
                self._model_key = model_key
                self._task = task
                self._lp_fov_error = None
                self._lp_shapes = None
                self._lp_last = None
                self._set_lp_columns(meta)
                self._labels = (spec["labels"] if spec.get("uploaded")
                                else labels_for(model_key))
                self._infer_error_logged = False
                self._rebuilding = False
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass

        if isinstance(new_detector, HailoLPPoseDetector):
            m = new_detector.meta
            self.logger.info(
                f"Lightning Pose model ready: {model_key} — {len(m.keypoint_names)} "
                f"keypoints, input {m.input_size[1]}x{m.input_size[0]}, stride "
                f"{m.heatmap_stride}, output {new_detector.output_desc}, decode "
                f"{new_detector.decode_method}, every {self._lp_every_n} frame(s)")
        elif new_detector is not None:
            self.logger.info(f"Hailo {task} model ready: {model_key} ({spec['hef']}), threshold {threshold}")
        elif task == "lp_pose":
            self.logger.error(f"Lightning Pose model unavailable ({error}) — "
                              "no keypoints will be recorded")
        else:
            self.logger.warning(f"Hailo model unavailable ({error}) — running as a plain camera")

    # ── Lightning Pose: field of view, CSV columns, per-frame keypoints ───────

    @staticmethod
    def _aspect_mismatch(a: tuple[int, int], b: tuple[int, int]) -> bool:
        """a, b = (width, height); True if their aspects differ by more than
        _LP_ASPECT_TOL."""
        return abs((a[0] / a[1]) / (b[0] / b[1]) - 1.0) > _LP_ASPECT_TOL

    def _lp_fov_problem(self, meta: LPModelMeta, main_wh, lores_wh) -> str | None:
        """Keypoints are mapped from the preview (lores) stream the model
        sees to recorded-video pixels by scaling alone, which is only right
        if both streams show the same field of view. picamera2 scales both
        from the same sensor crop (ScalerCrop), so that reduces to the same
        aspect. The model also has to see frames shaped like its training
        frames, since LP stretch-resizes to its input."""
        if self._aspect_mismatch(main_wh, lores_wh):
            return (f"LP model off: preview stream {lores_wh[0]}x{lores_wh[1]} and "
                    f"recorded stream {main_wh[0]}x{main_wh[1]} differ in aspect, "
                    "so keypoints can't be mapped onto the recording")
        src = meta.source_frame_size
        if src and self._aspect_mismatch(main_wh, src):
            return (f"LP model off: trained on {src[0]}x{src[1]} frames but the camera "
                    f"records {main_wh[0]}x{main_wh[1]}; set a camera size/crop "
                    "with the training aspect")
        return None

    def _lp_static_fov_error(self, meta: LPModelMeta) -> str | None:
        """Config-time check from the configured stream sizes (the per-frame
        hook re-checks the real buffers)."""
        w, h = getattr(self, "width", None), getattr(self, "height", None)
        lw, lh = getattr(self, "lores_width", None), getattr(self, "lores_height", None)
        if not (w and h and lw and lh):
            return None
        # Rotation doesn't matter: both streams are rotated together or not
        # at all (see _frame_request_hook), so they keep the same aspect.
        return self._lp_fov_problem(meta, (int(w), int(h)), (int(lw), int(lh)))

    def _set_lp_columns(self, meta: LPModelMeta | None) -> None:
        """The CSV header is fixed when a segment's CSV opens, so a model
        change takes effect on the next CSV; until then the open CSV keeps
        its columns (results from a model with other keypoints stay empty)."""
        self._lp_columns_next = meta.csv_columns() if meta else []
        if getattr(self, "_timestamp_csv_writer", None) is None:
            self.CSV_EXTRA_COLUMNS = list(self._lp_columns_next)

    def _open_timestamp_csv(self, video_filename: str) -> None:
        self.CSV_EXTRA_COLUMNS = list(self._lp_columns_next)
        det = self.detector
        self._csv_kp_names = (tuple(det.meta.keypoint_names)
                              if isinstance(det, HailoLPPoseDetector)
                              and self.CSV_EXTRA_COLUMNS == det.meta.csv_columns() else ())
        with self._pending_lock:
            self._pending.clear()
        super()._open_timestamp_csv(video_filename)

    def _close_timestamp_csv(self, stage: bool = True) -> str | None:
        self._flush_pending(_LP_CLOSE_WAIT_S)
        return super()._close_timestamp_csv(stage)

    def _frame_request_hook(self, req, timing, main_shape, main_rot_k):
        """Capture thread, every frame. With an LP model loaded: copy this
        frame's lores image (oriented like the recorded frame) into the
        worker queue with its frame number and timestamp, and register the
        frame's CSV row as pending. Returns (seq, queued) for _emit_csv_row,
        or None to write the row straight away (no LP model / FOV error)."""
        det = self.detector
        if not isinstance(det, HailoLPPoseDetector) or self._lp_fov_error:
            return None
        self._lp_seq += 1
        seq = self._lp_seq
        csv_open = self._timestamp_csv_writer is not None and self._encoder_active
        if seq % self._lp_every_n:
            self._lp_counts["skipped"] += 1
            if csv_open:
                self._add_pending(seq, "skip")
            return (seq, False)
        with MappedArray(req, "lores") as lm:
            lores = lm.array
            key = (tuple(main_shape[:2]), tuple(lores.shape[:2]), main_rot_k)
            if key != self._lp_shapes:
                self._lp_shapes = key
                mh, mw = main_shape[:2]
                lh, lw = lores.shape[:2]
                if main_rot_k % 2:
                    lh, lw = lw, lh
                self._lp_fov_error = self._lp_fov_problem(det.meta, (mw, mh), (lw, lh))
                if self._lp_fov_error:
                    self.logger.error(self._lp_fov_error)
                    return None
            frame = lores[:, :, :3].copy()
        if main_rot_k:
            frame = np.ascontiguousarray(np.rot90(frame, main_rot_k))
        self._lp_main_rot_k = main_rot_k
        if csv_open:
            self._add_pending(seq, "wait")
        item = (seq, timing.timestamp_ns, frame, (int(main_shape[1]), int(main_shape[0])))
        try:
            stale = self._infer_q.get_nowait()
        except queue.Empty:
            stale = None
        if isinstance(stale, tuple):
            self._lp_counts["dropped"] += 1        # the worker fell behind
            self._lp_resolve(stale[0], "dropped")
        try:
            self._infer_q.put_nowait(item)
        except queue.Full:
            self._lp_counts["dropped"] += 1
            self._lp_resolve(seq, "dropped")
        return (seq, True)

    def _add_pending(self, seq: int, state: str) -> None:
        with self._pending_lock:
            self._pending[seq] = {"row": None, "state": state,
                                  "t": time.monotonic(), "kp": None, "names": ()}

    def _emit_csv_row(self, row: list, token) -> None:
        if token is None:
            return super()._emit_csv_row(row, token)
        seq = token[0]
        with self._pending_lock:
            entry = self._pending.get(seq)
            if entry is None:            # registered before the CSV opened, or cleared
                self._csv_row_buffer.append(row)
                return
            entry["row"] = row
            self._release_pending_locked(time.monotonic())

    def _lp_resolve(self, seq: int, state: str, kp=None, names=()) -> None:
        """Worker (or capture thread, for drops): this frame's result."""
        with self._pending_lock:
            entry = self._pending.get(seq)
            # Not pending any more = released as late already: drop the result.
            if entry is not None and entry["state"] == "wait":
                entry.update(state=state, kp=kp, names=tuple(names))
            self._release_pending_locked(time.monotonic())

    def _release_pending_locked(self, now: float, force: bool = False) -> None:
        """Move resolved rows from the head of the queue to the CSV buffer,
        in frame order. A row still waiting after _LP_RESULT_TIMEOUT_S (or
        on `force`) goes out with empty keypoints. Caller holds _pending_lock."""
        while self._pending:
            seq, e = next(iter(self._pending.items()))
            age = now - e["t"]
            if e["row"] is None:           # its row never came (CSV closed)
                if force or age > _LP_RESULT_TIMEOUT_S:
                    self._pending.popitem(last=False)
                    continue
                return
            if e["state"] == "wait":
                if not (force or age > _LP_RESULT_TIMEOUT_S):
                    return
                e["state"] = "late"
                self._lp_counts["late"] += 1
            if e["state"] == "done":
                self._fill_row(e["row"], e["kp"], e["names"])
            self._pending.popitem(last=False)
            self._csv_row_buffer.append(e["row"])

    def _fill_row(self, row: list, kp, names: tuple) -> None:
        if not self._csv_kp_names or names != self._csv_kp_names:
            return
        base = len(self.BASE_CSV_COLUMNS)
        for i in range(len(names)):
            x, y, c = (float(v) for v in kp[i])
            if np.isfinite(x) and np.isfinite(y):
                row[base + 3 * i] = f"{x:.3f}"
                row[base + 3 * i + 1] = f"{y:.3f}"
            if np.isfinite(c):
                row[base + 3 * i + 2] = f"{c:.4f}"

    def _flush_pending(self, wait_s: float) -> None:
        """Before a CSV closes: wait up to `wait_s` for the results of rows
        already pending (not for frames still arriving), then release
        everything, unfinished rows with empty keypoints."""
        with self._pending_lock:
            if not self._pending:
                return
            last = next(reversed(self._pending))
        worker = self._infer_worker
        deadline = time.monotonic() + (wait_s if worker and worker.is_alive() else 0.0)
        while time.monotonic() < deadline:
            with self._pending_lock:
                waiting = any(e["state"] == "wait" for k, e in self._pending.items() if k <= last)
            if not waiting:
                break
            time.sleep(0.01)
        with self._pending_lock:
            self._release_pending_locked(time.monotonic(), force=True)

    # ── per-frame overlay (MJPEG/preview stream only) ─────────────────────────

    def _status_line(self, m: MappedArray, text: str, colour: tuple) -> None:
        # Bottom-left corner: timestamp owns top-centre, FPS owns top-right
        # (same collision habitat_camera dodges the same way).
        cv2.putText(m.array, text, (10, m.array.shape[0] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)

    def _process_lores_frame(self, m: MappedArray, timing) -> None:
        # Capture-thread side: NO detect() here (see _inference_worker). Hand
        # every Nth frame to the worker (drop-oldest) and draw the result the
        # worker last published. _det_lock is not taken on this path.
        detector = self.detector          # atomic read; may flip during a swap
        if detector is None:
            self._last_results = []
            if self._rebuilding:
                self._status_line(m, "AI: loading model…", (0, 191, 255))
            else:
                self._status_line(
                    m, f"AI off: {self._detector_error or 'no model'}", (0, 0, 255))
            return

        if self._task == "lp_pose":
            # Frames reach the LP model from _frame_request_hook (every frame,
            # not just previewed ones); here we only draw the latest result.
            if self._lp_fov_error:
                self._status_line(m, self._lp_fov_error, (0, 0, 255))
                return
            summary = self._draw_lp(m.array, self._lp_last)
            self._status_line(m, f"[{self._model_key}] {summary}", (255, 255, 255))
            return

        self._infer_counter += 1
        if self._infer_counter % max(1, self._infer_every_n) == 0:
            try:
                self._infer_q.get_nowait()          # drop the stale frame
            except queue.Empty:
                pass
            try:
                self._infer_q.put_nowait(m.array.copy())
            except queue.Full:
                pass

        model_key, task, labels = self._model_key, self._task, self._labels
        results = self._last_results

        if task == "pose":
            summary = self._draw_poses(m.array, results)
        elif task == "segmentation":
            summary = self._draw_masks(m.array, results, labels)
        elif task == "depth":
            summary = self._draw_depth(m.array, results)
        else:
            summary = self._draw_detections(m.array, results, labels)
        self._status_line(m, f"[{model_key}] {summary}", (255, 255, 255))

    def _draw_detections(self, frame, dets, labels) -> str:
        counts: dict[str, int] = {}
        for d in dets[: self._max_labels]:
            name = labels[d.category] if d.category < len(labels) else str(d.category)
            counts[name] = counts.get(name, 0) + 1
            x, y, w, h = d.box
            colour = _PALETTE[d.category % len(_PALETTE)]
            cv2.rectangle(frame, (x, y), (x + w, y + h), colour, 2, cv2.LINE_AA)
            cv2.putText(frame, f"{name} {int(d.conf * 100)}%", (x, max(12, y - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)
        return "  ".join(f"{n}x {k}" for k, n in
                         sorted(counts.items(), key=lambda kv: -kv[1])) or "nothing"

    def _draw_poses(self, frame, poses) -> str:
        for p in poses[: self._max_labels]:
            kp = p.keypoints
            for a, b in COCO_SKELETON:
                xa, ya, ca = kp[a]
                xb, yb, cb = kp[b]
                if ca > 0.3 and cb > 0.3:
                    cv2.line(frame, (xa, ya), (xb, yb), _BONE_COLOUR, 2, cv2.LINE_AA)
            for x, y, c in kp:
                if c > 0.3:
                    cv2.circle(frame, (x, y), 3, _KP_COLOUR, -1, cv2.LINE_AA)
        n = len(poses)
        return f"{n} {'person' if n == 1 else 'people'}"

    def _draw_lp(self, frame, last) -> str:
        """Keypoints + skeleton of the latest LP result on the preview.
        Results are in recorded-frame pixels; the preview is the lores frame,
        rotated afterwards by whatever software rotation the recorded frame
        did NOT get in place (90/270 on a non-square output)."""
        if last is None:
            return "LP: waiting for first result"
        _, _, kp, meta = last
        mw, mh = int(self.width or 1), int(self.height or 1)
        k = ((int(getattr(self, "_rotation", 0) or 0) // 90) - self._lp_main_rot_k) % 4
        if self._lp_main_rot_k % 2:
            mw, mh = mh, mw
        rw, rh = (mh, mw) if k % 2 else (mw, mh)
        fh, fw = frame.shape[:2]
        sx, sy = fw / rw, fh / rh
        thr = float(self.config.get("hailo.threshold", 0.4))

        def pt(i):
            x, y, c = (float(v) for v in kp[i])
            if not (np.isfinite(x) and np.isfinite(y)) or not c >= thr:
                return None
            if k == 1:
                x, y = y, mw - x
            elif k == 2:
                x, y = mw - x, mh - y
            elif k == 3:
                x, y = mh - y, x
            return int(round(x * sx)), int(round(y * sy))

        names = meta.keypoint_names
        idx = {n: i for i, n in enumerate(names)}
        pts = [pt(i) for i in range(len(names))]
        for a, b in meta.skeleton:
            pa, pb = pts[idx[a]], pts[idx[b]]
            if pa and pb:
                cv2.line(frame, pa, pb, _BONE_COLOUR, 2, cv2.LINE_AA)
        for p in pts:
            if p:
                cv2.circle(frame, p, 3, _KP_COLOUR, -1, cv2.LINE_AA)
        shown = sum(p is not None for p in pts)
        return f"LP {shown}/{len(names)} keypoints >= {thr:.2f}"

    def _draw_masks(self, frame, segs, labels) -> str:
        counts: dict[str, int] = {}
        overlay = frame.copy()
        drawn = segs[: self._max_labels]
        for s in drawn:
            name = labels[s.category] if s.category < len(labels) else str(s.category)
            counts[name] = counts.get(name, 0) + 1
            colour = _PALETTE[s.category % len(_PALETTE)]
            overlay[s.mask.astype(bool)] = colour
        # blend the fills, then draw crisp boxes + labels on top
        cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)
        for s in drawn:
            name = labels[s.category] if s.category < len(labels) else str(s.category)
            colour = _PALETTE[s.category % len(_PALETTE)]
            x, y, w, h = s.box
            cv2.rectangle(frame, (x, y), (x + w, y + h), colour, 1, cv2.LINE_AA)
            cv2.putText(frame, f"{name} {int(s.score * 100)}%", (x, max(12, y - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)
        return "  ".join(f"{n}x {k}" for k, n in
                         sorted(counts.items(), key=lambda kv: -kv[1])) or "nothing"

    def _draw_depth(self, frame, results) -> str:
        if not results:
            return "no depth"
        cv2.addWeighted(results[0].colored, 0.7, frame, 0.3, 0, frame)
        return "depth map"

    # ── commands / checks ────────────────────────────────────────────────────

    @command()
    def list_hailo_models(self) -> dict:
        """Return the curated model list + the module's current selection for
        the config card's 'AI' tab dropdown."""
        return {"models": CURATED_MODELS, "current": self._model_key}

    # Uploaded HEFs. The web UI's upload stages the file on the controller
    # (web.py, upload_hef_*), then sends fetch_hef; replies to all three come
    # back to the AI tab as hef_list_response.

    @command()
    def list_hefs(self) -> dict:
        """Uploaded HEFs for the AI tab's model picker (the curated stock nets
        are listed by the frontend itself), and whether a Hailo accelerator is
        fitted at all."""
        hefs = []
        try:
            names = sorted(os.listdir(USER_HEF_DIR))
        except OSError:
            names = []
        for name in names:
            path = os.path.join(USER_HEF_DIR, name)
            if name.endswith(".hef") and os.path.isfile(path):
                hefs.append({"name": name, "path": path, "source": "uploaded",
                             "size_bytes": os.path.getsize(path),
                             "model": self._hef_model_info(path)})
        return {
            "hailo_present": os.path.exists(HAILO_DEVICE),
            "hefs": hefs,
            "current": self.config.get("hailo.model", DEFAULT_MODEL),
        }

    @staticmethod
    def _hef_model_info(hef_path: str) -> dict:
        """What the AI tab shows for an uploaded HEF: an LP model's metadata
        summary (or why its sidecar is invalid), or plain detection."""
        side = sidecar_path(hef_path)
        if not os.path.exists(side):
            return {"type": "detection"}
        try:
            m = load_meta(side)
        except LPModelError as e:
            return {"type": LP_MODEL_TYPE, "error": str(e)}
        return {"type": LP_MODEL_TYPE, "keypoint_names": list(m.keypoint_names),
                "skeleton": [list(e) for e in m.skeleton],
                "input_size": list(m.input_size), "heatmap_stride": m.heatmap_stride,
                "source_frame_size": list(m.source_frame_size) if m.source_frame_size else None}

    @command()
    def set_hef_metadata(self, filename: str = "", metadata=None) -> dict:
        """Save (or with metadata=None remove) an uploaded HEF's model
        metadata sidecar: the <name>.json tools/convert_lp_pose_to_hailo.py
        writes. Validated here, so a bad file is refused at upload rather than
        at model load."""
        if not HEF_NAME_RE.match(filename or ""):
            return {**self.list_hefs(), "error": f"Invalid HEF filename: {filename!r}"}
        hef = os.path.join(USER_HEF_DIR, filename)
        if not os.path.isfile(hef):
            return {**self.list_hefs(), "error": f"{filename} is not uploaded"}
        side = sidecar_path(hef)
        if self._recording_active() and filename == self._model_key:
            return {**self.list_hefs(), "error": "Recording: can't change the model in use"}
        try:
            if metadata is None:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(side)
            else:
                meta = validate_meta(metadata)
                tmp = side + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(meta.to_dict(), f, indent=2)
                os.replace(tmp, side)
        except LPModelError as e:
            return {**self.list_hefs(), "error": f"Model metadata refused: {e}"}
        except OSError as e:
            return {**self.list_hefs(), "error": f"Couldn't save model metadata: {e}"}
        self.logger.info(f"{'Removed' if metadata is None else 'Saved'} model metadata for {filename}")
        if filename == self._model_key:
            self._rebuild_detector_async()
        return {**self.list_hefs(), "metadata_saved": filename}

    @command()
    def fetch_hef(self, controller_url: str = "", filename: str = "") -> dict:
        """Download an uploaded HEF from the controller into USER_HEF_DIR.
        Returns the refreshed list_hefs() plus `fetched` (the filename, which
        is what `hailo.model` is set to for it)."""
        if not HEF_NAME_RE.match(filename or ""):
            return {**self.list_hefs(), "error": f"Invalid HEF filename: {filename!r}"}
        if not controller_url:
            return {**self.list_hefs(), "error": "controller_url is required"}
        try:
            size = download_hef(controller_url, filename, USER_HEF_DIR)
        except Exception as e:
            self.logger.error(f"fetch_hef {filename} failed: {e}")
            return {**self.list_hefs(), "error": f"Download failed: {e}"}
        self.logger.info(f"Fetched HEF {filename} ({size // 1024} KiB) to {USER_HEF_DIR}")
        if filename == self._model_key:
            # Replaced the HEF that's loaded: reload it.
            self._rebuild_detector_async()
        return {**self.list_hefs(), "fetched": filename}

    @command()
    def delete_hef(self, filename: str = "") -> dict:
        """Remove an uploaded HEF. Refused for the selected model; pick
        another one first."""
        if not HEF_NAME_RE.match(filename or ""):
            return {**self.list_hefs(), "error": f"Invalid HEF filename: {filename!r}"}
        if filename == self.config.get("hailo.model", DEFAULT_MODEL):
            return {**self.list_hefs(), "error": "That model is selected; choose another and save first"}
        try:
            os.remove(os.path.join(USER_HEF_DIR, filename))
            with contextlib.suppress(FileNotFoundError):
                os.remove(sidecar_path(os.path.join(USER_HEF_DIR, filename)))
        except OSError as e:
            return {**self.list_hefs(), "error": f"Delete failed: {e}"}
        self.logger.info(f"Deleted uploaded HEF {filename}")
        return self.list_hefs()

    @check()
    def _check_hailo(self) -> tuple[bool, str]:
        # Never blocks readiness for the demo models — the module records fine
        # without inference. A Lightning Pose model does block it: its
        # keypoints are the point of the recording.
        with self._det_lock:
            active = self.detector is not None
            model = self._model_key
            err = self._detector_error
        if self._lp_selected and self._infer_enabled():
            if not active:
                return False, f"Lightning Pose model {model} not running: {err or 'no model'}"
            if self._lp_fov_error:
                return False, self._lp_fov_error
            st, dec = self._infer_timing_stats(), self._pcts(self._decode_ms)
            timing = (f" — run p50 {st['p50']:.0f}ms, decode p50 {dec['p50']:.0f}ms"
                      if st and dec else "")
            return True, f"Lightning Pose keypoints active ({model}){timing}"
        if active:
            st = self._infer_timing_stats()
            timing = (f" — detect p50 {st['p50']:.0f}ms p99 {st['p99']:.0f}ms"
                      if st else "")
            return True, f"Hailo inference active ({model}){timing}"
        if not self._infer_enabled():
            return True, "Hailo inference disabled by config (plain camera)"
        return True, f"Hailo inference off ({err or 'no model'}) — recording still works"

    # ── lifecycle ────────────────────────────────────────────────────────────

    def stop(self) -> bool:
        """Tear the inference worker down BEFORE the detector so no frame is
        mid-detect() when the Hailo device closes (that's a hard SIGABRT).
        If the worker won't stop, skip the close — the process is exiting and
        the OS reclaims the device anyway."""
        if self._stop_inference_worker():
            with self._det_lock:
                det, self.detector = self.detector, None
                if det is not None:
                    try:
                        det.close()
                    except Exception:
                        pass
        return super().stop()


def main():
    camera = HailoCameraModule()
    camera.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        camera.stop()


if __name__ == "__main__":
    main()
