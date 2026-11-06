# Audio/video sync residual: validation & root cause

- **Status:** in progress (Phase A buzzer runs 2026-11-06; LED/camera check and the latency-anchor probe next)
- **Created:** 2026-09-07
- **Owner:** ascottg
- **CLAUDE.md ref:** "AudioMoth sync calibration" open item (Architectural concerns) +
  `docs/AUDIO_SYNC_CALIBRATION_DESIGN.md`

## Background — what's known

- `src/controller/audio_align.py::parse_mic_sidecar` (now committed)
  anchors `sample0_wall_ns` on the recorder's `STARTED` timestamp
  (`microphone_module.py:367-368`, `actual_start = time.time()` right after the
  `soundcard` recorder context manager `__enter__` returns) instead of a
  block-index linear-fit intercept. That cut a real A/V sync error from ~450 ms
  to **~150–200 ms residual**.
- The residual is *assumed* to be USB / PipeWire / kernel-stack latency, with no
  hardware timestamp anywhere to anchor on.
- **Unvalidated (load-bearing):** whether the residual is a stable constant for a
  fixed device across reboots/time. `docs/AUDIO_SYNC_CALIBRATION_DESIGN.md`'s
  whole per-device-correction-constant approach depends on this.

## New observation (2026-09-07) — pins the sign

In the one test video reviewed, the clap was **seen before** the audio transient
appeared / was heard → **audio lags video**.

With the transient near the start of the recording (drift negligible), the
pipeline assigns sample `n` the wall time `A(n) = sample0_wall + n/rate_est`. For
the transient to land *late*:

```
sample0_wall  >  true_capture_time(sample 0)
```

i.e. the `STARTED` anchor is stamped **later** than sample 0 was actually
captured — sample 0 is *older* than the anchor. That's the signature of the
first `recorder.record()` returning audio **already buffered** in the
PipeWire/ALSA source at connect time, **not** of stream-setup latency delaying
the first sample.

This flips the sign assumed in earlier analysis. The two mechanisms predict
opposite signs:

| Mechanism | sample 0 vs `STARTED` | first `record()` blocks for… | A/V result |
|---|---|---|---|
| **H1** first `record()` drains a pre-filled source buffer | older than anchor | ~nothing (data already there) | audio **lags** ✓ observed |
| **H2** setup / source latency delays first captured sample | newer than anchor | ~a full block (~683 ms) | audio **leads** |
| **H3** rate-drift term dominates | — | — | only if transient is late in the recording |
| **H4** the `STARTED`-anchor change overshot a prior *audio-leads* bug | — | — | same observation, different story |

The eyeballed direction is soft evidence (perception of sub-250 ms offsets is
unreliable), but 150–200 ms is above the human audio-lag threshold, so it's
credible and worth treating as the working hypothesis until measured.

## Step 0 — the quick discriminator (no hardware) — **IMPLEMENTED 2026-09-07**

`_record_microphone_segment` (`src/modules/variants/microphone/microphone_module.py`)
now writes four extra `KEY value` lines into every segment's `*_timestamps.txt`
sidecar, right after `STARTED`:

| Sidecar line | Meaning |
|---|---|
| `RECORDER_ENTER_MS` | wall time from just before `microphone.recorder(...)` to just after its `__enter__` returns — the `soundcard`/PulseAudio stream-setup cost |
| `FIRST_RECORD_MS` | wall duration of the **first** `recorder.record(numframes=frame_num)` call only |
| `FIRST_RECORD_SAMPLES` | `data.shape[0]` of that first read (guards against a short first read) |
| `FIRST_RECORD_EXPECTED_MS` | `frame_num / sample_rate * 1000` — one full block at the current settings, for comparison |

`audio_align.parse_mic_sidecar` already skips any line containing a space
(`if " " in line ...`), so these do not perturb the block-rate fit. They ride
the normal export next to the FLAC.

**Reading `FIRST_RECORD_MS` vs `FIRST_RECORD_EXPECTED_MS`:**

| First read takes… | Reading |
|---|---|
| ≪ `FIRST_RECORD_EXPECTED_MS` (e.g. ~5–20 ms) | samples were already buffered at stream open → **H1**, and sample 0 predates `STARTED` (matches the observed audio-lags-video sign) |
| ≈ `FIRST_RECORD_EXPECTED_MS` (~680 ms) | it blocked waiting for capture → **H2** |
| ≈ 2× `FIRST_RECORD_EXPECTED_MS` | the case the existing `audio_align.py` comment already describes (see below) |

