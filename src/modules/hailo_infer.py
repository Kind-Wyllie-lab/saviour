#!/usr/bin/env python3
"""
Shared Hailo inference helpers.

Home of the picamera2 Hailo wrapper (`HailoDetector`) and the curated
stock-model registry used by the generic `hailo_camera` module. Kept out of any
one variant so both `apa_camera` (custom rat model) and `hailo_camera` (stock
model-zoo demo) import the same code path.

`HailoDetector` expects a model whose HEF has Hailo's NMS baked in — i.e. the
output is already `results[class_id] = ndarray(N, 5)` of
`[y_min, x_min, y_max, x_max, confidence]`, normalised 0–1. Every pre-compiled
detection HEF in the Hailo model zoo is like this, as is a HEF produced by this
repo's `tools/convert_to_hailo.py`. For a HEF that only emits raw YOLO tensors
(no on-chip NMS), see `apa_camera`'s `HailoRawDetector`.
"""

import cv2
import numpy as np

# Where download_hefs.sh drops the curated stock HEFs on a deployed module.
MODEL_DIR = "/usr/local/src/saviour/hailo_models"

# 80-class COCO label set (yolov8/yolov6/yolov5/yolox model-zoo detection HEFs).
COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]

# COCO 17-keypoint pose (yolov8*_pose model-zoo HEFs).
COCO_KP_NAMES = [
    "nose", "left_eye", "right_eye", "left_ear", "right_ear",
    "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
    "left_wrist", "right_wrist", "left_hip", "right_hip",
    "left_knee", "right_knee", "left_ankle", "right_ankle",
]
# Skeleton edges as keypoint-index pairs (COCO convention).
COCO_SKELETON = [
    (0, 1), (0, 2), (1, 3), (2, 4), (0, 5), (0, 6), (5, 6),
    (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]

# Curated selection surfaced in the module's "AI" config tab, grouped by
# `category`. `hef` is the filename download_hefs.sh fetches into MODEL_DIR
# (arch suffix is added at download time, not stored here). `task` selects the
# decoder + overlay in hailo_camera_module.py.
CURATED_MODELS = {
    "yolov8s": {
        "label": "YOLOv8s — balanced (default)",
        "category": "Object detection",
        "hef": "yolov8s.hef",
        "task": "detection",
        "labels": "coco",
    },
    "yolov6n": {
        "label": "YOLOv6n — fastest",
        "category": "Object detection",
        "hef": "yolov6n.hef",
        "task": "detection",
        "labels": "coco",
    },
    "yolov8m": {
        "label": "YOLOv8m — most accurate",
        "category": "Object detection",
        "hef": "yolov8m.hef",
        "task": "detection",
        "labels": "coco",
    },
    "yolov11n": {
        "label": "YOLOv11n — newest, fast",
        "category": "Object detection",
        "hef": "yolov11n.hef",
        "task": "detection",
        "labels": "coco",
    },
    "yolov8s_pose": {
        "label": "YOLOv8s-pose — body keypoints",
        "category": "Pose estimation",
        "hef": "yolov8s_pose.hef",
        "task": "pose",
    },
    "yolov8m_pose": {
        "label": "YOLOv8m-pose — keypoints, more accurate",
        "category": "Pose estimation",
        "hef": "yolov8m_pose.hef",
        "task": "pose",
    },
}

DEFAULT_MODEL = "yolov8s"


def labels_for(model_key: str) -> list[str]:
    spec = CURATED_MODELS.get(model_key, {})
    return COCO_LABELS if spec.get("labels") == "coco" else COCO_LABELS


class Detection:
    """A single object detection result."""
    def __init__(self, category: int, conf: float, box: tuple):
        self.category = category   # integer class index
        self.conf = conf           # confidence 0–1
        self.box = box             # (x, y, w, h) pixels on the original frame


class HailoDetector:
    """
    Wraps picamera2's Hailo integration for object detection.

    Expected model output (NMS baked into the HEF):
        results[class_id] = ndarray (N, 5): [y_min, x_min, y_max, x_max, confidence]
        all values normalised 0–1.
    """

    def __init__(self, hef_path: str, threshold: float = 0.5):
        from picamera2.devices.hailo import Hailo
        self._hailo = Hailo(hef_path)
        self._input_shape = self._hailo.get_input_shape()
        self._threshold = threshold

    @property
    def input_size(self) -> tuple:
        shape = self._input_shape
        if len(shape) == 4:
            return shape[1], shape[2]
        return shape[0], shape[1]

    def detect(self, frame: np.ndarray, labels: list[str]) -> list[Detection]:
        """Run inference on a BGR frame. Returns detections by descending confidence."""
        h, w = self.input_size
        rgb = cv2.cvtColor(cv2.resize(frame, (w, h)), cv2.COLOR_BGR2RGB)
        return self._decode(self._hailo.run(rgb), frame.shape, labels)

    def _decode(self, results, orig_shape: tuple, labels: list[str]) -> list[Detection]:
        detections = []
        oh, ow = orig_shape[:2]
        for class_id, class_dets in enumerate(results):
            if class_dets is None or len(class_dets) == 0:
                continue
            for det in class_dets:
                if len(det) < 5:
                    continue
                y1, x1, y2, x2 = float(det[0]), float(det[1]), float(det[2]), float(det[3])
                score = float(det[4])
                if score < self._threshold:
                    continue
                box = (int(x1 * ow), int(y1 * oh),
                       int((x2 - x1) * ow), int((y2 - y1) * oh))
                detections.append(Detection(
                    class_id if class_id < len(labels) else 0, score, box
                ))
        detections.sort(key=lambda d: d.conf, reverse=True)
        return detections

    def set_threshold(self, threshold: float) -> None:
        self._threshold = threshold

    def close(self):
        self._hailo.close()


class PoseResult:
    """One detected person's pose."""
    def __init__(self, box: tuple, score: float, keypoints: list):
        self.box = box                 # (x, y, w, h) pixels
        self.score = score             # person confidence 0–1
        self.keypoints = keypoints     # list[(x, y, conf)] in COCO_KP_NAMES order


class HailoPoseDetector:
    """
    Wraps picamera2's Hailo integration for yolov8*-pose (17 COCO keypoints).

    The model-zoo pose HEFs bake NMS + pose decode in, so `Hailo.run()` returns
    one row per person of [y1, x1, y2, x2, score, then 17*(kx, ky, kconf)] with
    box + keypoint coords normalised 0–1. The exact nesting picamera2 hands back
    varies a little by version, so _decode is defensive about wrapper lists and
    a possible split (boxes, scores, keypoints) layout.

    NOT verified against a real run yet — if the shape differs, only _decode
    changes.
    """

    _KP = 17
    _ROW = 5 + _KP * 3   # 56

    def __init__(self, hef_path: str, threshold: float = 0.3):
        from picamera2.devices.hailo import Hailo
        self._hailo = Hailo(hef_path)
        self._input_shape = self._hailo.get_input_shape()
        self._threshold = threshold

    @property
    def input_size(self) -> tuple:
        shape = self._input_shape
        if len(shape) == 4:
            return shape[1], shape[2]
        return shape[0], shape[1]

    def detect(self, frame: np.ndarray, labels=None) -> list[PoseResult]:
        h, w = self.input_size
        rgb = cv2.cvtColor(cv2.resize(frame, (w, h)), cv2.COLOR_BGR2RGB)
        return self._decode(self._hailo.run(rgb), frame.shape)

    def _rows(self, raw):
        """Best-effort reduction of picamera2's pose output to a 2-D array of
        [y1,x1,y2,x2,score, 17*(x,y,c)] rows."""
        # Unwrap single-element / per-class wrapper lists.
        node = raw
        for _ in range(3):
            if isinstance(node, (list, tuple)) and len(node) == 1:
                node = node[0]
            else:
                break
        arr = np.asarray(node, dtype=np.float32)
        if arr.ndim == 3 and arr.shape[0] == 1:
            arr = arr[0]
        if arr.ndim == 2 and arr.shape[1] >= self._ROW:
            return arr
        # Split layout: raw == (boxes[N,5], keypoints[N,17,3]) or similar.
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            try:
                boxes = np.asarray(raw[0], dtype=np.float32).reshape(-1, 5)
                kpts = np.asarray(raw[-1], dtype=np.float32).reshape(len(boxes), self._KP * 3)
                return np.hstack([boxes, kpts])
            except Exception:
                pass
        return np.empty((0, self._ROW), dtype=np.float32)

    def _decode(self, raw, orig_shape: tuple) -> list[PoseResult]:
        oh, ow = orig_shape[:2]
        out: list[PoseResult] = []
        for row in self._rows(raw):
            score = float(row[4])
            if score < self._threshold:
                continue
            y1, x1, y2, x2 = (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
            box = (int(x1 * ow), int(y1 * oh), int((x2 - x1) * ow), int((y2 - y1) * oh))
            kps = []
            for k in range(self._KP):
                base = 5 + k * 3
                kx, ky, kc = float(row[base]), float(row[base + 1]), float(row[base + 2])
                # keypoint coords are normalised the same way as the box
                kps.append((int(kx * ow), int(ky * oh), kc))
            out.append(PoseResult(box, score, kps))
        out.sort(key=lambda p: p.score, reverse=True)
        return out

    def set_threshold(self, threshold: float) -> None:
        self._threshold = threshold

    def close(self):
        self._hailo.close()
