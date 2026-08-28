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


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


def _as_prob(x: np.ndarray) -> np.ndarray:
    """Return `x` as a probability. Some Hailo-compiled YOLO heads bake the
    final sigmoid into the score / keypoint-visibility branch and some don't —
    a raw-logit tensor spans well outside [0, 1], an already-activated one does
    not. Double-sigmoiding an activated branch squashes the usable range so the
    confidence threshold stops discriminating (every cell reads ~0.5+), which is
    exactly the "hundreds of false people" failure. Detect and skip it."""
    if x.size and x.min() >= -1e-3 and x.max() <= 1.0 + 1e-3:
        return np.clip(x, 0.0, 1.0)
    return _sigmoid(x)

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
    "yolov8s_seg": {
        "label": "YOLOv8s-seg - instance masks",
        "category": "Instance segmentation",
        "hef": "yolov8s_seg.hef",
        "task": "segmentation",
        "labels": "coco",
    },
    "yolov8m_seg": {
        "label": "YOLOv8m-seg - masks, more accurate",
        "category": "Instance segmentation",
        "hef": "yolov8m_seg.hef",
        "task": "segmentation",
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


class PoseResult:
    """One detected person's pose."""
    def __init__(self, box: tuple, score: float, keypoints: list):
        self.box = box                 # (x, y, w, h) pixels
        self.score = score             # person confidence 0–1
        self.keypoints = keypoints     # list[(x, y, conf)] in COCO_KP_NAMES order


class HailoPoseDetector:
    """
    Wraps picamera2's Hailo integration for yolov8*-pose (17 COCO keypoints).

    The Hailo model-zoo yolov8*_pose HEFs ship the raw multi-scale head
    UNDECODED: `Hailo.run()` returns a dict of conv tensors, 3 scales x
    {box HxWx64 (DFL), score HxWx1, kpt HxWx51 (17x3)}. `_decode_raw` does the
    full YOLOv8-pose decode (DFL softmax boxes + sigmoid scores + kpt decode +
    NMS). _decode also keeps fallbacks for a HEF that instead emits per-person
    dicts or flat [y1,x1,y2,x2,score,17*(x,y,c)] rows.

    Verified shape (yolov8s_pose, zoo v2.14.0 / hailo8l): conv43-45 (80x80),
    conv57-59 (40x40), conv70-72 (20x20). Decode geometry not yet checked
    against real footage.
    """

    _KP = 17
    _ROW = 5 + _KP * 3   # 56
    _REG = 16            # DFL bins
    _INPUT = 640         # yolov8*_pose model-zoo HEFs are 640x640
    _MAX_PER_SCALE = 200  # pre-NMS cap per scale (CPU guard, see _decode_raw)

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

    # --- dict-shaped output (picamera2's Hailo pose wrapper returns per-person
    #     dicts, not flat rows) -------------------------------------------------
    _BOX_KEYS = ("bbox", "box", "boxes", "bboxes")
    _SCORE_KEYS = ("score", "confidence", "conf", "objectness", "detection_score")
    _KP_KEYS = ("keypoints", "kpts", "joints", "landmarks", "points")
    _KPSCORE_KEYS = ("joint_scores", "keypoint_scores", "kpt_scores", "scores")

    @staticmethod
    def _first(d: dict, keys):
        for k in keys:
            if k in d:
                return d[k]
        return None

    def _person_from_dict(self, d: dict, oh: int, ow: int):
        box_raw = self._first(d, self._BOX_KEYS)
        score = self._first(d, self._SCORE_KEYS)
        kps_raw = self._first(d, self._KP_KEYS)
        if box_raw is None or kps_raw is None:
            return None
        b = np.asarray(box_raw, dtype=np.float32).ravel()
        y1, x1, y2, x2 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
        # normalised (<=~1) vs already-pixel coords
        norm = max(abs(y1), abs(x1), abs(y2), abs(x2)) <= 2.0
        sy, sx = (oh, ow) if norm else (1.0, 1.0)
        box = (int(x1 * sx), int(y1 * sy), int((x2 - x1) * sx), int((y2 - y1) * sy))

        kp = np.asarray(kps_raw, dtype=np.float32)
        if kp.size in (self._KP * 2, self._KP * 3):
            kp = kp.reshape(self._KP, -1)
        elif kp.ndim == 1:
            kp = kp.reshape(-1, 2)
        kp_scores = self._first(d, self._KPSCORE_KEYS)
        kp_scores = np.asarray(kp_scores, dtype=np.float32).ravel() if kp_scores is not None else None

        kps = []
        for i in range(min(self._KP, len(kp))):
            row = kp[i].ravel()
            kx, ky = float(row[0]), float(row[1])
            kc = float(row[2]) if row.size >= 3 else (
                float(kp_scores[i]) if kp_scores is not None and i < len(kp_scores) else 1.0)
            kps.append((int(kx * sx), int(ky * sy), kc))
        while len(kps) < self._KP:
            kps.append((0, 0, 0.0))
        return PoseResult(box, float(score if score is not None else 1.0), kps)

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

    def _dicts(self, raw):
        """Return a flat list of per-person dicts if that's the shape, else []."""
        node = raw
        for _ in range(3):
            if isinstance(node, (list, tuple)) and len(node) == 1:
                node = node[0]
            else:
                break
        if isinstance(node, dict):
            # A single dict of parallel arrays: {'bbox':[N..], 'keypoints':[N..], ...}
            box = self._first(node, self._BOX_KEYS)
            kps = self._first(node, self._KP_KEYS)
            if box is not None and kps is not None:
                boxes = np.asarray(box, dtype=np.float32).reshape(-1, 4)
                kparr = np.asarray(kps, dtype=np.float32)
                kparr = kparr.reshape(len(boxes), -1)
                sc = self._first(node, self._SCORE_KEYS)
                sc = np.asarray(sc, dtype=np.float32).ravel() if sc is not None else np.ones(len(boxes))
                ksc = self._first(node, self._KPSCORE_KEYS)
                ksc = np.asarray(ksc, dtype=np.float32).reshape(len(boxes), -1) if ksc is not None else None
                return [
                    {"bbox": boxes[i], "score": float(sc[i]) if i < len(sc) else 1.0,
                     "keypoints": kparr[i], **({"joint_scores": ksc[i]} if ksc is not None else {})}
                    for i in range(len(boxes))
                ]
            return []
        if isinstance(node, (list, tuple)) and node and isinstance(node[0], dict):
            return list(node)
        return []

    # --- raw multi-scale YOLOv8-pose head (model-zoo HEFs ship undecoded) -----
    def _raw_scales(self, raw):
        """If `raw` is the dict of conv tensors a yolov8*_pose HEF emits, group
        it into {grid_size: {'box':HxWx64, 'score':HxWx1, 'kpt':HxWx51}}."""
        node = raw
        while isinstance(node, (list, tuple)) and len(node) == 1:
            node = node[0]
        if not isinstance(node, dict):
            return None
        scales: dict = {}
        for arr in node.values():
            a = np.asarray(arr, dtype=np.float32)
            if a.ndim != 3 or a.shape[0] != a.shape[1]:
                return None
            g, c = a.shape[0], a.shape[2]
            d = scales.setdefault(g, {})
            if c == 4 * self._REG:
                d["box"] = a
            elif c == 1:
                d["score"] = a
            elif c == self._KP * 3:
                d["kpt"] = a
        if not scales or any({"box", "score", "kpt"} - set(d) for d in scales.values()):
            return None
        return scales

    @staticmethod
    def _nms(boxes, scores, iou_thr=0.45):
        x1, y1, x2, y2 = boxes.T
        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]
        keep = []
        while order.size:
            i = order[0]
            keep.append(int(i))
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            w = np.clip(xx2 - xx1, 0, None)
            h = np.clip(yy2 - yy1, 0, None)
            inter = w * h
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-9)
            order = order[1:][iou <= iou_thr]
        return keep

    def _decode_raw(self, scales, orig_shape) -> list[PoseResult]:
        oh, ow = orig_shape[:2]
        bins = np.arange(self._REG, dtype=np.float32)
        all_box, all_sc, all_kp = [], [], []
        for g, d in scales.items():
            stride = self._INPUT / g
            gy, gx = np.mgrid[0:g, 0:g].astype(np.float32)
            gx, gy = gx.ravel(), gy.ravel()
            sc = _as_prob(d["score"].reshape(-1))
            keep = sc > self._threshold
            # Safety cap: a mis-scaled score branch can pass most of the grid
            # and wedge NMS + keypoint decode on the Pi CPU. Keep the strongest.
            if keep.sum() > self._MAX_PER_SCALE:
                cut = np.sort(sc[keep])[-self._MAX_PER_SCALE]
                keep &= sc >= cut
            if not keep.any():
                continue
            # box: DFL softmax over 16 bins per side -> l,t,r,b in grid units
            box = d["box"].reshape(-1, 4, self._REG)[keep]
            box = box - box.max(-1, keepdims=True)
            box = np.exp(box)
            box /= box.sum(-1, keepdims=True)
            dist = (box * bins).sum(-1)               # (K, 4)
            cx, cy = gx[keep] + 0.5, gy[keep] + 0.5    # anchor centres, grid units
            x1 = (cx - dist[:, 0]) * stride
            y1 = (cy - dist[:, 1]) * stride
            x2 = (cx + dist[:, 2]) * stride
            y2 = (cy + dist[:, 3]) * stride
            # keypoints: (K,17,3) -> pixels in the 640 frame
            kp = d["kpt"].reshape(-1, self._KP, 3)[keep]
            kx = (kp[:, :, 0] * 2.0 + gx[keep][:, None]) * stride
            ky = (kp[:, :, 1] * 2.0 + gy[keep][:, None]) * stride
            kv = _as_prob(kp[:, :, 2])
            all_box.append(np.stack([x1, y1, x2, y2], 1))
            all_sc.append(sc[keep])
            all_kp.append(np.stack([kx, ky, kv], -1))

        if not all_box:
            return []
        B = np.concatenate(all_box)
        S = np.concatenate(all_sc)
        Kp = np.concatenate(all_kp)
        rx, ry = ow / self._INPUT, oh / self._INPUT
        out: list[PoseResult] = []
        for i in self._nms(B, S):
            bx1, by1, bx2, by2 = B[i]
            box = (int(bx1 * rx), int(by1 * ry),
                   int((bx2 - bx1) * rx), int((by2 - by1) * ry))
            kps = [(int(Kp[i, j, 0] * rx), int(Kp[i, j, 1] * ry), float(Kp[i, j, 2]))
                   for j in range(self._KP)]
            out.append(PoseResult(box, float(S[i]), kps))
        out.sort(key=lambda p: p.score, reverse=True)
        return out

    def _decode(self, raw, orig_shape: tuple) -> list[PoseResult]:
        oh, ow = orig_shape[:2]
        out: list[PoseResult] = []

        scales = self._raw_scales(raw)
        if scales is not None:
            return self._decode_raw(scales, orig_shape)

        dicts = self._dicts(raw)
        if dicts:
            for d in dicts:
                p = self._person_from_dict(d, oh, ow)
                if p is not None and p.score >= self._threshold:
                    out.append(p)
            out.sort(key=lambda p: p.score, reverse=True)
            return out

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