**Pre-existing evidence already in the tree:** the comment above the fit in
`audio_align.py::parse_mic_sidecar` (added 2026-09-04, from an on-device probe
against the real AudioMoth + PipeWire stack) records that block 0's first
`record()` "routinely takes ~2× a normal block's duration … a one-off
software/buffering cost of the *first* large read, not missing audio: a tiny
first read on the same freshly-opened stream returns real noise-floor signal
(not silence) within ~20 ms." That already points at **H1** (data present at
open, the delay is read-processing cost). `FIRST_RECORD_MS` promotes that
one-off manual observation to a field on every recording, so Phase A can check
it's stable and Phase B can see how it moves with `block_size`.

**Next:** take a few normal recordings, read the new fields out of the sidecars,
and decide whether Phase B's sweep is warranted. The probe lines are temporary —
remove them (and this note) once the mechanism is settled.

**Tooling (added 2026-09-07):** `tools/analyse_audio_sync.py` — no ffmpeg
dependency, reads FLAC via `soundfile`, reuses `audio_align`'s block fit and
PTP-window summary so the sample-0 anchor is identical to the post-hoc aligner.

| Subcommand | Does |
|---|---|
| `probes SESSION_DATEDIR [more…]` | Step 0: prints `RECORDER_ENTER_MS` / `FIRST_RECORD_MS` (× expected) / block-fit rate + residual per segment, then the H1-vs-H2 reading from the table above |
| `ttl SESSION_DATEDIR [more…] [--pin N]` | Phase A: for each rising edge in the TTL `*_events.csv`, finds the transient in the FLAC, times its onset off the `STARTED` anchor + fitted rate, differences it against the edge; reports mean/median signed offset, within-run spread, drift slope, and — across several dirs — the between-run spread against the ±40 ms decision gate |
| `ref SESSION_DATEDIR --at-ns NS …` | Same, against hand-supplied instants (e.g. a clap frame's `timestamp_ns` from a camera CSV) |

Onset detection: high-pass (default 1.5 kHz) → short-time RMS → first sustained
crossing of `noise×ratio`, choosing the candidate nearest the predicted
position so a neighbouring pulse can't hijack it. Tests:
`src/controller/tests/test_analyse_audio_sync.py` (5, synthetic FLAC + sidecar +
TTL CSV with an injected offset).

The tool's own block fit **excludes block 0 explicitly** and fits the
steady-state cadence (k ≥ 1) — see the bug note below; `audio_align`'s in-tree
fit does not, so on a short (< ~1 min) recording its `measured_rate_hz` is
garbage. Doesn't matter in production (60-min segments) but bit the bench runs.

### Step 0 results — 5 bench runs, 2 AudioMoths, 2026-09-07

`test_mic_latency-microphone-1509*` — audio-only, ~6 s each, mic module
`microphone-4703`, both AudioMoths (`2474750264200FAD`, `24FCBD0864934CA8`).

| Quantity | Result | Spread |
|---|---|---|
| `FIRST_RECORD_MS` / expected (682.7 ms) | **1.87×** (~1275 ms) | 1.85–1.88×, 10/10 |
| block 0 vs steady-cadence line | **−597 ms** (block 0 sits ~1 block *below* the extrapolated k≥1 line) | std **6.4 ms** across 10 sidecars |
| `RECORDER_ENTER_MS` (stream open) | ~52 ms | 42–63 ms; the `24FC…` unit is consistently ~10 ms slower to open than `2474…` |
| `block[0] − STARTED` | ~0.3 ms | negligible — the `STARTED` line and the first pre-`record()` stamp are the same instant |
| steady-state rate | 191.9–192.0 kHz (−490…+60 ppm) | the low-residual runs (`…0945`, `…0854`) are trustworthy; `…0910` had scheduler jitter (p95 2.7–4.9 ms) |

**Reading:**

- The first-read anomaly is **~2×**, matching the existing `audio_align.py` note,
  and is a *one-off software cost* (PipeWire over-priming its ring buffer on the
  first large read), not a capture gap — consistent with the 2026-09-04 on-device
  probe. So `FIRST_RECORD_MS` is **not** the ≪1× pure-H1 signature and **not** the
  ~1× H2 signature; it's the middle case.
- **The magnitude is a rock-stable per-device constant** (±6 ms run-to-run, across
  a device power context that included fresh `soundcard.get_microphone` each run).
  This is the load-bearing precondition for `docs/AUDIO_SYNC_CALIBRATION_DESIGN.md`
  §6.1 — a single per-device correction constant is viable *if* the sign/size can
  be pinned.
- **The sign is still not decidable from the sidecar.** "sample 0 captured ~600 ms
  after `STARTED`" (→ audio *leads*) and "sample 0 ≈ `STARTED`, but a fixed
  ~600 ms delivery latency established by the slow first read and never
  recovered" (→ audio *lags*) fit the block timestamps **identically** — they
  differ only by an unobservable constant. The `−597 ms` `STARTED − steady k=0`
  figure is the size of that ambiguity, not a measurement of the offset.
- **Phase A (TTL buzzer) is now unavoidable** and is the whole ballgame: it's the
  only thing that breaks the degeneracy. The `NO-NAME-105539` rig already has a
  TTL module with free pins.
- **Phase B is still worth doing**: if the ~600 ms first-read excess scales with
  `block_size`, that both confirms the PipeWire-priming mechanism and makes
  `block_size` a real mitigation knob.

### Bug found (not yet fixed): `audio_align.parse_mic_sidecar` short-recording fit

`parse_mic_sidecar`'s comment claims `_robust_linfit`'s n-sigma rejection drops
block 0 on its own. True for a 60-min segment (block 0 is 1/5270). **False for a
short recording**: with 8–11 blocks, block 0's ~600 ms deviation isn't rejected
(`n_outliers 0`), the slope is dragged, and `measured_rate_hz` comes out
40–70 k ppm low (e.g. 181 kHz instead of 192 kHz), with `residual_p95` ~290 ms.
`tools/analyse_audio_sync.py` works around it by excluding index 0 before the
fit; `audio_align` should do the same (explicit skip, not rely on statistical
rejection) so the post-hoc aligner is safe on short clips too. Low urgency —
real sessions are long — but it's a latent footgun for anyone aligning a test
recording.

## Phase A — sign & magnitude, camera out of the loop

> **Tooling built 2026-10-02 (buzzer/LED hardware on order):** the buzzer
> now hangs off the **microphone module's own GPIO** rather than a TTL
> module, so edge and audio share one clock. `sync_selftest` (REST `POST
> /api/v1/modules/<id>/sync_selftest`) runs this phase on the device with no
> session and no camera; `sync_pulses` + `tools/av_sync_check.py` add an LED
> and measure every camera too. Analysis in `src/shared/av_sync.py`, anchored
> with `audio_align.parse_mic_sidecar` so the result is directly the
> correction constant. How to run: `docs/AV_SYNC_TEST.md`. The TTL-module
> rig below still works with `tools/analyse_audio_sync.py ttl`.

Do **not** make hand-clap-vs-video the primary method: a clap is a multi-frame
visual event, and you get one measurement per recording.

**Rig:** a TTL output pin driving a small **piezo/buzzer** positioned near the
AudioMoth.

- The TTL module already logs `time.time_ns()` adjacent to every output-pin edge
  (`ttl_module.py` output generators + `_write_ttl_event`), on the
  PTP-disciplined `CLOCK_REALTIME` — that's the ground-truth timeline, good to a
  few ms (output-pin logging has some Python jitter but no ~200 ms term).
- `interval_pulse` mode → 15 pulses/recording at a configurable interval →
  **15 offset measurements spread across one recording**, so constant-vs-drift is
  visible within a single run.
- Per pulse: cross-correlate into the aligned FLAC → sample index →
  `STARTED + n/rate_est` (the exact quantity `parse_mic_sidecar` produces) →
  difference against the TTL edge timestamp.

**Runs:** 5–10, including across reboots and under `stress-ng --cpu 4 --io 2` +
an open monitoring stream.

**Outputs:**
- mean **signed** offset (confirms/refutes the lag direction);
- within-run spread → drift vs constant;
- **between-run spread → the load-bearing number.** If this is tight, a single
  per-device correction constant is viable and the design-doc approach stands.
  If it's wide, calibration alone won't fix it.

**Fallback if no buzzer:** a clapperboard (sharp visual edge + sharp transient)
beats hands, and *then* higher camera FPS helps (tightens the video side from
±33 ms at 30 fps to ±8 ms at 120 fps). Still one event per take — strictly worse
than the buzzer.

### Phase A results — first buzzer runs, 2026-11-06 (desk rig, `microphone_4703`)

**Rig:** TDK PS1240 passive piezo straight on the mic Pi's GPIO17 / GND
(`sync_pulse: {buzzer_pin: 17, drive: "pwm", tone_hz: 4000}`), both AudioMoths
(A1 `2474750264200FAD`, A2 `24FCBD0864934CA8`) a few cm from it,
`mic_distance_m` 0 (≈0.3 ms of flight time left in). 192 kHz, `filter_type`
none, gain 4, `block_size` = `frame_num` = 32768, idle Pi, PTP-locked. 20
buzzes per run at 1.2 s, edge bracket ≤ 40 µs.

**Result: audio is placed ~21 ms *after* the sound, steady within a recording
but moving ±5 ms between recordings, independently per AudioMoth.**

| | A1 median (ms) | A2 median (ms) |
|---|---|---|
| run 1 | 22.33 | 18.79 |
| run 2 | 16.59 | 23.99 |
| run 3 | 18.75 | 25.45 |
| run 4 (kept) | 20.29 | 27.72 |
| run 5 (kept) | 17.99 | 26.48 |
| run 6 (kept) | 23.02 | 20.78 |
| run 7 (kept) | 12.77 | 30.83 |
| run 8 (kept) | 17.47 | 21.97 |
| run 9 (kept) | 11.41 | 18.49 |

- **Within a run:** all 20 offsets sit inside ~1 ms peak-to-peak (e.g. A1 run 1:
  21.83–22.75). Onset detection, the buzzer and the block-fit rate (residual p95
  0.33 ms) are not the problem. Every pulse detected on both mics at ~33 dB SNR.
- **Between runs (the load-bearing number):** 18 recordings, mean 20.8 ms,
  sd 5.0 ms, range 11.4–30.8 ms. A1 and A2 in the *same* run differ by up to
  18 ms and swap order, so this is **per recording (per stream open), not per
  device**. The "fixed per-device constant" assumption (Background, and the
  `_block_size_note` in `microphone_config.json`) does not hold below ~±5 ms.
- **Against the decision gate:** well inside the < ±20 ms gate, so a single
  constant (subtract ~21 ms in `parse_mic_sidecar`) is viable *now* and takes
  the error from ~21 ms to ~±5–10 ms (2 sd). Getting below that needs a
  per-recording measurement (below).
- **Agrees with Phase B**, whose clap test at 32768 found ~+15 ms. The 150–200 ms
  in Background predates the block-size change.
- **An earlier run** with only A1 near the buzzer gave 30 ms at ~20 dB SNR: weak
  onsets are detected late. Keep the buzzer right against the AudioMoths.

**Which timestamp best predicts the true sample-0 time?** Runs 4–9 kept their
recordings (`sync_selftest` `keep_audio: true`, via `/facade/send_command`;
the REST route doesn't pass it). For each of the 12 recordings, true sample 0
= `STARTED` − measured offset, compared against every stamp the sidecar
already has:

| candidate anchor | minus true sample 0: mean | sd | range |
|---|---|---|---|
| `STARTED` (current) | +20.8 ms | 5.75 | 11.4 … 30.8 |
| `STARTED` − `RECORDER_ENTER_MS` (stream open) | −9.6 ms | 7.77 | −24.3 … 1.0 |
| robust fit intercept, blocks 1.. (steady state) | +104.1 ms | 4.54 | 95.8 … 109.8 |
| block 0 return − one block | +102.6 ms | 4.84 | 94.1 … 108.8 |
| block 1 start − one block | +103.8 ms | 4.79 | 95.3 … 110.0 |

`corr(offset, FIRST_RECORD_MS)` = −0.82, `corr(offset, RECORDER_ENTER_MS)` = 0.49.

None is much better than `STARTED`. The steady-state fit intercept averages
hundreds of blocks, so Python scheduling can't be what moves it: the ~±5 ms is
**variation in the audio stack's own capture-to-delivery latency, fixed for a
stream's life and different each time a stream opens**. It looks quantised:
intercepts bunch at ≈ 99 and ≈ 106.5 ms (6 of 12 within 106.2–106.7). No
host-side "better time to stamp" can recover that; the latency itself has to be
measured, per recording. (Now `tools/audio_anchor_check.py`, which reproduces this table from the kept
runs.)

**Proposed anchor: the stream's own reported latency.** PipeWire (via its
PulseAudio API; `soundcard` 0.4.6 exposes it as `recorder.latency`, which calls
`pa_stream_update_timing_info` + `pa_stream_get_latency`) reports, for a record
stream, how long ago the next unread sample was captured. So right after a
`record()` returns at wall time `T` with `S` samples read so far:

```
sample0_wall ≈ T − latency(T) − S / fs
```

Taken after every block, that gives one estimate per block (~6/s), so the
per-recording latency is measured, not assumed, and Python jitter at any one
stamp averages out. It needs no new hardware and no raw ALSA (Phase C's
`htstamp` spike stays the fallback if PipeWire's figure doesn't track the
truth). The quantum here is the default (`clock.quantum` 1024, min 32, max
2048).

Probe first, change the anchor second:
1. **Built 2026-11-06:** after each `record()` the recorder appends
   `LAT <index> <t_before> <t_after> <latency_us>` to the sidecar
   (`microphone.latency_probe`, default on; `stream_latency_line` in
   `microphone_module.py`). `<index>` is samples returned plus the ones
   soundcard still holds in `_pending_chunk`, because PipeWire counts those as
   read. Five fields, so `parse_mic_sidecar` and the other sidecar readers skip
   it (tested). One `update_timing_info` round trip per block; the line's
   `t_after − t_before` shows that cost, check it adds no stalls under the
   Phase B stress run.
2. Re-run the 6 kept-audio self-tests and compare with
   `tools/audio_anchor_check.py run_dirs...` (numpy only; how to fetch the
   root-owned scratch dirs is in its docstring). Accept the new anchor if
   `median(t − latency − index/fs)` per recording tracks true sample 0 with
   sd ≲ 1 ms (vs 5.75 for `STARTED`).
3. Then `parse_mic_sidecar` prefers the latency anchor when `LAT` lines exist,
   falling back to `STARTED` (old recordings, and the 131072-block habitat audio).
   Any fixed remainder (buzzer start-up, USB transport the stream latency doesn't
   see) is then a genuine per-rig constant, from the same self-test.

**How far to trust the GPIO edge times.** The self-test's truth is the
Python stamp around the gpiozero write (bracket ≤ 40 µs), on the same Pi and
the same `CLOCK_REALTIME` as the audio stamps, so PTP error can't enter it.
Not yet measured: (a) with `drive: "pwm"` the 4 kHz tone comes from gpiozero's
lgpio *software* PWM, whose first transition after the write is unmeasured
(expected well under 1 ms, but nobody has looked); (b) the piezo's acoustic
ring-up, a few 4 kHz cycles (~0.5–1 ms). Both are fixed delays that land in
the mean, not the per-recording spread, so they don't affect today's ±5 ms
finding. They matter once an anchor aims at ~1 ms. To bound (a): jumper GPIO17
to a spare input (e.g. GPIO22, header pin 15) and log that input's kernel edge
timestamps (libgpiod edge events, realtime clock) beside each pulse's Python
stamp. (b) can't be separated from the audio path by the camera run (audio −
video still contains it); bound it from the kept FLACs by how many cycles the
onset detector needs before it fires, or compare against a bare piezo disc.

