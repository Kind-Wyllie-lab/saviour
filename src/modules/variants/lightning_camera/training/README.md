# Training a single-view pose model for lightning_camera

This folder covers getting from hand-labelled data to a trained Lightning
Pose checkpoint. `tools/convert_lp_pose_to_hailo.py` (repo root) then
compiles that checkpoint into the `.hef` `lightning_camera_module.py`'s
`HailoPoseDetector` runs on-device — see
[`docs/readthedocs/hailo_pose_conversion.md`](../../../../docs/readthedocs/hailo_pose_conversion.md)
for that step. As of 2026-08-26 that conversion script is still a
self-documented unverified template — nobody has run it against a real
checkpoint yet, since until now no single-view checkpoint existed at all.

## Why a separate single-view model

The multiview 3D checkpoint in `SHARE_lp3d_package` takes all camera streams
at once and can't run on Hailo (Hailo runs one model per video stream, not a
multiview transformer). A single-view 2D model — one frame in, this
module's 11 keypoints out — is the piece that's actually deployable, and
until this data drop, none existed.

## The data

`LP3D_hailo_extras` (a companion drop to `SHARE_lp3d_package`, not part of
this git repo — kept wherever your copy lives) contains real hand
annotations, DeepLabCut/Lightning-Pose CSV format:

```
training_labels/
  CollectedData_cam1.csv ... CollectedData_cam9.csv   92 labelled rows each
  frames/cam1/ ... frames/cam9/                        the matching 92 JPGs
labelled_videos/
  junetest_cam1_labeled.mp4 ...                        OLD MODEL predictions
                                                        overlaid, not ground
                                                        truth -- eyeball
                                                        sanity check only
```

All 9 cameras share the same 92 synchronised timepoints (confirmed by
diffing the frame-index sets across CSVs) — pick whichever camera matches
the physical Hailo-equipped Pi you actually intend to deploy to; the other
8 views' labels are there if a different camera turns out to be the better
choice, or if a future multiview-aware training pass becomes worth doing.

92 frames is a modest but workable dataset for transfer learning from a
pretrained backbone — not a large dataset by pose-estimation standards, so
don't expect state-of-the-art accuracy from a first pass. Real accuracy
here is genuinely unknown until it's actually trained and checked, not
something either this README or the prep script can promise.

## Pipeline

1. **Assemble the project.** `prepare_lp_project.py` copies the labelled
   frames + CSV into the on-disk layout Lightning Pose expects, and writes
   a `config.yaml` (keypoint list read from `lightning_camera_config.json`
   directly, so it can't drift out of sync with the module):
   ```bash
   pip install -r requirements.txt   # pyyaml only, for this step
   python prepare_lp_project.py \
       --source-dir "/path/to/LP3D_hailo_extras" \
       --camera cam1 \
       --out ~/lp_projects/cam1 \
       --copy-eval-video
   ```
   Run this **on the machine you'll actually train on** — `config.yaml`
   bakes in absolute paths (`data_dir`, `video_dir`) for wherever it was
   run; copying the assembled project to a different machine afterward
   means hand-fixing those paths first.

2. **Train.** Needs a real `lightning-pose` install (GPU workstation, not
   the Pi — same split as the apa_camera rat-detector's own training/GPU
   step):
   ```bash
   pip install -r requirements.txt   # now installs lightning-pose too
   litpose train ~/lp_projects/cam1/config.yaml --output_dir ~/lp_projects/cam1/outputs
   ```
   Produces a `.ckpt` under `outputs/`. `--resize-height`/`--resize-width`
   (must both be multiples of 128) and `--backbone` are the two prep-script
   flags most likely worth revisiting based on how a first pass looks —
   defaults were chosen to approximate the real frames' aspect ratio and to
   start from an animal-pose-pretrained backbone, not independently
   benchmarked against alternatives.

3. **Sanity-check before converting anything.** Lightning Pose's own
   post-training video prediction (`eval.predict_vids_after_training`,
   already on in the generated config) overlays predicted keypoints on
   `video_dir`'s video(s) — watch that output before spending time on Hailo
   conversion. A model that clearly isn't tracking the animal at this stage
   isn't going to get better by compiling it.

4. **Convert + deploy** — see
   [`docs/readthedocs/hailo_pose_conversion.md`](../../../../docs/readthedocs/hailo_pose_conversion.md).
   Expect this step to need real debugging: the equivalent detection-model
   conversion (`tools/convert_to_hailo.py`, apa_camera's rat detector) took
   several rounds of trial and error against the Hailo Dataflow Compiler
   before parsing succeeded, and is *still* not fully working through
   quantization as of this writing — and the docs for the pose path
   specifically flag it as less standardized than the detection path (no
   on-chip NMS equivalent for heatmaps, fewer known-working examples).
