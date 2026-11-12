# Ephys headstage accelerometer (Post-Process)

- **Status:** in progress (recording choice + phase 1 built 2026-11-11; phase 2 built
  2026-11-12, not yet accepted on hardware; phase 3 open)
- **Created:** 2026-11-10
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (Post-Process).

## Why

A researcher asked to process and plot the accelerometer data from the ephys
system. Intan-based Open Ephys headstages carry a 3-axis analog accelerometer
that the acquisition board records as `A1_AUX1-3`, interleaved with the neural
channels in `continuous.dat`. We already upload Open Ephys data to a session
(`POST /api/ephys/upload` → `<share>/<session>/_ephys/`) and fit a clock model
from the shared TTL train (`run_ephys_align` → `_ephys/model.json`). The
accelerometer is the next thing to get out of the same upload: head tilt and
movement intensity, aligned to video.

## What the data looks like (from `SAVIOUR_Natalie_GS_lab-240626`)

Checked by hand on 2026-11-10. A prototype script (scipy + matplotlib, not in the
repo) produced a summary PNG and a 3D orientation video from it.

- **Channels:** 75 channels at 30 kHz: `CH1-64`, then `A1_AUX1-3` (oebin
  description "Aux input channel", `AUXsOn="1"` in `settings.xml`), then `ADC1-8`.
  AUX `bit_volts` = 3.74e-5 (the oebin says "mV", but that number is volts per
  count). With a second headstage there would also be `B1_AUX*` etc.
- **Rate:** the AUX value changes every 4th sample (7.5 kHz). 100 Hz is plenty
  for analysis.
- **Range and bad samples:** the Intan AUX input is 0-2.45 V, but int16 storage
  tops out at 32767 × bv ≈ 1.2255 V (≈ +2.15 g here), so samples at the ceiling
  are clipped. Values below 0 V can't come from the sensor: there are ~1 ms dips,
  and garbage during saturation bursts (one 0.2 s burst at 219 s read −5 g).
  Mask both, take the median per 10 ms bin, and interpolate across the gaps.
  That's 0.02-0.08 % of samples per axis in experiment 2.
- **Calibration:** fit a sphere to the slowly changing (gravity) part of the
  signal during the stillest 20 % of the recording. It gives offset ≈ 0.49 V on
  all three axes and 0.34 V/g, which is consistent with a 3 V analog part.
  Cross-check: experiment 1 (headstage at rest) comes out at a total of 1.00 g,
  with Z up. The fit needs enough orientation variety; a recording with the
  headstage sitting still needs a stored calibration instead (below).
- **Yaw (heading) can't be measured.** Gravity gives pitch and roll only.
  Heading needs a gyroscope or magnetometer, or video tracking.
- **One upload can hold several recordings.** This one has `experiment1`
  (11:38:50 UTC, 139 s, headstage not on an animal, so the trace is flat) and
  `experiment2` (11:43:48, 290 s, real movement). Session `openfield-2-123850`
  matches experiment 1. The recording that goes with experiment 2 isn't in the
  dataset.

## Built: recording choice + phase 1 (2026-11-11, branch `feat/ephys-accelerometer`)

Write-up in `docs/CHANGELOG.md` (2026-11-11). In short:

- **Recording choice** (`src/controller/ephys_data.py`, used by
  `tools/ephys/align_cli.py` and the accelerometer step). It lists every
  recording in the upload and picks the one whose span overlaps the session's
  TTL log. No overlap, or two about equal, is an error that lists the
  candidates; `--recording` / the panel's selector override it. `model.json`
  records `oe_recording`, and the accelerometer step reads it from there.
  Natalie's upload: `openfield-2-123850` → experiment 1, 135 of 135 s.