**Camera check, first run (2026-11-06, session `avsync_led4-132647`).** LED on
GPIO27 (330 Ω, red) about 70% down `habitat_camera_0fcc`'s frame (imx477,
1920×1080 at 30 fps, AE at its 33 ms ceiling, `habitat_motion.always_record`
on), 30 pulses of 50 ms at 2 s, `tools/av_sync_check.py`:

| | mean | sd |
|---|---|---|
| audio onset − edge, A1 / A2 | +29.7 / +28.9 ms | 3.6 (one +48 ms pulse) |
| first lit frame timestamp − edge | +4.3 ms | 9.1 |
| **audio − video (what compose shows)** | **+25.5 / +24.7 ms** | 9.3 |

- The video sd is the frame quantisation (a uniform phase in a 33 ms
  frame gives 9.6 ms). Lit-fraction fit: intercept −20.7 ms, slope 25.5 ms
  against a 33 ms exposure. That's consistent with timestamps at the start
  of exposure plus rolling-shutter delay for an LED 70% down the frame
  (≈ 12 ms if readout is ≈ 17 ms); moving the LED to the top would test it.
- The clip has 5 more CSV rows than frames. They can't be at the start (a 5-frame
  shift would move the video figure by ≈ 167 ms), so they're trailing rows.
- So in a composite, sound currently lands ~25 ms after the picture, almost
  all of it the audio anchor (+29 ms this recording); the camera's own
  placement is ≈ +4 ms on average.
