#!/usr/bin/env python3
"""
apa_track_assess.py -- run several rat trackers over every frame of APA
recordings and save per-frame results, to assess tools/apa_track.py (and a
live-capable variant of it) without hand-labelled ground truth.

Per frame, from one decode pass:

  offline  apa_track.RatTracker against a median background of the whole
           video (what apa_track.py does; needs the finished recording)
  causal   the same detector against a rolling median of the last
           CAUSAL_WINDOW_S of frames only, i.e. what a live tracker could do
  hood     an independent dark-pixel detector (hooded rats' black hood),
           sharing no code or background with the two above
  legacy   the apa_camera module's live BlobTracker (frame differencing),
           with the session's blob_tracker config

Writes <out>/<session>.npz; tools/apa_track_report.py turns a folder of them
into the report and contact sheets for visual checking.

    python3 tools/apa_track_assess.py --out DIR [--jobs 3] <date_dir> [...]
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time
import types
from collections import deque
from concurrent.futures import ProcessPoolExecutor

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apa_track  # noqa: E402 -- path set just above

CAUSAL_WINDOW_S = 60.0   # 2 rpm turns the floor once per 30 s; cover two turns
CAUSAL_SAMPLE_S = 2.0    # one background sample every N seconds
CAUSAL_MIN_SAMPLES = 8   # no causal detections until the window has this many
HOOD_THRESHOLD = 45      # blurred grey level counted as "hood black"
HOOD_MIN_AREA = 60       # px at apa_track.PROC_SIZE


def _legacy_tracker_class():
    """The module's BlobTracker, importable off-camera by stubbing picamera2."""
    class _Stub(types.ModuleType):
        def __getattr__(self, name):
            return type(name, (), {})
    for m in ("picamera2", "picamera2.encoders", "picamera2.outputs",
              "picamera2.devices", "libcamera"):
        sys.modules.setdefault(m, _Stub(m))
    src = os.path.join(HERE, "..", "src")
    sys.path.insert(0, src)
    sys.path.insert(0, os.path.join(src, "modules", "variants", "apa_camera"))
    import apa_camera_module
    return apa_camera_module.BlobTracker


def hood_detect(grey: np.ndarray, arena: np.ndarray, kern) -> tuple[float, float, int] | None:
    m = ((grey < HOOD_THRESHOLD) & arena).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kern)
    n, _, stats, cents = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n < 2:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    area = int(stats[k, cv2.CC_STAT_AREA])
    if area < HOOD_MIN_AREA:
        return None
    return float(cents[k][0]), float(cents[k][1]), area


