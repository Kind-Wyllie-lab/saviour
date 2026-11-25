# Running a Lightning Pose model on the Hailo (hailo_camera)

> **Status:** the decoder is tested against Lightning Pose's own code paths
> (v2.4.4). The converter and the on-Pi path have not yet been run on a real
> trained checkpoint, the Hailo DFC or a Hailo camera. The first real model
> is the next step.

`hailo_camera` can run a **single-view** Lightning Pose (LP) heatmap model on
every frame and write the keypoints into the recording's timestamp CSV. The
general Hailo conversion mechanics (x86-64-only DFC, calibration, `--hw-arch`)
are in [Training a model and deploying it to Hailo](hailo_model_deployment.md).
This page covers what is specific to LP.

## What can run

Only an LP `heatmap` model (one frame in, one heatmap per keypoint out) with a
CNN backbone (ResNet, etc.). Context models (`heatmap_mhcrnn`) and multiview
transformers can't: a Hailo runs one model on one stream, and cross-view
attention has no single-camera equivalent. For 3D, run one single-view model
per camera and triangulate the CSVs afterwards.

## The .hef contract

| | |
|---|---|
| input | `(H, W, 3)` uint8, **BGR**, a plain (stretch) resize of the frame to LP's `image_resize_dims`, with `cv2.INTER_LINEAR` like LP's `predict_frame` |
| normalisation | LP's ImageNet mean/std, in BGR order, as the `.hef`'s normalization layer |
| output | one layer, `(H/s, W/s, K)` channels-last (HailoRT's layout), **raw heatmaps before LP's final softmax**, `s = 2**downsample_factor` |
| reading | as FLOAT32, so HailoRT applies the quantisation scale and zero-point, for 8- and 16-bit output layers alike |

picamera2's `RGB888` buffers are B, G, R in memory, and hailo_camera hands
the model that buffer unchanged. LP trains on RGB, so the converter folds the
channel swap into the first convolution's weights. That is exact and costs
nothing per frame. The final spatial softmax is left out of the graph, because
it squashes every value to around 1e-4, which int8 can't represent. The Pi
applies it in NumPy instead.

At load time the module checks the HEF's real input and output shapes against
the sidecar and refuses a mismatch; it never guesses a layout.

## Decoding (on the Pi, `src/modules/lp_decode.py`)

This reproduces LP v2.4.x `HeatmapHead.forward` + `run_subpixelmaxima` +
`model_to_frame_batch`:

1. Spatial softmax, temperature 1 (the head's `final_softmax`).
2. Upsample ×2, `downsample_factor` times: bicubic (`align_corners=False`),
   then kornia's 5×5 pyramid Gaussian with a zero border.
3. Spatial softmax of `heatmap × 1000`.
4. Expected x, y over the pixel-index grid, minus 0.5 / 1.5 / 2.5 for factor
   1 / 2 / 3.
5. Confidence = the softmax summed over the 5×5 window at the truncated
   location (LP's `evaluate_heatmaps_at_location`).
6. `x · frame_w / W`, `y · frame_h / H`: recorded-video pixels.

Two LP quirks are reproduced on purpose. At factor 3, LP's offset should be
3.5, so its keypoints are +1 input px on both axes. At factor 1, a single
bicubic upsample leaves up to ~0.2 px of sub-pixel bias. Factor 2 (the default)
is unbiased.

`hailo.lp_decode`:

- `exact` (the default) is the above, done with OpenCV. On a Pi 5, single
  thread, K = 11, it takes 9.5 ms at a 256×256 input, 21 ms at 384×384,
  37 ms at 512×512 and 65 ms at 512×896.
- `windowed` does steps 2–5 only around each heatmap's peak: 2.4 ms at 256²
  and 5.6 ms at 512×896. It is within 0.01 px of exact wherever LP's
  confidence is ≥ 0.3. Below that the two differ, because LP's full-map
  expectation drifts toward the image centre on weak or two-peaked heatmaps.

Pick an input size whose decode fits the frame interval, or use `windowed`.

## Step 1: checkpoint → ONNX (where lightning-pose is installed)

```bash
python tools/convert_lp_pose_to_hailo.py export-onnx \
    --checkpoint outputs/.../model.ckpt --config outputs/.../config.yaml \
    --out-dir out/
```

This loads the checkpoint the way LP does (`load_model_from_checkpoint`), then
builds the export from the backbone and the head's upsampling layers only.
Before writing anything it checks:

- the installed LP's dataset transform still normalises with
  `_IMAGENET_MEAN/_IMAGENET_STD` (it aborts if not);
- on real labelled frames, softmax(export, fed BGR) equals LP's forward
  (fed RGB);
- the ONNX output is `(1, K, H/s, W/s)`, matches torch, and is **not**
  non-negative-summing-to-1 per channel (which would mean the softmax crept
  back in).

It writes `<stem>.onnx` and `<stem>.lpmeta.json`. The skeleton comes from
`src/modules/variants/hailo_camera/training/rat_keypoints.json` (`--skeleton`).

## Step 2: ONNX → HEF (x86-64 + Hailo DFC)

```bash
python tools/convert_lp_pose_to_hailo.py compile-hef \
    --onnx out/model.onnx --hw-arch hailo8l \
    --calib-dir frames/session_a frames/session_b frames/session_c
```

`--calib-dir` is required, with at least 64 frames. Sample them across several
sessions and lighting conditions, including the animal in unusual postures
and near the walls. Each directory is searched recursively and sampled evenly.
The frames are fed BGR, like the Pi will. `--output-16bit` asks for 16-bit
activations on the heatmap layer; it is not yet tried on a real DFC run. This
step writes `<stem>.hef`, `<stem>_quantized.har` and the `<stem>.json`
sidecar.

## Step 3: check quantisation before anything goes on a Pi

```bash
python tools/convert_lp_pose_to_hailo.py eval-quant \
    --onnx out/model.onnx --har out/model_quantized.har \
    --labels lp_project/CollectedData.csv --frames-root lp_project \
    --split-from outputs/.../predictions.csv     # keeps LP's held-out "test" rows
```

This runs the float ONNX (onnxruntime) and the quantised HAR (DFC emulator)
on the same frames, decodes both with the Pi's decoder, and prints per-keypoint
pixel error: float vs labels, quantised vs labels, quantised vs float. If
quantisation adds error comparable to the float model's own, try
`--output-16bit`, more varied calibration frames, or quantisation-aware
fine-tuning.

## Step 4: deploy

On the camera's config card, open the **AI** tab. Upload the `.hef`, then
**Load model info (.json)…** with the sidecar, and Save. The module checks:

- the HEF shapes against the sidecar;
- that the preview stream the model sees has the recorded stream's aspect
  (both are scaled from the same sensor crop, so equal aspect means the same
  field of view, and keypoints map by scaling);
- if the sidecar has `source_frame_size`, that the recording has the training
  frames' aspect.

A failure leaves the module **not ready**, with the reason on the preview and
in its health check.

Settings: `hailo.lp_infer_every_n` (default 1), `hailo.lp_decode`
(`exact`/`windowed`), `hailo.threshold` (overlay only).

## What lands in the CSV

`kp_<name>_x`, `kp_<name>_y` (3 decimals, recorded-video pixels) and
`kp_<name>_conf` (4 decimals) for each keypoint, after the base timestamp
columns. Each row carries the result of **its own frame**: the frame is
queued with its number and timestamp, and its row is held back until that
result arrives. Rows get empty values (never the previous result) when:

- the frame was skipped by `lp_infer_every_n`;
- the worker fell behind and a newer frame replaced it in the queue
  ("dropped");
- the result didn't arrive within 1 s ("late");
- inference failed.

A log line every 30 s gives Hailo run and decode timings and how many frames
were inferred, skipped, dropped, late and failed (WARNING when any were
dropped, late or failed).

## Testing against LP itself

`tools/lp_reference_outputs.py` runs on the training box:

- `synthetic` puts hand-made heatmaps through LP's own code;
- `model --checkpoint ... --config ...` saves a real model's pre-softmax
  heatmaps plus LP's keypoints and confidences.

Copy the `.npz` into `src/modules/tests/fixtures/`. Then
`pytest src/modules/tests/test_lp_decode.py` compares the decoder against it.
The fixture shipped in the repo
(`lp_reference_synthetic_transcription.npz`) came from a torch + kornia
transcription of `run_subpixelmaxima`, not from LP itself; replace it with a
real `synthetic` run.