- Getting this run needed four fixes found along the way: `sync_pulses`
  refused mid-session (Module.is_recording), habitat clips wrote no CSV rows
  and the clip open at stop missed its export, the camera needed
  `always_record`, and `av_sync.led_trace` picked blown-out pixels when the
  CSV had more rows than decoded frames. See CHANGELOG 2026-11-06.

**Next (camera):** repeat with the LED at the top of the frame and with manual
exposure (e.g. 5 ms), for a cleaner camera timestamp offset. Then combine
with the latency anchor once that's validated.

**Corroborate with a camera (LED).** Wire an LED on the same mic Pi
(GPIO27, header pin 13, 330 Ω to GND; `sync_pulse.led_pin: 27`) in view of
`habitat_camera_0fcc`, near the top of its frame, and run the session check in
`docs/AV_SYNC_TEST.md` (`sync_pulses` during a recording, then
`tools/av_sync_check.py`). Buzzer and LED fire from one GPIO write on one
clock, so that run gives audio − edge, frame − edge and audio − video for the
same instants: whether ~21 ms is what compose actually shows, and how the
camera's own timestamp offset compares.

## Phase B — mechanism confirmation: `block_size` / `sample_rate` sweep

### How to actually change `block_size` — simpler than first thought

`microphone.block_size` / `microphone.frame_num` are stored `_`-prefixed in
`microphone_config.json` (`_block_size` / `_frame_num`, both 131072).
**`Config.get()` resolves a leading-underscore fallback** for every path segment
(`config.py:402` — `elif f"_{part}" in config`), verified: `get("microphone.
block_size", 1024*128)` returns `_block_size` (131072), **not** the hardcoded
default. So the earlier "trap #2" (the config value is inert) was wrong — editing
`_block_size` / `_frame_num` in the base file **does** take effect.

