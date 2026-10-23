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

### Built (2026-10-23): steps 1 and 2 below, plus `auto` (not yet run on the rig)

Design changed from the first draft: the gate is on the **controller**, not
the `apa_arduino` module. Every shock command already goes through the
controller, and LAN access is the trust boundary, so a module-side gate added
a second copy of the state for no protection. The module stays a plain
actuator (pulse loop while the 1 s lease is refreshed, per-session cap).

- `src/controller/variants/apa/shock_automation.py` (`ShockAutomation`), owned
  by `APAController`:
  - `mode`: `disarmed` | `manual` | `auto` | `dry_run`. Starts disarmed (and
    after every controller restart: never persisted). It goes back to disarmed
    when the last recording session ends, and on Stop all.
  - Manual shocks (the dashboard's `send_command activate_shock`) go through
    `Web.handle_special_command`. They're refused (and logged, at most once a
    second) while disarmed, and allowed in every other mode, auto included.
    `deactivate_shock` always passes. `/facade/send_command` is the
    deliberate escape hatch and isn't gated.
  - auto / dry_run: in zone for `apa.auto_shock.entry_latency_s` (0.5 s)
    → `activate_shock` every 0.25 s while it stays in (the module pulses
    `duration` on / `intershock_latency` off), `deactivate_shock` on
    `zone_exited`, on a zone report older than `zone_state_timeout_s` (0.5 s),
    on mode change, or when `max_pulses_per_min` (20, from the module's
    `attempted_shocks`) is reached. Only while a session is recording.
    dry_run logs `would_shock_start/stop` and sends nothing.
  - Socket events: `get_apa_shock_state`, `set_shock_mode` (auth),
    `stop_all_shocks` (no auth: it only makes things safer), and the broadcast
    `apa_shock_state` (4 Hz armed, 1 Hz disarmed).
  - Decisions are logged to the journal and to
    `<share>/<session>/<session>_shock_decisions.csv` (time, mode, event,
    reason, time in zone, position, confidence, camera, by).
- Camera: `zone_state` every 0.2 s while tracking (in_zone, tracking, det_cx,
  det_cy, det_conf, timestamp_ns), plus the existing edge events.
- Dashboard (`APACommands.jsx`): a mode selector replaces the per-tab Arm
  toggle, every tab shows the controller's mode, and a Stop all button (also
  Esc) is added. In auto / dry_run a status line shows what the controller sees
  and the last decision.

Still open from the first draft: a REST route for Stop all; disarming when the
camera goes offline (today a stale camera just stops shocks); requiring the
hood detector to agree; an exactly-one-box rule.

### Validation on the rig (dummy Arduino first)

1. Tracking on (Hailo), shown on the livestream. Mode Auto, then start a
   session. A rat video in front of the camera: shocks start 0.5 s after the dot
   turns red and stop when it leaves. Check `<session>_shock_decisions.csv`
   against the camera's `in_zone` column.
2. The same in Dry run: identical decisions, `attempted_shocks` doesn't move.
3. Stop all mid-shock (button and Esc): the shock stops and the mode is
   Disarmed in every open tab.
4. Kill tracking mid-shock (turn it off): the shock stops within ~0.5 s.
5. End the session: the mode goes to Disarmed.

### Open questions (protocol decisions for the researchers)

- Spacebar in `auto`: built as allowed (placeholder).
- Minimum time in zone (0.5 s), shock on/off (the module's 0.5 s / 1 s), and
  per-minute / per-session caps (20 / 50): placeholders until the
  researchers decide.
- Is there a hardware way to power the grid down (relay / enable line the
  Arduino can drive), or is "arm" a software gate on the trigger only? A
  hardware disarm would make Stop all independent of the software.
- Stop all works without a login: built that way (placeholder).

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
