# APA rat detector: auto-labelled training set, whole-video evaluation, safe automated shocks

- **Status:** in progress (step 1 built; awaiting step 2; step 5 designed 2026-10-23)
- **Created:** 2026-10-21
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (APA rat detector / automated shocking)

## Why

The offline blob tracker (`tools/apa_track.py`) is good enough for analysis
(98.7% of audited frames on the rat), but its live version is not safe to
shock from: about 7 false zone entries of 1 s or more per hour across 38
sessions, almost all on the wrong blob (the moving grid rim, a dark rat
against the dark wall). Full figures: `docs/CHANGELOG.md` 2026-10-21 and the
report at https://claude.ai/artifact/M3Qj8gDADCyNZafSnPRfBU.

A YOLO detector on the APA camera's Hailo has no rolling background, so it
should not see blobs where nothing is. The pipeline mostly exists already:
`src/modules/variants/apa_camera/training/` (`train.py`, YOLO11n at 640, the
Roboflow download), `tools/convert_to_hailo.py` (HEF compile, done before by
the owner on an x86 machine), and `HailoDetector` / `HailoRawDetector` in
`apa_camera_module.py`. The August run on Roboflow
`sidb-workshop/rat-tracker-zh4ex` v2 (587 images) scored mAP50 0.995, but on
a validation split from the same images. Nobody has measured it on whole
videos.

What v2 actually holds (downloaded 2026-10-21): 95 distinct source frames,
augmented (flip, crop, shear, greyscale) to 587 and stretched to 640x640.
32 of the 37 validation frames and 4 of the 5 test frames also appear in
train, so the 0.995 is mostly a training score. And the frames are of other
rigs: a rectangular open-field box (2024) and an older wide-angle APA setup
where the rat is a few dozen pixels. None are from the current APA camera
view, and none are empty-arena negatives. It is not a reference for this rig.

The bottleneck is labelled images. This plan generates most of them from the
offline tracker, and measures any model with the same numbers as the
blob trackers.

## Box convention

Already documented in `docs/readthedocs/apa_rat_detector_training.md`: a
tight box around the **body without the tail** (nose to tail base), because
the shock decision uses the box centre and a moving tail shifts it.

| Case | Label |
|---|---|
| Rearing against the wall | the body visible from above |
| Partly outside the mask | the visible part; skip the frame if well under a third shows |
| Dark rat against the dark wall or rim | label it (the cases the blob tracker fails on) |
| Operator's hand with the rat | the rat only; a hand-only frame is a negative |
| Empty arena | negative: image plus an empty label file |
| Shadow or reflection | not labelled |

## Step 1 — auto-label exporter (`tools/apa_autolabel.py`)

Input: the `apa_track_assess.py` `.npz` folder plus the share (as
`apa_track_report.py --share-root`). Output: a YOLO-format folder ready to
upload to Roboflow beside the existing set.

1. **Positives (about 2-3k frames).** Frames where offline and hood agree
   (within 100 px), the blob area is within the session's normal range, and
   the position is steady (no jump in the frames around it). Stratify by rat,
   session and position band: centre, wall, rim (beyond 0.8 R), inside the
   zone, within 30 px of the zone edge. Cap per session so no one recording
   dominates, and take frames at least 1 s apart.
2. **Boxes.** The `.npz` holds centre and area only, so the exporter decodes
   each video **sequentially** (seeks are wrong on the May files: 25 fps
   content in a 30 fps container), re-runs `RatTracker` on the chosen frames
   and takes the chosen blob's bounding box at full resolution. The blur and
   opening already remove most of the tail; check on a sample of 50 against
   the convention before trusting it, and trim with a distance-transform
   cut (drop thin protrusions) if tails survive.
3. **Hard cases for review (about 300).** Disagreement frames (offline vs
   hood vs causal), dark-wall and rim frames, jumps, frames with several
   competing blobs, and the frames the visual audit marked wrong or
   uncertain (`index.csv`). Exported **with** a best-guess box into a
   separate `review/` set so the owner corrects boxes in Roboflow instead of
   drawing them.
4. **Negatives (about 200).** Pre-onset frames (empty arena, often with the
   rim reflection at its strongest), hand-only frames around placement and
   removal, and frames where the causal tracker fired on nothing.
5. **Split by rat, not by frame.** Hold one May rat and one October rat out
   entirely for validation and evaluation; frames from the same session are
   near-duplicates.
6. **Write** `images/`, `labels/` (YOLO txt; empty file for negatives), a
   `manifest.csv` (source session, frame, timestamp, category, which trackers
   agreed) and a contact sheet per category so a reviewer can scan the set
   before upload.

Acceptance: 50 random positives checked by eye, at least 48 with the box on
the body and the tail excluded; every review frame has a visible box; no
held-out rat appears in train.