`_`-prefixed keys are also never touched by `Config._prune_stale_keys()` (it
skips `key.startswith("_")`, `config.py:259`), so there's no stale-key wipe to
worry about either.

**→ Method:** on the bench module, edit `_block_size` **and** `_frame_num`
(keep them equal) in `src/modules/variants/microphone/microphone_config.json`,
redeploy, restart the service. A redeploy per sweep value is cheap on a bench
module. `tools/analyse_audio_sync.py` auto-detects the block size per recording
from the sidecar's `FIRST_RECORD_SAMPLES`, so no `--frame-num` bookkeeping.

### Re: "won't the controller overwrite the module's config?"

Not on its own. On module (re)connect the controller **pulls** config via
`get_command "get_config"` and caches it for the frontend
(`controller.py:316`) — it does **not** push. It only writes a module's config
via `set_config` when:

- an operator hits **Save** on that module's config card in the web UI
  (`facade.set_config` → `send_command "set_config"` →
  `module.set_config(persist=True)` → `Config.set_all` persists to
  `active_config.json`);
- `apply_section_to_type` / `apply_section_to_cameras` — **camera only**;
- FrameSync `reconcile_framesync` — pushes only `camera.sync_mode`;
- `reset_module_config`.

None touch a microphone `block_size`. So: run the sweep on a bench rig, and just
don't Save that module's config from the UI mid-experiment. (Even if you did,
`set_all` would merge your other keys, not reset the file.)

