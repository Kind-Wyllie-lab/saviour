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

# Curated selection surfaced in the module's "AI" config tab, grouped by
# `category`. `hef` is the filename download_hefs.sh fetches into MODEL_DIR
# (arch suffix is added at download time, not stored here). Only the
# "detection" task has a decoder today; the registry shape leaves room for
# pose/segmentation/depth to slot in as more categories later.
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
