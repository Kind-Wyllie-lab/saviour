# Audio/video sync residual: validation & root cause

- **Status:** proposed
- **Created:** 2026-09-07
- **Owner:** ascottg
- **CLAUDE.md ref:** "AudioMoth sync calibration" open item (Architectural concerns) +
  `docs/AUDIO_SYNC_CALIBRATION_DESIGN.md`

## Background — what's known

- `src/controller/audio_align.py::parse_mic_sidecar` (uncommitted on `staging`)
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

## Phase A — sign & magnitude, camera out of the loop

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

## Phase B — mechanism confirmation: `block_size` / `sample_rate` sweep

### How to actually change `block_size` (there are three traps)

1. `microphone.block_size` and `microphone.frame_num` are stored **`_`-prefixed**
   in `microphone_config.json` (`_block_size` / `_frame_num`, both 131072).
   `_`-prefixed keys are internal defaults, not part of the user-overridable /
   frontend-visible / controller-synced surface.
2. The code reads the **non-underscore** path:
   `self.config.get("microphone.block_size", 1024*128)` (`microphone_module.py:341`).
   So today the config file value is inert — that `get()` always returns the
   hardcoded `1024*128`. To vary it you must add real `microphone.block_size` /
   `microphone.frame_num` keys (or drop the underscores).
3. `Config._prune_stale_keys()` runs at **every module startup** and deletes any
   non-private `active_config.json` key **not present in the static
   `base_config.json` / `microphone_config.json`** (`config.py:238-265`). So a
   hand-edit of `/etc/saviour/module/active_config.json` is wiped on the next
   restart.

**→ The only reliable way:** edit `microphone_config.json` on the bench module
(add real `block_size` / `frame_num` keys), redeploy, restart the service. It's a
bench module — a redeploy per sweep value is cheap.

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

### The sweep

- `block_size ∈ {8192, 32768, 131072, 262144}` (keep `frame_num == block_size`),
  3 runs each, buzzer pulses throughout.
  - offset ∝ `block_size` in **seconds** → **H1** confirmed, and `block_size` is
    the knob.
  - offset **flat** across `block_size` → **H2** (fixed source/USB latency);
    `block_size` is a red herring.
- Then hold `block_size` fixed and sweep `audiomoth.sample_rate`: is the constant
  offset fixed in **milliseconds** (time-based buffer) or in **samples**
  (count-based)? Further pins the mechanism.
- Watch `SEGMENT_TOTAL_SAMPLES` and per-block deltas in the sidecar for xruns /
  gaps at the small `block_size` values — 192 kHz on a loaded Pi 5 will drop
  blocks below some threshold. **Test only; do not ship a small value** without
  proving xrun headroom (CLAUDE.md already notes ~4% of blocks stall on a loaded
  Pi).

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

- Reuse `audio_align.py`'s fit for `rate_est` and the `_align.json` fields
  (`residual_p50_ms` / `p95_ms`, `ppm`, `n_outliers`).
- New small script: cross-correlate a transient template into the aligned FLAC,
  return sample index + `STARTED`-anchored wall time, difference against the
  reference-event (TTL edge / GPIO) wall time, aggregate across pulses and runs →
  mean, confidence interval, drift slope. Use the **same anchor** as
  `parse_mic_sidecar`. This is the "automated multi-trial protocol"
  `docs/AUDIO_SYNC_CALIBRATION_DESIGN.md` scopes — building the analysis half now
  against the TTL-buzzer rig is a no-regret step whether or not the piezo-clicker
  hardware is ever built.

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