### Monitoring on/off (companion probe — `monitoring.enabled`)

The design doc says the always-on monitoring stream "isn't the lever" (the
`STARTED` anchor is captured before it matters) but concedes it "plausibly
explains *why* the first-block priming behaviour looks the way it does". Now
directly testable: **`monitoring.enabled`** (base config `monitoring` section,
default `true`; added 2026-09-07). `start_streaming()` returns early when false,
so no second `soundcard` recorder is ever opened on the AudioMoths.

Set it `false` on the bench module, restart, take the same 5 recordings, re-run
`analyse_audio_sync.py probes`. What to look at:

- Does `FIRST_RECORD_MS` still come in at ~1.87× a block? If it drops to ~1×, the
  first-read over-prime is a *contention* artefact of the concurrent monitor
  reader, not intrinsic to opening a fresh PipeWire stream.
- Does `block-0 vs steady line` (the ~−597 ms ambiguity term) shrink?
- `RECORDER_ENTER_MS` — expect little change (stream open, not first read).

This isolates "fresh-stream priming" from "two readers on one device". Cheap, no
buzzer needed, and it's a real deployment option regardless of the outcome.

### The block-size sweep

- `block_size ∈ {8192, 32768, 131072, 262144}` (keep `frame_num == block_size`),
  3–5 runs each. **Step 0 metrics alone are informative here even before the
  buzzer:** if `FIRST_RECORD_MS − expected` (the first-read excess) tracks
  `block_size` in **seconds** (~0.87 blocks at every size), the over-prime is
  "one extra block" and `block_size` is the knob; if it stays a fixed ~595 ms
  regardless of `block_size`, it's a fixed time-based buffer and `block_size`
  won't help. Add the buzzer for the true signed A/V offset once Phase A exists.
- Then hold `block_size` fixed and sweep `audiomoth.sample_rate`: is the constant
  offset fixed in **milliseconds** (time-based buffer) or in **samples**
  (count-based)? Further pins the mechanism.
- Watch `SEGMENT_TOTAL_SAMPLES` and per-block deltas in the sidecar for xruns /
  gaps at the small `block_size` values — 192 kHz on a loaded Pi 5 will drop
  blocks below some threshold. **Test only; do not ship a small value** without
  proving xrun headroom (CLAUDE.md already notes ~4% of blocks stall on a loaded
  Pi). `analyse_audio_sync.py probes` reports the steady-state fit residual p95,
  which spikes when blocks are being dropped.

### Phase B results — sweep run 2026-09-07 (bench, idle, ~10 s recordings, 3 trials × 2 AudioMoths per size)