class SegResult:
    """One instance-segmentation result."""
    def __init__(self, box: tuple, score: float, category: int, mask: np.ndarray):
        self.box = box            # (x, y, w, h) pixels on the original frame
        self.score = score        # confidence 0-1
        self.category = category  # integer class index
        self.mask = mask          # uint8 {0,1}, original-frame resolution


class HailoSegDetector:
    """
    Wraps picamera2's Hailo integration for yolov8*-seg (COCO instance masks).

    Like the pose HEFs, the model-zoo yolov8*_seg HEFs ship the raw multi-scale
    head UNDECODED: `Hailo.run()` returns a dict of conv tensors -- per scale
    {box HxWx64 (DFL), cls HxWxNC, mask-coeff HxWx32} plus one prototype tensor
    (~160x160x32). Mask for a kept detection = sigmoid(proto @ coeffs), cropped
    to the box, thresholded, upsampled to the frame. If the HEF instead ships a
    decoded output this returns [] (caller falls back to a plain preview).
    """

    _REG = 16            # DFL bins
    _NM = 32             # mask prototypes
    _INPUT = 640         # yolov8*_seg model-zoo HEFs are 640x640
    _MAX_PER_SCALE = 200
    _MAX_DET = 30        # post-NMS cap (mask assembly is the per-frame cost)
    _MASK_THR = 0.5

    def __init__(self, hef_path: str, threshold: float = 0.4):
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

    def detect(self, frame: np.ndarray, labels=None) -> list[SegResult]:
        h, w = self.input_size
        rgb = cv2.cvtColor(cv2.resize(frame, (w, h)), cv2.COLOR_BGR2RGB)
        return self._decode(self._hailo.run(rgb), frame.shape)

    def _raw_scales(self, raw):
        """Group the conv-tensor dict into
        ({grid: {'box','cls','mc'}}, proto HxWx32) or (None, None)."""
        node = raw
        while isinstance(node, (list, tuple)) and len(node) == 1:
            node = node[0]
        if not isinstance(node, dict):
            return None, None
        scales: dict = {}
        proto = None
        for arr in node.values():
            a = np.asarray(arr, dtype=np.float32)
            if a.ndim != 3:
                return None, None
            # prototype tensor: a 32 dim + two equal large spatial dims
            if a.shape[0] == self._NM and a.shape[1] == a.shape[2]:
                proto = np.transpose(a, (1, 2, 0))          # CHW -> HWC
                continue
            if a.shape[2] == self._NM and a.shape[0] == a.shape[1] \
                    and a.shape[0] > self._INPUT // 8:
                proto = a
                continue
            if a.shape[0] != a.shape[1]:
                return None, None
            g, c = a.shape[0], a.shape[2]
            d = scales.setdefault(g, {})
            if c == 4 * self._REG:
                d["box"] = a
            elif c == self._NM:
                d["mc"] = a
            else:
                d["cls"] = a                                # NC channels
        if proto is None or not scales:
            return None, None
        if any({"box", "cls", "mc"} - set(d) for d in scales.values()):
            return None, None
        return scales, proto

    def _decode_raw(self, scales, proto, orig_shape) -> list[SegResult]:
        oh, ow = orig_shape[:2]
        mh, mw, _ = proto.shape
        proto_flat = proto.reshape(-1, self._NM)            # (mh*mw, 32)
        bins = np.arange(self._REG, dtype=np.float32)
        all_box, all_sc, all_cls, all_mc = [], [], [], []
        for g, d in scales.items():
            stride = self._INPUT / g
            gy, gx = np.mgrid[0:g, 0:g].astype(np.float32)
            gx, gy = gx.ravel(), gy.ravel()
            cls = _as_prob(d["cls"].reshape(-1, d["cls"].shape[2]))
            conf = cls.max(1)
            cid = cls.argmax(1)
            keep = conf > self._threshold
            if keep.sum() > self._MAX_PER_SCALE:
                cut = np.sort(conf[keep])[-self._MAX_PER_SCALE]
                keep &= conf >= cut
            if not keep.any():
                continue
            box = d["box"].reshape(-1, 4, self._REG)[keep]
            box = box - box.max(-1, keepdims=True)
            box = np.exp(box)
            box /= box.sum(-1, keepdims=True)
            dist = (box * bins).sum(-1)
            cx, cy = gx[keep] + 0.5, gy[keep] + 0.5
            x1 = (cx - dist[:, 0]) * stride
            y1 = (cy - dist[:, 1]) * stride
            x2 = (cx + dist[:, 2]) * stride
            y2 = (cy + dist[:, 3]) * stride
            all_box.append(np.stack([x1, y1, x2, y2], 1))
            all_sc.append(conf[keep])
            all_cls.append(cid[keep])
            all_mc.append(d["mc"].reshape(-1, self._NM)[keep])   # raw coeffs

        if not all_box:
            return []
        boxes = np.concatenate(all_box)
        scores = np.concatenate(all_sc)
        cids = np.concatenate(all_cls)
        coeffs = np.concatenate(all_mc)
        rx, ry = ow / self._INPUT, oh / self._INPUT
        out: list[SegResult] = []
        for i in HailoPoseDetector._nms(boxes, scores)[: self._MAX_DET]:
            m = _sigmoid(proto_flat @ coeffs[i]).reshape(mh, mw)
            # zero everything outside the box (in proto coords)
            px1 = int(np.clip(boxes[i, 0] * mw / self._INPUT, 0, mw))
            px2 = int(np.clip(np.ceil(boxes[i, 2] * mw / self._INPUT), 0, mw))
            py1 = int(np.clip(boxes[i, 1] * mh / self._INPUT, 0, mh))
            py2 = int(np.clip(np.ceil(boxes[i, 3] * mh / self._INPUT), 0, mh))
            cropped = np.zeros_like(m)
            cropped[py1:py2, px1:px2] = m[py1:py2, px1:px2]
            mask_bin = (cropped >= self._MASK_THR).astype(np.uint8)
            mask_full = cv2.resize(mask_bin, (ow, oh), interpolation=cv2.INTER_NEAREST)
            bx1, by1, bx2, by2 = boxes[i]
            box = (int(bx1 * rx), int(by1 * ry),
                   int((bx2 - bx1) * rx), int((by2 - by1) * ry))
            out.append(SegResult(box, float(scores[i]), int(cids[i]), mask_full))
        out.sort(key=lambda s: s.score, reverse=True)
        return out

    def _decode(self, raw, orig_shape: tuple) -> list[SegResult]:
        scales, proto = self._raw_scales(raw)
        if scales is not None:
            return self._decode_raw(scales, proto, orig_shape)
        return []

    def set_threshold(self, threshold: float) -> None:
        self._threshold = threshold

    def close(self):
        self._hailo.close()
