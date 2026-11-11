# Ephys headstage accelerometer (Post-Process)

- **Status:** proposed
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

## Prerequisite: pick the right recording (affects alignment today)

`tool1_fit_alignment.find_oe_ttl_dir` returns the first TTL folder that
`os.walk` happens to reach. With a two-experiment upload, `run_ephys_align` can
fit the session against the wrong experiment, and nothing flags it: the fit
can still look acceptable if the pulse train is similar.

Fix in `align_cli.py` (not the vendored GUI module):
- List every `recordingN` under `_ephys/` with its start time
  (`sync_messages.txt` software time) and duration (samples / rate).
- Choose the one whose span overlaps the session's TTL CSV span the most. If
  none overlaps, or two overlap about equally, stop with an error that lists the
  candidates.
- Write the chosen recording's path into `model.json` (`oe_recording`). The
  accelerometer step reads it from there rather than discovering the recording
  again.
- Allow a manual override (`--recording`, plus a selector in the panel).

Accept: on the Natalie upload, `openfield-2-123850` picks experiment 1 and says
so in the result.

## Phase 1: process and plot (Ephys panel)

**Backend: `src/controller/ephys_accel.py`** (numpy only; the controller env has
no scipy or matplotlib).
- `find_aux_channels(oebin)`: AUX channels grouped by headstage port (`A1`,
  `B1`, ...). Empty means the panel says "no accelerometer channels recorded".
- `load_aux(recording, port)`: memmap with stride 4 → mask ceiling / below 0 V
  → 10 ms median bins → interpolate gaps. Returns 100 Hz volts, an invalid
  mask, and OE timestamps.
- `calibrate(v)`: sphere fit as above, with a quality check (orientation spread
  and fit residual). If it fails, use the stored calibration (phase 3) and warn.
- Derived signals: acceleration in g; gravity (a numpy low-pass at 1 Hz, e.g.
  forward-backward moving average or FFT; it doesn't need to be a Butterworth);
  pitch and roll; ODBA (sum of absolute dynamic acceleration, 0.5 s mean).
- Time: OE seconds come from `timestamps.npy`. When `model.json` exists, SAVIOUR
  time = `(oe_s − offset) / scale + sv_t0` (the inverse of tool3's
  `saviour_ns_to_oe_seconds`), so each row also carries `timestamp_ns`.
- **Outputs** in `<session>/_ephys/accel/<port>/`:
  - `accel.csv`: 100 Hz; `timestamp_ns` (blank if not aligned), `oe_s`,
    `ax_g, ay_g, az_g`, `pitch_deg, roll_deg`, `odba_g`, `invalid`.
  - `summary.json`: recording, port, calibration (offset_V, V_per_g, method,
    fit quality), clip ceiling in g, invalid fraction per axis, duration, and
    whether it's aligned.
  - `plot.json`: a reduced copy for the browser (min/max per bucket, about 2000
    buckets per series).
- Runs as a background job like `run_ephys_align`: socket `run_ephys_accel` →
  `ephys_accel_update` (queued/running/done/error, stage).

**Frontend: an "Accelerometer" section in `EphysAlignPanel`** (or a sibling
panel once the upload exists).
- Recording + port selectors (pre-filled from `model.json` / detection), a Run
  button, the job state.
- The summary chart, drawn by hand as React SVG from `plot.json` (the frontend
  has no charting library and this doesn't justify one). Three stacked charts on
  one time axis: X/Y/Z in g; pitch and roll; ODBA. Hover crosshair with values,
  legend + direct labels, categorical slots 1-3, light and dark themes. The time
  axis is session time when aligned, ephys time otherwise (labelled as such).
- Calibration and data-quality line from `summary.json` (offset, V/g, invalid
  %, "heading not measurable"), plus Download CSV.

Accept:
- On the Natalie upload: experiment 2 gives offset 0.49 ± 0.01 V and 0.34 ± 0.01
  V/g; the 219 s burst doesn't appear in the chart; experiment 1 gives a total
  of 1.00 ± 0.03 g at rest.
- A synthetic `continuous.dat` fixture in pytest (known orientations, injected
  clipped and below-0 V samples) round-trips to the right pitch/roll within 1°.

*About two days.*

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
