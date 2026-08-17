# Training the APA camera's rat detector

`apa_camera` uses a Hailo-accelerated object detector (`HailoDetector` in
`apa_camera_module.py`) to find the rat and drive shock-zone logic in
real time, with a frame-differencing `BlobTracker` as a config-switchable
fallback that needs no model at all. This page covers training and
deploying a custom detector — the `ratnet.pt` referenced throughout the
code and `tools/convert_to_hailo.py`'s defaults.

No trained weights or dataset ship with this repo — `ratnet.pt` is
something each lab trains for its own arena and lighting. There's nothing
here to reuse from history either: `refactor/ai_apa`, the branch this
feature was originally scoped on, was checked while writing this page and
only contains a stale copy of `convert_to_hailo.py` (path differences from
before the `variants/` rename, no dataset or training scripts) — there
was never any additional training material committed anywhere in this repo.

## Overview

```
record footage → extract frames → label → train (this page)
                                              ↓
                                        tools/convert_to_hailo.py
                                              ↓
                                    object_detection.model_path
```

Everything up to and including `train.py` lives in
`src/modules/variants/apa_camera/training/` (also see that folder's own
`README.md` for the terse version of this page). The conversion step
(`tools/convert_to_hailo.py`, repo root) already existed and is documented
in its own docstring — this page only summarizes it in [Step 5](#step-5-convert-to-a-hailo-hef).

## Step 1 — collect footage

Record normally with an `apa_camera` module — no special mode needed. One
important setting for the *source* recordings you'll extract training
frames from:

**Turn off `object_detection.enabled` and `shock_zone.shock_zone_display`
while collecting.** Both are drawn onto the recorded main stream, not just
the live preview (`_process_main_frame` → `_draw_detections` /
`_apply_shock_zone` in `apa_camera_module.py`), so footage recorded with
either on has a detection dot / shock-zone arc baked into the pixels —
exactly the kind of thing you don't want a new model learning to reproduce
as a feature of "rat." (The small per-frame timestamp overlay in the top
corner is fine to leave — a fixed, tiny watermark isn't something YOLO
training meaningfully latches onto.)

Aim for footage across different lighting conditions, times of day, and
however much the arena's visual background changes (bedding, occlusion from
water bottles, etc.) — variety here matters far more than raw frame count.

## Step 2 — extract candidate frames

```bash
cd src/modules/variants/apa_camera/training
python extract_frames.py --session-dir /path/to/session/date_dir \
    --out training_data/images --every-n-frames 10 --motion-only
```

`--motion-only` skips frames that look nearly identical to the previous
sampled one (simple mean-pixel-diff check) — a typical session is mostly
"nothing changed," and hand-labelling near-duplicates wastes effort without
adding useful training signal. Tune `--every-n-frames` and
`--motion-threshold` to land somewhere in the low hundreds to low thousands
of candidate frames per session — labelling is the bottleneck, not compute.

## Step 3 — label

Not something this repo provides tooling for — use an existing labelling
tool and export in YOLO format (one `.txt` per image:
`class x_center y_center width height`, all normalised 0–1). Reasonable
options:

- **[Roboflow](https://roboflow.com)** — hosted, has an assisted-labelling
  mode that speeds up later batches once a handful of frames are labelled,
  free tier is generally enough for a single-class dataset this size.
- **[CVAT](https://www.cvat.ai)** — self-hostable if you'd rather not put
  footage in a third-party cloud tool.
- **[LabelImg](https://github.com/HumanSignal/labelImg)** — simplest, fully
  local, no auto-assist.

Single class: `rat` (class index `0`). Include some frames with **no rat
visible at all** (still-empty arena) and no label file (or an empty one) —
negative examples matter for a detector that has to say "nothing here"
correctly, not just find the rat when it's there.

A rough starting point: **150–300 labelled frames**, biased toward a
higher fraction of "hard" cases (partial occlusion, corner of frame, rat
near arena edge/shock zone boundary) rather than easy centre-of-arena shots
— the easy cases are what the model is least likely to need help with.

## Step 4 — assemble the dataset and train

```
training_data/
  images/train/*.jpg   images/val/*.jpg
  labels/train/*.txt   labels/val/*.txt
```

An 80/20 or 85/15 train/val split is typical — split by *session*, not
randomly by frame, if you can, so validation frames aren't near-duplicates
of training frames from the same recording.

Copy `dataset.yaml.example` → `dataset.yaml`, point `path` at
`training_data/`, then:

```bash
python train.py --data training_data/dataset.yaml
```

Defaults to `yolo11n.pt` (smallest YOLO11 checkpoint, a good fit for
`hailo8l`) for 150 epochs at 640×640 — matching `convert_to_hailo.py`'s own
`--imgsz` default, since that has to match later. Produces
`ratnet.pt` in the `training/` folder.

Needs a GPU workstation or a Colab-style notebook — nothing about this step
touches Hailo or needs to run on the Pi. `--device cpu` works but will be
slow for more than a quick smoke-test run.

## Step 5 — convert to a Hailo HEF

From the repo root, on **x86-64 Linux** (the Hailo Dataflow Compiler
doesn't run on ARM — not on the Pi):

```bash
pip install "ultralytics>=8.3" hailo_dataflow_compiler onnx onnxruntime
python tools/convert_to_hailo.py \
    --model src/modules/variants/apa_camera/training/ratnet.pt \
    --calib-dir /path/to/64-128/representative/arena/images
```

`--calib-dir` matters for accuracy — supply real arena frames (a subset of
your labelled images works fine) rather than relying on the random-data
fallback. See that script's own docstring for the full option list
(`--imgsz`, `--hw-arch hailo8`/`hailo8l`, `--onnx-only` for a two-machine
split if your training machine isn't the same x86 box with the DFC
installed).

## Step 6 — deploy and verify

Copy the resulting `.hef` to the Pi and update the module config:

```json
"object_detection": {
    "enabled": true,
    "backend": "hailo",
    "model_path": "/path/to/your_model.hef",
    "threshold": 0.55
}
```

Watch the live MJPEG preview with detection on before trusting it in a real
session — the drawn detection dot (`_draw_detections`) makes it obvious at
a glance whether the box is landing on the rat consistently, including at
the arena edges and shock-zone boundary where accuracy matters most for
the actual shock logic. If it's noticeably worse than `BlobTracker`
(`object_detection.backend: "blob"`, the no-model fallback already in the
codebase) at holding lock on the rat, that's a sign the training set needs
more hard examples from exactly the failure cases you're seeing, not
necessarily more data overall.