- **Sync pin**: `align_cli` defaulted to TTL pin 1 and the page never passed
  a pin, so alignment from the page could never have fitted a rig whose sync
  train is on another pin (Natalie's: pin 19). It now uses the pin logged in
  `pseudorandom` mode, else the only pin (`ephys_data.sync_pin`).
- **Ephys PC clock**: alignment reports `pc_clock_offset_s`, how far the
  acquisition PC's clock was from PTP at the first sample. Natalie's PC was
  10.85 s fast. The recording choice leans on that clock, so it is coarse: fine
  for telling sessions minutes apart, not seconds. (This also corrects an
  earlier reading: the ~13 pulses missing from experiment 1 are at the end,
  where ephys stopped ~10 s before SAVIOUR, not the start.)
- **`src/controller/ephys_accel.py`** (numpy only) → `<session>/_ephys/accel/
  <port>/accel.csv` (100 Hz), `summary.json`, `plot.json`, as planned. Gravity
  is a 1 s boxcar applied twice (zero phase). Calibration falls back to
  `DEFAULT_CALIBRATION` (this headstage's fit) when the auto fit fails a gate
  (0.2-0.6 V/g, residual ≤ 3 % of 1 g, ≥ 25° spread of still orientations),
  flagged as "default" with a warning. A clock model fitted on a different
  recording is not applied (output stays in ephys time, with a warning).
- **Web**: `get_ephys_info` (recordings + AUX ports, match, model, results),
  `run_ephys_accel` → `ephys_accel_update`, `get_ephys_accel_plot`;
  `run_ephys_align` takes `recording` / `pin`. Jobs share one subprocess
  runner.
- **Panel**: reads what is already on disk (it used to forget an upload when
  you left the page), recording selector, clock-model line, Accelerometer
  section with calibration/quality facts, the three-chart SVG with a hover
  readout, Download CSV. `dev:mock` covers it (`src/mock/ephysMock.js`).

Acceptance, as measured:
- Experiment 2: offset 0.496 / 0.498 / 0.492 V, 0.332 V/g (auto) ✓. The 219 s
  burst is masked where clipping dominates and stays within ±1.5 g; it still
  shows as a ~1.9 g ODBA spike, which looks like a genuine knock that saturated
  the sensor, so it is flagged rather than removed.
- Experiment 1 (headstage at rest): the auto fit fails its gates (0.0066 V/g)
  → default calibration, Z 0.976 g ✓.
- Synthetic recordings (`test_ephys_accel.py`): pitch/roll within 1°, offsets
  within 0.01 V, dips ignored, burst flagged and filled ✓.

Not yet done: run on a controller (the jobs, upload size, timing on a Pi 5);
the chart at phone width wasn't viewed (the test browser wouldn't resize).

## Phase 2: compose tiles (layout editor)

Two new tile kinds in `compose_layout.TILE_KINDS`, with source
`ephys/accel/<port>`. They're only offered when that session has an aligned
`accel.csv`; without `model.json` there's no video time to place them on.

- **`accel_trace`**: a scrolling strip like the spectrogram tile. Options:
  `signal` (`xyz` | `tilt` | `odba`), `window_s`, `playhead`, y range (auto or
  fixed), `label`. Rendered once per job into a wide strip image with OpenCV
  polylines (one strip pixel column per output time step, capped like
  `_strip_pixels_per_second`), then shown per frame by the same code as
  `spectrogram_pane` / `_SpectrogramStrip`.
- **`orientation`**: the 3D headstage box. It tilts by pitch/roll, with heading
  fixed and stated on the tile. Drawn per frame with OpenCV (fixed oblique
  projection, faces depth-sorted, `fillPoly` + axis lines + X/Y/Z labels,
  optional dynamic-acceleration arrow). No matplotlib, so the controller needs
  no new dependency.
- Preview at the scrubbed time draws both kinds the same way the render does.
- Validation follows the existing per-kind option validators. Old layouts are
  unaffected.

Accept:
- A short tap on the headstage during a session (do it on the desk rig, with a
  TTL + camera + ephys) shows on the trace tile within one output frame of the
  video frame where it happens.
- Tilting the headstage by hand to ±45° shows on the orientation tile within ±5°.

*About two days*, after the compose editor's phase 3 (event/text tiles) or in
parallel with it. They share the "non-camera strip tile" plumbing.

### As built (2026-11-12)

- `src/controller/accel_tiles.py` draws both kinds (OpenCV + numpy). A tile
  carries a per-frame `draw(t_ns, w, h)` function that `video_compose`'s render
  and preview loops call; the trace's whole-window strip is drawn once per job
  and scrolled by `spectrogram_pane` (`strip_from_image`).
- The preview draws the trace window directly around the scrubbed time. A
  missing or unaligned source shows its reason on the tile instead of failing
  the preview; a render refuses it.
- `compose_streams_info` gains `accel_sources` (from each port's
  `summary.json`). The editor lists them under "Headstage accelerometer"; only
  aligned ones can be added, and they get a lane on the timeline.
- Checked with a stand-in: the `1TS-1627-102915` session (one camera, one mic)
  with experiment 2's headstage data and a hand-made clock model marked
  `"fudged": true`. The accept tests above still need a real session with the
  headstage, camera and TTL together.
- Fixed on the way: `resolve_date_dir` took `_ephys` as the day folder of any
  session with an ephys upload (it sorts after the dates); and `_run_progress`
  never read ffmpeg's stderr pipe, so a chatty render could hang (seen on
  Windows).

## Phase 3: animal frame and stored calibration

- **Mounting:** map headstage axes to the animal's frame (which axis points
  forward / left / up, and its sign). With that, "pitch" means nose up/down and
  "roll" means ear down, instead of headstage X/Y/Z. Stored per rig in the
  controller config, with an override per session.
- **Stored calibration:** offset and V/g per axis, entered from the datasheet or
  measured by a guided "place the headstage on each of six faces" step. Used
  when the auto-fit quality check fails, or when the user picks it.
- Optional later: movement-derived states (immobility, from ODBA below a
  threshold for N s; rearing, from sustained nose-up pitch) as ethogram tracks.
  This needs validating against hand-scored video before anyone relies on it.

## Not in scope

- Gyroscope/IMU headstages (different channel layout, and a full orientation
  filter); revisit if a lab has one.
- LFP/spike traces as compose tiles (tool3 does LFP export today; a separate
  plan if wanted).
- NWB export of the accelerometer (`docs/NWB_EXPORT_DESIGN.md` covers that
  later).

## Open questions

- **Upload size.** The accelerometer needs `continuous.dat` (1.3 GB for 5 min ×
  75 ch), while alignment alone needs only the `events/` folder. Accept the full
  upload (simplest), or extract the AUX columns in the browser with
  `File.slice` + `DataView` and upload a few MB? Recommend: full upload first,
  in-browser extraction only if the field rigs' upload time becomes a problem.
- Does every lab's headstage use the same accelerometer part (offset/V-per-g)?
  If so, a default calibration can ship instead of an auto-fit fallback.
- Test data: we need a recording with the headstage on an animal **and** its
  SAVIOUR session (Natalie's experiment 2 has no session). Ask the lab for one
  before phase 2 acceptance.