**Built** (`tools/apa_autolabel.py`, tests in `src/tests/test_apa_autolabel.py`).
What the first full run (38 sessions, ~22 min on the controller, 2 jobs)
taught, and what changed because of it:

- **Clipped boxes.** A rat leaning over the wall is cut off by the arena
  mask. Boxes are re-found inside a mask 1.25x the arena; a blob that still
  reaches the edge goes to review (`clipped_<band>`).
- **Partial blobs.** A dark rat against the dark wall comes out as a
  fragment. Positives need a blob between the session's 30th and 95th
  area percentiles; small rim blobs go to review (`small_rim`).
- **"Empty" frames that weren't.** Before onset the rat is sometimes already
  in. A negative needs both offline and hood to see nothing bigger than a
  quarter of the rat; other outside frames go to review as `check_empty`
  with no box. All 127 negatives checked by eye: empty (a few with only the
  operator's hand, which is a negative by the convention).
- **Loose boxes.** The arena rotates, so the median background is a smooth
  blur with no grid in it; the grid and rim next to the rat differ from it
  and merge with the rat's blob. 50 random positives: all 50 on the body,
  no tail visible, but 11 visibly loose, 5 with the centre noticeably off.
  Tightening by intensity does not work (the grid bars are as bright and
  dark as the fur). Instead, a blob filling under 37% of its box
  (`MIN_FILL`) is not a positive: up to 5 per session go to review as
  `loose_<band>`, the rest are dropped. On the sample this caught 7 of the
  11 loose boxes and 3 borderline ones.
- **Split.** Holdout rats 152 (May) and 854 (October) -> `valid/`, about
  30% of the frames.
- `DIR/calib/`: 128 train images (10% empty) for `convert_to_hailo.py
  --calib-dir`.

Run 2 (with `MIN_FILL`), `/home/pi/apa_autolabel_v2` on the controller,
303 MB: **1,846 positives** (1,215 train / 631 valid), **733 review** (183
of them `loose_*`), **127 negatives**, 128 calib. Acceptance on 50 new
random positives: 50/50 on the body, 49/50 with no tail (one curl of tail
possibly inside), about 6 still loose, 4 of them with the centre off by
around a sixth of the box. Passes; the remaining loose boxes are label
noise worth correcting if they turn up while reviewing, and the reason to
read step 3's numbers against the offline tracker rather than mAP alone.

## Step 2 — label review and training (owner)

1. **Baseline, no Roboflow needed:** copy `DIR/dataset/` to the laptop and
   `python train.py --data dataset/data.yaml` (YOLO11n, 640). `valid/` is
   the two held-out rats, so its mAP is an honest first number.
2. **Review:** upload `DIR/review/` to Roboflow as pre-labelled (YOLO
   format keeps the boxes), fix boxes, delete or empty-label the
   `check_empty` frames that are empty, and mark the ones with a rat.
   Category is in the file name and `manifest.csv`.
3. **Merge and train:** upload `DIR/dataset/` as well, keep the
   train/valid split from the folders (don't let Roboflow re-split by
   frame), add no augmentation in Roboflow (Ultralytics augments at train
   time), export, train. Keep v2 out of `valid/`; adding it to train is
   optional, it is a different view.
4. **Compile** to HEF on the x86 machine with `--calib-dir DIR/calib`.

## Step 3 — whole-video evaluation

Add a `yolo` tracker to `tools/apa_track_assess.py` (Ultralytics `.pt` on the
laptop GPU; optional, skipped when `ultralytics` is missing) and report it in
`apa_track_report.py` beside the others. Run it on the held-out sessions, and
then on all 38.

Numbers that decide it, against the offline tracker as reference:

- false zone entries of 1 s or more per hour (live blob tracker: 7.4)
- zone entries never seen (live: 36 / 1,111) and the 95th-percentile delay
  in seeing an entry (live: 0.08 s)
- frames with a detection after the rat goes in, and frames with a detection
  before it does (should be near 0)
- a visual audit of its disagreements with offline, same sheets as before

Then check the **compiled HEF**, not just the `.pt`: quantization can cost
accuracy, so run the HEF (Hailo emulator on x86, or on the Pi over recorded
frames) on the held-out sessions and compare.

Target before going to the rig: under 1 false entry of 1 s or more per
hour, and no worse than the live blob tracker on missed entries.

## Step 4 — on-device

Confirm the APA camera Pi's Hailo (8 vs 8L, `hailortcli fw-control
identify`), the HailoRT version against the compiler that built the HEF, and
that `HailoDetector` loads the model. Measure inference rate and latency on
the lores stream at the recording frame rate, and that recording frames are
not dropped (`framesync`-style check on `_recording.json`).

## Step 5 — automated shocking: modes, arming, safety rules

Not enabled for real shocks until step 3's target is met; the dry-run mode
below can be built and run on the rig before that, and is how step 3 gets
measured live.

### Today (2026-10-23)

- The camera sends `zone_entered` / `zone_exited` (edge events only) to the
  controller; `apa_controller.py` just forwards them to browsers.
- "Armed" exists only in the browser (`sharedArmed` in `APACommands.jsx`,
  one per tab). `apa_arduino`'s `_activate_shock` runs for any caller; the
  module and controller have no arm state. Safeguards that do exist on the
  module: the 1 s shock lease (`SHOCK_LEASE_S`, the caller must re-send
  `activate_shock`) and the per-session cap (`_max_shocks`, 50).

### Tracking is independent of shock mode

Tracking (camera config) runs whatever the shock mode: manual shocking with
tracking on, shown on the livestream or not, and logged per frame
(`det_cx`, `det_cy`, `det_conf`, `in_zone` in the timestamps CSV) is a
supported setup in its own right, not only a step towards automation.

### Shock mode and arming: one gate, on the Arduino module

The gate sits on `apa_arduino`, the last hop before the hardware, so a
controller or frontend bug can't bypass it.

- `shock_mode`: `disarmed` | `manual` | `auto` | `dry_run` (auto decisions
  logged, never delivered). `activate_shock` carries a `source` (`manual` or
  `auto`) and is refused unless the mode allows that source; refusals are
  logged.
- Always starts `disarmed`: at module start, after an Arduino reconnect, and
  never persisted to config. Arming is an explicit operator action
  (`set_shock_mode`, auth-gated), broadcast in `arduino_state` so every
  browser shows the same state.
- **Stop all** (`stop_all_shocks`): deactivate immediately, set `disarmed`,
  and tell the controller to drop any auto state. Reachable from every APA
  page (a fixed button, plus a key, e.g. Escape), from the REST API, and
  always allowed, even for a lapsed login? (open question below). Never
  throttled.
- Also disarm automatically on: session stop, camera module offline,
  tracking lost for longer than a limit while in `auto`, controller restart
  (it re-sends `disarmed` on reconnect).

### Zone state: periodic, not edge-only

The controller can only keep a shock going by re-sending `activate_shock`
inside the 1 s lease. With edge events alone, a camera that crashes mid-zone
never sends `zone_exited` and the controller would keep refreshing. So the
camera also sends `zone_state` at ~5 Hz while tracking: in zone or not,
position, confidence, number of boxes above threshold, time in zone, frame
timestamp. The controller refreshes the lease only while the latest
`zone_state` is younger than ~0.4 s and passes the rules below; anything
stale stops the refreshes, and the Arduino ends the shock within the lease.

### Auto decision (controller), rules

- in zone continuously for a minimum time (start at 0.5 s; tune from the
  evaluation's entry durations)
- confidence threshold, and exactly one box above it
- optionally require the hood detector to agree (cheap, independent)
- caps per minute and per session, on top of the module's 50-pulse cap and
  the lease
- the operator can always stop it (Stop all above)
- every decision (deliver, dry-run, refused, capped) logged to
  `shock_events.csv` with position, confidence, time in zone and source, so
  a session can be audited
- a supervised trial on the rig (operator watching, ready to stop) before
  any unattended run

### Build order

1. `zone_state` from the camera; `shock_mode` + `stop_all_shocks` on the
   module, with the frontend's arm control and a Stop all button replaced
   by the module's state (manual shocking keeps working, now behind a real
   gate).
2. Controller auto logic in `dry_run` only; run sessions on the rig and
   count would-be shocks against the video (this is step 3 measured live).
3. `auto` with real shocks, supervised, once the false-entry target is met.

### Open questions (protocol decisions for the researchers)

- In `auto`, does the spacebar still deliver manual shocks, or only in
  `manual`?
- Minimum time in zone, shock duration per entry, re-shock interval while
  the rat stays in, and per-minute / per-session caps.
- Is there a hardware way to power the grid down (relay / enable line the
  Arduino can drive), or is "arm" a software gate on the trigger only? A
  hardware disarm would make Stop all independent of the software.
- Should Stop all work without a login (it only ever makes things safer)?

## Not doing

- Pose / keypoints (nose, tail base). The zone test needs a body centre; a
  box gives that. Revisit if heading direction becomes a measure.
- Replacing the offline tracker for analysis. It stays the reference.
- Tracking more than one animal.

## Open questions

- Does the APA camera Pi have a Hailo HAT, and which one?
- Upload to the existing Roboflow project as a new version, or a new project?
  A new project keeps v2's other-rig frames out of the split by default.
- The minimum time in zone before a shock is a protocol decision for the
  researchers, not only an engineering one.
