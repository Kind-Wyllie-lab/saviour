# Training a rat detector for apa_camera

Quick reference — full walkthrough (including labelling tool recommendations,
data collection caveats, calibration images, and deployment) is at
[`docs/readthedocs/apa_rat_detector_training.md`](../../../../docs/readthedocs/apa_rat_detector_training.md).

This produces the YOLO `.pt` checkpoint that `tools/convert_to_hailo.py`
(repo root) then compiles into the `.hef` `apa_camera_module.py`'s
`HailoDetector` actually runs on-device — this folder only covers getting
*to* that `.pt`, not the Hailo conversion itself (already documented in that
script's own docstring).

## Pipeline

1. **Collect footage** — normal SAVIOUR recordings from an `apa_camera`
   module, ideally with `object_detection.enabled` and
   `shock_zone.shock_zone_display` both off (avoids baking a
   detector/shock-zone overlay into the training images).
2. **Extract candidate frames**:
   ```bash
   python extract_frames.py --session-dir /path/to/session/date_dir \
       --out training_data/images --every-n-frames 10 --motion-only
   ```
3. **Label them** — external tool (Roboflow, CVAT, or LabelImg), YOLO-format
   output (`class x_center y_center width height`, normalised 0-1, one
   `.txt` per image). Single class: `rat` (class `0`).
4. **Arrange into the standard Ultralytics layout, then train**:
   ```
   training_data/
     images/train/*.jpg   images/val/*.jpg
     labels/train/*.txt   labels/val/*.txt
   ```
   Copy `dataset.yaml.example` → `dataset.yaml`, point `path` at
   `training_data/`, then:
   ```bash
   python train.py --data training_data/dataset.yaml
   ```
   Produces `ratnet.pt` in this folder.

   **Already labelled in Roboflow? One command does the download and the
   training run** — use your Roboflow **Private API Key** (Workspace
   Settings → API Keys), not the Publishable one; the Publishable key
   can't pull a full dataset export:
   ```bash
   pip install -r requirements.txt
   export ROBOFLOW_API_KEY=...   # the Private key
   python train.py --roboflow-workspace <ws> --roboflow-project <proj>   # lists versions, then exit
   python train.py --roboflow-workspace <ws> --roboflow-project <proj> --roboflow-version <N>
   ```
   (`download_roboflow_dataset.py` also works standalone if you just want
   the dataset without training yet — `train.py` calls into the same code.)

   Prefer a notebook? `train_rat_detector.ipynb` in this folder walks
   through the same download → train flow cell-by-cell (works locally or
   on Colab with a GPU runtime).
5. **Convert + deploy** — from the repo root:
   ```bash
   python tools/convert_to_hailo.py --model src/modules/variants/apa_camera/training/ratnet.pt
   ```
   Then point `object_detection.model_path` at the resulting `.hef`.