def assess_video(date_dir: str, out_dir: str) -> str:
    date_dir = date_dir.rstrip("/")
    session = os.path.basename(os.path.dirname(date_dir))
    out = os.path.join(out_dir, f"{session}.npz")
    if os.path.exists(out):
        return f"{session}: exists, skipped"
    cam_dir = apa_track.find_module_dir(date_dir, "apa_camera")
    cfg = apa_track.load_config(cam_dir)
    seg = apa_track.camera_segments(cam_dir)[0]
    stamps = np.array(apa_track.read_timestamps(seg.csv_path), dtype=np.int64)

    cap = cv2.VideoCapture(seg.video_path)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    geom = apa_track.zone_geometry(cfg, w, h)
    scale = apa_track.PROC_SIZE / max(w, h)
    size = (round(w * scale), round(h * scale))
    bg, _ = apa_track.background(seg.video_path, size)
    yy, xx = np.mgrid[:size[1], :size[0]]
    arena = np.hypot(xx - geom["cx"] * scale, yy - geom["cy"] * scale) <= geom["outer_r"] * scale
    rim = {"centre": (geom["cx"] * scale, geom["cy"] * scale), "radius": geom["outer_r"] * scale}
    offline = apa_track.RatTracker(bg, arena, 15, 150, 60.0, **rim)
    causal = apa_track.RatTracker(bg, arena, 15, 150, 60.0, **rim)
    bt_cfg = cfg.get("blob_tracker", {}) or {}
    try:
        legacy = _legacy_tracker_class()(**{k: v for k, v in bt_cfg.items()
                                            if not k.startswith("_")})
    except ImportError as e:  # off the Pi: the module's own deps (zmq...) may be missing
        print(f"{session}: legacy tracker unavailable ({e}); skipping it", flush=True)
        legacy = None
    hood_kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

    n = len(stamps)
    res = {k: np.full((n, 3), np.nan, np.float32) for k in ("offline", "causal", "hood", "legacy")}
    blobs = np.zeros(n, np.int16)      # offline candidate blobs >= min area
    second = np.zeros(n, np.float32)   # offline runner-up blob area
    det_ms = np.zeros(n, np.float32)   # offline detect() time per frame

    fps = 1e9 / np.median(np.diff(stamps[:500])) if n > 1 else 30.0
    samples: deque = deque(maxlen=int(CAUSAL_WINDOW_S / CAUSAL_SAMPLE_S))
    every = max(1, int(round(CAUSAL_SAMPLE_S * fps)))
    t_start = time.time()
    for i in range(n):
        ok, frame = cap.read()
        if not ok:
            n = i
            break
        grey = apa_track._prep(frame, size)

        t0 = time.perf_counter()
        d = offline.detect(grey)
        det_ms[i] = 1000 * (time.perf_counter() - t0)
        if d:
            res["offline"][i] = (d[0] / scale, d[1] / scale, d[2] / scale ** 2)
        blobs[i] = len(offline.areas)
        second[i] = offline.areas[1] / scale ** 2 if len(offline.areas) > 1 else 0

        if i % every == 0:
            samples.append(grey)
            if len(samples) >= CAUSAL_MIN_SAMPLES:
                causal.bg = np.median(np.stack(samples), axis=0).astype(np.uint8)
        if len(samples) >= CAUSAL_MIN_SAMPLES:
            d = causal.detect(grey)
            if d:
                res["causal"][i] = (d[0] / scale, d[1] / scale, d[2] / scale ** 2)

        d = hood_detect(grey, arena, hood_kern)
        if d:
            res["hood"][i] = (d[0] / scale, d[1] / scale, d[2] / scale ** 2)

        dets = legacy.detect(frame, ["rat"]) if legacy else None
        if dets:
            x, y, bw, bh = dets[0].box
            res["legacy"][i] = (x + bw / 2, y + bh / 2, np.nan)
    cap.release()

    np.savez_compressed(
        out, session=session, date_dir=date_dir, video=seg.video_path,
        ts=stamps[:n], geom=np.array([geom["cx"], geom["cy"], geom["outer_r"], geom["inner_r"],
                                      geom["start_deg"], geom["span_deg"]]),
        blobs=blobs[:n], second=second[:n], det_ms=det_ms[:n],
        **{k: v[:n] for k, v in res.items()})
    return f"{session}: {n} frames in {time.time() - t_start:.0f}s"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("date_dirs", nargs="+",
                    help="session date dirs; wildcards are expanded (for Windows shells) "
                         "and @FILE reads one per line")
    ap.add_argument("--root", default="", help="prefix for relative date dirs, e.g. the share")
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=3)
    args = ap.parse_args()
    dirs = []
    for arg in args.date_dirs:
        if arg.startswith("@"):
            with open(arg[1:]) as f:
                dirs += [line.strip() for line in f if line.strip()]
        else:
            dirs.append(arg)
    dirs = [os.path.join(args.root, d) for d in dirs]
    args.date_dirs = [m for d in dirs for m in (sorted(glob.glob(d)) if glob.has_magic(d) else [d])]
    os.makedirs(args.out, exist_ok=True)
    with ProcessPoolExecutor(args.jobs) as pool:
        futures = [pool.submit(assess_video, d, args.out) for d in args.date_dirs]
        for d, f in zip(args.date_dirs, futures):
            try:
                print(f.result(), flush=True)
            except Exception as e:  # noqa: BLE001 -- report and carry on with the batch
                print(f"{d}: failed ({e!r})", flush=True)


if __name__ == "__main__":
    main()