| `block_size` | block ms | `FIRST_RECORD_MS` ÷ expected | **first-read excess** (block-0 below steady line) | excess ÷ block | `RECORDER_ENTER_MS` |
|---|---|---|---|---|---|
| 8192   | 42.7  | 1.14× | **−11.9 ms** (±3) | 0.28 | ~49 ms |
| 32768  | 170.7 | 1.55× | **−99.8 ms** (±4) | 0.58 | ~52 ms |
| 131072 | 682.7 | 1.87× | **−596.7 ms** (±6) | 0.87 | ~52 ms |
| 262144 | 1365  | 1.95× | **−1305 ms** (±4) | 0.96 | ~49 ms |

**The first-read excess scales as `block_size^1.36`** (12 ms → 1305 ms over a 32× range).
Super-linear, and the excess-per-block ratio climbs monotonically toward ~1 as the
block grows. This is the **H1** signature: the first `record()` over-primes
PipeWire's ring buffer, and the cost grows worse-than-linearly with the requested
read size (buffer fill + copy/settle, not a fixed latency).

**Consequences:**
- **`block_size` is the knob.** At **8192** the anomaly is nearly gone — the
  block-0 ambiguity term is **~12 ms** vs ~597 ms at the default, i.e. the
  first-read contribution to any A/V offset (whatever its sign) is reduced ~50×
  and is down at `RECORDER_ENTER_MS` scale.
- **H2 is refuted** — a fixed source/USB latency would be flat across `block_size`.
- `RECORDER_ENTER_MS` is flat (~50 ms) at every size, as expected (stream open is
  independent of read size); the ~10 ms per-unit gap between the two AudioMoths
  persists.
- **No dropped blocks** at any size on the idle bench (`SEGMENT_TOTAL_SAMPLES ==
  n_blocks × block_size` exactly; fit residual p95 ~1 ms at 8192). **Not yet
  stress-tested** — the `stress-ng --cpu 4 --io 2` run is still required before
  8192 could ship, and the recordings here were only ~10 s.
- Still **does not give the sign** of the residual — Phase A (TTL buzzer) remains
  the only thing that does. But it means the sign question now matters much less
  if `block_size` drops: 12 ms of ambiguity vs 597 ms.

### Stress run — 8192, `stress-ng --cpu 4 --io 2 --vm 2` on the mic Pi, ~65 s, monitor on (2026-09-07, session `8192_transient_monitoron_stress`)

| Quantity | Idle 8192 | **Stressed 8192** |
|---|---|---|
| First-read excess (block-0 vs steady line) | ~12 ms | **0.3 / 15 ms** — still negligible |
| `RECORDER_ENTER_MS` | ~50 ms | **157 / 234 ms** (3–5× slower; pre-recording, harmless) |
| Steady-fit residual p95 | ~1 ms | **7.6 / 13.3 ms** (scheduler jitter on the per-block `time.time()`) |
| Blocks with a `record()` stall >85 ms | 0 | **3–6 per mic** (worst single stall ~290 ms) |
| `SEGMENT_TOTAL_SAMPLES == n_blocks × 8192` | exact | **exact** — no samples dropped |
| Apparent measured rate | −6…+60 ppm | **−975 / −1088 ppm** — *fit degradation from the stalls, not a real clock shift* |
| Clap A/V offset (16 claps, matched-motion) | mean +17 ms | **mean +9.3 ms, std 10 ms, no drift across 65 s** |

**Verdict: 8192 survives a hammered Pi for recording + timing.** `soundcard.record(numframes=8192)` blocks until it genuinely has 8192 fresh samples, so a stalled read costs a late *timestamp*, not lost *audio* — `_robust_linfit` + the `STARTED` anchor drop the stalled blocks and the alignment still lands sub-frame (mean +9 ms, no drift, despite the −1000 ppm the raw slope reports). Alignment accuracy degrades from <1 ms (idle) to ~10 ms p95 (stressed) — still well inside a video frame.

**But the stress run also surfaced a real export bug** (fixed, `fix/export-mount-reuse-and-false-success`): under `--io 2` the mic module's Samba export failed and reported **success in 0 s** having transferred zero files — `_mount_share()` tore down the working mount, `umount` hit `target is busy`, and `export_staged` fabricated a `True` for the triggered session. Data was recoverable (`to_export/` on the module). See CLAUDE.md "Correctness / data loss".

**Open follow-ups:**
- Same stress run at 32768 and 131072 for comparison (does the p95 residual scale, do stalls get worse).
- The `sample_rate` sweep (hold `block_size`, vary rate) to confirm the excess is
  fixed in *samples* not *milliseconds*.
- `monitoring.enabled=false` × `block_size` — does removing the concurrent reader
  change the exponent or just the constant?
- Phase A (TTL buzzer) for the sign, ideally with a stress run too.

### Shipped default: `block_size` = `frame_num` = **32768** (settled 2026-09-07)

