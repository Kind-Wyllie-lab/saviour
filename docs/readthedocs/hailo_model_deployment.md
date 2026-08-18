# Training a model and deploying it to Hailo

Every SAVIOUR module that does on-device inference on a Raspberry Pi AI
HAT / Hailo-8 ends up going through the same compile and deployment
mechanics, regardless of what the model actually detects. This page
covers the parts that are the same across any Hailo model. For the parts
that differ — dataset collection, labelling conventions, exact CLI
commands, config keys — see the model-specific page:

- **[APA Rat Detector Training](apa_rat_detector_training.md)** — object
  detection (YOLO), `apa_camera`
- **[Hailo Pose Conversion](hailo_pose_conversion.md)** — keypoint/pose
  (heatmap regression), `lightning_camera`

## Hardware: what actually runs where

The compiled `.hef` executes **on the Hailo chip itself** (the AI HAT's
Hailo-8L, or a full Hailo-8) — not on the Raspberry Pi's own CPU. The
Pi's job is camera capture, HailoRT orchestration, and pre/post-processing
glue, which costs roughly the same whichever model is deployed. What
actually varies model-to-model is the Hailo chip's own compute/memory
footprint and how well-trodden that model's Dataflow Compiler (DFC)
compile path is — not "how heavy it is to run on the Pi."

## Two task shapes, two very different risk profiles

This is the thing to understand before picking (or trusting) a model
architecture for a new module:

**Object detection (YOLO family)** predicts bounding boxes + class +
objectness across a multi-scale grid. Hailo's toolchain has first-class,
well-supported handling for this family — the compiled `.hef` bakes NMS
postprocessing **into the chip itself**, so what comes back to the Pi is
already a clean list of `(box, class, confidence)` with no decode step
needed beyond thresholding. `tools/convert_to_hailo.py` (repo root) is
this path, proven and working — see
[APA Rat Detector Training](apa_rat_detector_training.md).

**Keypoint/pose (heatmap regression)** predicts one heatmap channel per
keypoint — a per-pixel "how likely is this joint here" map — not a box.
There's no on-chip NMS equivalent; postprocessing instead means finding
each channel's peak (argmax, optionally sub-pixel refinement)
**host-side, after inference**, not baked into the `.hef`. This is a much
less standardized Hailo compile path with far fewer known-working
examples than YOLO's. It's exactly why
[Hailo Pose Conversion](hailo_pose_conversion.md) is currently flagged as
an unverified draft: the DFC compile shape is copied from the proven YOLO
tool, but a real heatmap-output model hasn't been pushed through it yet.

Practical consequence: expect a keypoint/pose conversion to need more
debugging at the DFC compile step, more careful calibration images (a
heatmap peak is more sensitive to exact pixel intensity than a box's
rough extent is), and treat the "verify the actual output shape" step
below as **mandatory, not optional** — heatmap decode has more ways to be
silently wrong (wrong channel order, `(K,H,W)` vs `(H,W,K)` layout) than
a detector's cleanly-typed box+class+score output does.

If you're deciding what architecture to use for a new module and the
underlying task is "where is a single point," a detection model is
usually still the pragmatic choice *because* of this tooling gap, not
because it's the purer fit for the task — see the reasoning in
[APA Rat Detector Training](apa_rat_detector_training.md)'s own notes on
why it uses a box detector for what's ultimately a centroid readout.

## Requirements

Same regardless of model type — run on **x86-64 Linux only**, the DFC
does not support ARM (so not the Pi itself):

```bash
pip install hailo_dataflow_compiler   # wheel from https://developer.hailo.ai
                                       # (free account needed to download it)
pip install onnx onnxruntime
```

Plus whatever framework produced your checkpoint (`ultralytics` for
YOLO, `torch`/`lightning-pose` for a Lightning Pose checkpoint, etc.) —
see the model-specific page.

## The general pipeline shape

```
checkpoint  →  ONNX  →  Hailo DFC (parse → quantize → compile)  →  .hef
```

Both conversion tools in this repo (`tools/convert_to_hailo.py` and
`tools/convert_lp_pose_to_hailo.py`) follow this same shape via the DFC's
`ClientRunner` API (`translate_onnx_model` → `optimize` → `compile`) —
the pose tool's compile step is copied directly from the proven detection
one, minus the detection-only NMS postprocessing config.

- `--hw-arch hailo8l` for the Raspberry Pi AI HAT, `hailo8` for a full
  Hailo-8 card.
- `--calib-dir`: 64–128 real images representative of the actual
  deployment setup (with and without the animal/subject) — quantization
  accuracy improves significantly over the random-data fallback, and
  matters even more for a heatmap model than a detector (see above).

## Verify before trusting it live

Two checks, in order, before any live use:

1. **Confirm the compiled `.hef`'s actual output shape** matches what
   your module's decode code assumes:
   ```bash
   hailortcli parse-hef path/to/model.hef
   ```
   Don't skip this for a heatmap model — if the DFC's postprocessing
   config produces something other than raw per-keypoint heatmaps, only
   the decode function needs to change, but you need to know that before
   it's running live.
2. **Watch it against the live MJPEG preview** with the feature enabled,
   before trusting it in a real session. Whatever overlay the module
   draws (detection dot, keypoint markers) makes accuracy obvious at a
   glance — including at the specific conditions that matter most for
   your use case (e.g. a shock-zone boundary, an occluded limb).

## Wiring into a module

The general pattern is the same across module types — copy the `.hef` to
the Pi and point the module's config at it (`enabled`, `backend: "hailo"`,
`model_path`, plus whatever else that feature's own config schema needs).
Exact keys differ per module — see the model-specific page.
