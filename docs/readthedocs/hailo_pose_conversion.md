# Converting a Lightning Pose model for the Hailo AI HAT

> **Status: draft / not yet hardware-verified.** This page describes the
> intended process for the `lightning_camera` module
> (`src/modules/variants/lightning_camera/`), written before a single-view
> model actually existed to compile. Steps 1 and 2 below need a real
> pass-through on real hardware before this stops being a draft — see the
> callouts marked ⚠️ for the specific parts most likely to need correcting.

## Why this isn't a one-step conversion

`lightning_camera` is built for **single-view 2D pose extraction on-device**,
one Hailo per camera. If you've been handed a **multiview** Lightning Pose
model (ingesting several synchronized camera views at once, doing cross-view
attention — e.g. a Lightning Pose 3D / transformer model), that model
**cannot go on a Hailo directly**: a Hailo runs one model on one stream on
one device, and there's no way to split a cross-view-attention graph across
cameras. See `src/modules/variants/lightning_camera/SHARE_lp3d_package/README.md`
for the full reasoning if you have that package.

The supported path — what this page walks through — is:

1. A **separate, single-view** 2D CNN pose model (ResNet/MobileNet backbone,
   *not* a multiview transformer) trained per camera angle, or one model
   generalising across the rig's camera positions.
2. Compiled per-camera to a Hailo `.hef`.
3. Each `lightning_camera` module runs its own camera's `.hef` locally, and
   writes its 2D keypoints to a CSV sidecar (`kp_<name>_x/y/conf` columns —
   see `LightningCameraModule.CSV_EXTRA_COLUMNS`).
4. 3D triangulation across cameras (if needed) happens **later, in
   software**, from the collected 2D CSVs plus a calibration file — not on
   the Hailo, and not built into the module itself yet (see the module's
   own docstring / CLAUDE.md's feature-ideas list for that follow-up).

## Step 1 — checkpoint → ONNX

Run this wherever the model was trained (needs `torch` + whatever
`lightning-pose` version produced the checkpoint — version matters, LP's
model-loading API has changed between releases):

```bash
python tools/convert_lp_pose_to_hailo.py export-onnx \
    --checkpoint path/to/model.ckpt \
    --config path/to/config.yaml \
    --out-dir path/to/out
```

This loads the checkpoint, wraps its forward pass so the traced graph
outputs **only the raw per-keypoint heatmap tensor** (no Lightning-Pose-side
postprocessing), and exports to ONNX at the resolution from
`config.yaml`'s `data.image_resize_dims`.

⚠️ **`load_lp_model()` in the script is a template.** It assumes a
`lightning_pose.models.HeatmapTracker`-shaped checkpoint. The first time you
run this against a real single-view checkpoint, expect to adjust the import
and loading call to match whatever class/API that specific `lightning-pose`
version actually exposes — check:
```bash
python -c "import lightning_pose; print(lightning_pose.__file__)"
```
and look at the `models` module for the right entry point. Everything else
in Step 1 (the wrapping, the `torch.onnx.export` call itself) is standard
and shouldn't need changes.

The script prints the ONNX graph's input/output shapes at the end — note
the output shape down, you'll need it in Step 3.

## Step 2 — ONNX → HEF

**Must run on x86-64 Linux** with the Hailo Dataflow Compiler (DFC)
installed — it does not run on ARM, so not on the Pi itself.

```bash
pip install hailo_dataflow_compiler   # wheel from https://developer.hailo.ai
pip install onnx onnxruntime opencv-python numpy

python tools/convert_lp_pose_to_hailo.py compile-hef \
    --onnx path/to/model.onnx \
    --num-keypoints 11 \
    --hw-arch hailo8l \
    --calib-dir path/to/calibration_images/
```

- `--hw-arch hailo8l` for the Raspberry Pi AI HAT; `hailo8` for a full
  Hailo-8.
- `--calib-dir`: 64–128 real frames from the actual camera/arena
  (with and without the animal) significantly improve quantization
  accuracy over the random-data fallback — more so for a heatmap model than
  a simpler detector, since the keypoint peak is sensitive to exact pixel
  intensities.

This mirrors the same parse → quantize → compile shape already proven for
object detection in `tools/convert_to_hailo.py`, minus that tool's
YOLO-specific NMS postprocessing step (a pose heatmap head doesn't need
one — just input normalization).

⚠️ **Not yet verified against a real DFC run.** The parse/quantize/compile
calls (`ClientRunner.translate_onnx_model` / `.optimize` / `.compile`) are
copied from the working YOLO tool, but a heatmap-output CNN hasn't actually
been pushed through this path yet. If the DFC errors partway through, that's
the part to debug first — the *shape* of the pipeline is right, the
model-specific details might not be.

## Step 3 — verify the HEF's actual output shape

Before trusting anything live, confirm what the compiled `.hef` actually
outputs and that it matches what `HailoPoseDetector._decode_heatmaps()` in
`lightning_camera_module.py` assumes: one heatmap channel per keypoint, as
either `(K, Hm, Wm)` or `(Hm, Wm, K)`, in the same order as
`pose_estimation.keypoint_names` in the module's config.

```bash
hailortcli parse-hef path/to/model.hef
```

If the real output differs — e.g. the DFC's postprocessing config instead
produces direct `(x, y, conf)` triplets rather than raw heatmaps — **only**
`_decode_heatmaps()` needs to change; nothing else in the module depends on
the decode internals.

## Step 4 — wire it into the module

Copy the `.hef` to the Pi (e.g. into
`src/modules/variants/lightning_camera/`) and update the module's config
(`lightning_camera_config.json`, or live via the web UI's config card once
one exists for this module type):

```json
"pose_estimation": {
    "enabled": true,
    "backend": "hailo",
    "model_path": "/usr/local/src/saviour/src/modules/variants/lightning_camera/your_model.hef",
    "keypoint_names": ["nose", "left_ear", "..."]
}
```

`keypoint_names` must be in the exact order the model was trained on —
it's what maps heatmap channel index → keypoint name for both the CSV
columns and the live overlay.

## Known gaps

- The `load_lp_model()` template (Step 1) and the DFC compile calls (Step 2)
  are both unverified against a real single-view checkpoint — see the ⚠️
  callouts above.
- 3D triangulation from the per-camera 2D CSVs (using the shared
  `SHARE_lp3d_package/calibration/*.toml`) is not built yet — this page and
  the module only cover single-camera 2D extraction.
- No CI/hardware test exercises this path — same caveat this project already
  applies to other camera-family changes (see CLAUDE.md's "Architectural
  concerns" section): treat this as needing an on-rig smoke test before a
  live experiment depends on it.