Changed in `microphone_config.json` (`microphone.frame_num` / `.block_size`,
was 131072). Rationale: 8192 and 32768 gave the same clap alignment (~+15 ms,
sub-frame) idle *and* under `stress-ng --cpu 4 --io 2 --vm 2`, but 8192 got
marginal under load (3–6 `record()` stalls >85 ms per mic, `RECORDER_ENTER_MS`
3–5×) while 32768 rode it with **zero** dropped/stalled blocks. The remaining
~90 ms first-read term at 32768 is a *fixed, bench-calibratable constant*; an
xrun is unrecoverable lost audio — so trade the correctable latency for the
robustness. 8192 stays selectable (Recording tab) for anyone who wants the raw
term minimal and accepts thinner margins.

**Not retroactive.** Habitat audio already collected at 131072 stays at 131072
(`audio_align` reads the block size per file from the sidecar). Those recordings
are still alignable — the ~597 ms first-read excess is absorbed by the `STARTED`
anchor + robust fit, leaving the ~50 ms residual measured on the 2026-09-04
sessions (≈1.5 video frames at 30 fps). Fine for coarse USV↔behaviour
attribution; the per-device calibration constant (Phase A) is what tightens it.
`monitoring.enabled` was found not to matter for the first-read term (never
tested paired, but the sweep points at intrinsic PipeWire priming).

## Phase C — targeted code probes (only if A/B point here)

- **Drain-then-stamp variant:** issue one throwaway `recorder.record()` *before*
  stamping `STARTED`, to flush whatever's pre-buffered. Measure whether the
  offset collapses and stays collapsed across runs. If yes → candidate
  structural fix, cheap.
- **Raw-ALSA `htstamp` spike:** via `pyalsaaudio` (or a small C shim), read
  `snd_pcm_status_get_htstamp()` + `avail` to get a driver timestamp of the
  hardware pointer position, and anchor sample 0 to *that* instead of
  `time.time()` around `__enter__`. This recovers the true sample-0 wall time
  regardless of pre-buffer depth. It's the principled fix if H1 is confirmed and
  the offset proves **unstable** run-to-run. Ties into the C-hand-off discussion
  in `plans/ttl-kernel-timestamping.md`'s sibling analysis.

## Preconditions for every measurement

- Confirm from `health.json` that the mic module **and** the camera/TTL module
  held `ptp4l_offset` and `phc2sys_offset` < 50 µs for the entire test
  recording. Otherwise you're measuring PTP error, not audio latency.
- Put the first transient **near the start** of the recording so the drift term
  `n·(1/rate_est − 1/rate_true)` doesn't contaminate the constant. Add one near
  the end too if you want to measure drift separately.
- Keep the same AudioMoth across a `sample_rate` sweep, and re-discover the
  PulseAudio device ID after each rate change — the AudioMoth firmware renames
  its USB device after its sample rate (see CLAUDE.md "Hardware gotchas →
  AudioMoth USB microphone").

## Analysis tooling

**Built 2026-09-07: `tools/analyse_audio_sync.py`** (see the Step 0 section for
the subcommand table). It does what this section scoped: reuses `audio_align.py`'s
robust block fit for `rate_est` / residuals / `n_outliers`, anchors sample 0 on
`STARTED` exactly as `parse_mic_sidecar` does, times a transient's onset in the
raw FLAC, differences it against a TTL edge (or a hand-supplied instant),
aggregates across pulses (drift slope) and across runs (between-run spread vs the
±40 ms gate), and folds in `summarise_ptp_window` for the recording window.
Currently uses an energy-onset detector rather than matched-filter
cross-correlation — a `--template` hook is the obvious next refinement if the
between-run spread looks borderline.

## Decision gates

- **Phase A between-run spread tight** (say < ±20 ms) → per-device correction
  constant is viable; wire it into `parse_mic_sidecar` and proceed with the
  design doc.
- **Spread wide** → calibration alone is insufficient; go to Phase C's
  driver-level anchor.
- **Phase B shows `block_size` is the knob** → consider lowering it as a partial
  structural fix (only with xrun headroom validated), and/or surface it in the
  frontend for per-rig tuning. Otherwise leave `block_size` alone.

## Effort

| Step | Estimate |
|---|---|
| Step 0 — probe lines | 1 hr code + 1 recording |
| Phase A — buzzer rig + runs + analysis script | ~1.5 days |
| Phase B — sweep (redeploys + runs) | 0.5 day |
| Phase C — drain variant / raw-ALSA spike | 1–2 days, only if needed |

## Not doing

- Treating "watched it once and it looked off" as evidence beyond the initial
  sign hypothesis.
- Building frontend controls for AudioMoth buffer params until Phase B shows they
  matter.
- A live in-recording acoustic marker — rejected in
  `docs/AUDIO_SYNC_CALIBRATION_DESIGN.md` as a USV confound risk.
