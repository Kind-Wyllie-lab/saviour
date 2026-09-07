# AudioMoth Sync Calibration — Design

**Status:** scoped 2026-09-04, **not implemented**. This document is the plan; no clicker
hardware, firmware, or calibration tooling exists yet. It follows directly from a live
debugging session (2026-09-04) that fixed a ~450ms audio/video sync bug down to a
~150-200ms residual (see `src/controller/audio_align.py`'s `parse_mic_sidecar` — the
`STARTED`-anchor change is on the `staging` working tree, **uncommitted** as of this
writing) and investigated, but couldn't close, the remaining gap. This doc is the plan
for closing it further, safely.

## 1. Background — why a residual remains

Camera sync is tight because it's real hardware sync: every camera disciplines its clock
from the controller's PTP grandmaster, and libcamera timestamps each frame at capture
time. There is no equivalent for the AudioMoth. It's a generic USB audio-class device
read through PipeWire → `soundcard` (a ctypes/cffi wrapper) → a Python `time.time()` call
around a blocking read, in `src/modules/variants/microphone/microphone_module.py`'s
`_record_microphone_segment`. Nothing in that chain carries a hardware timestamp; every
number we have is an inference from when a *userspace call returned*, several software
layers removed from the ADC.

**What was found and fixed today:** the recorder's very first `record()` call at the
production block size (131072 samples / ~683ms nominal) reliably takes ~2x as long
(~1.26-1.27s) as every subsequent call — confirmed live via SSH-instrumented probes
against the real hardware (`pi@10.0.0.156`, module `microphone-module-4703`). The
previous fix (commit `55eb99bc`, same day) assumed this meant no audio was captured
during that stretch, and pushed the sample-0 anchor ~0.6s later to compensate. Direct
instrumentation disproved this: a small-blocksize read on a freshly-opened stream returns
real noise-floor signal (not silence) within ~20ms, and `soundcard`'s `.latency` property
(backed by `pa_stream_get_latency`) confirmed the delay is PipeWire over-priming its
internal ring buffer on the first large read, not a capture gap. The fix: anchor
`sample0_wall_ns` on the `STARTED` timestamp (written immediately after
`recorder.__enter__()` returns, before the read loop begins — confirmed ~30ms overhead,
unaffected by the first-block priming behaviour) instead of extrapolating from a
block-index linear fit.

**Validation:** session `test-143757` (2026-09-04, on this controller) contains a real
clap sequence. Raw camera CSV timestamps (PTP-hardware-derived, trustworthy) place a
clap's hand-contact instant at frame-accurate precision; the old code placed that clap's
audio onset ~450ms late. The `STARTED`-anchor fix brought that down to audio landing
**~150-200ms early** — a real improvement, bounded by how precisely a human clap's
"contact" frame can be read (±30ms-ish at 30fps with motion blur) rather than by anything
more fundamental.

**What this residual most likely is:** real, small USB-isochronous/ALSA-period settling
latency between "PulseAudio reports the stream ready" and "the ADC's first sample is
actually visible to the OS" — a stage `.latency` can't see, because it reads 0 at that
exact moment (nothing buffered yet).

**Why the monitoring stream isn't the lever:** `microphone_module.py`'s
`_monitor_audiomoth` thread runs for the entire life of the module process
(`start_streaming()`/`stop_streaming()` are called once each, at module `start()`/`stop()`
— there is no per-recording toggle). Every recording this system makes happens with
monitoring already attached to the same device; it's the permanent operating condition,
not a variable to control for. It plausibly explains *why* the first-block priming
behaviour looks the way it does, but the `STARTED` anchor is captured before any of that
happens, so it doesn't touch the residual either way.

## 2. Why bench calibration, not a live-session marker

The natural next step — fire a known transient during a real recording and measure where
it lands — was rejected for this system specifically: **SAVIOUR is rodent behavioural
recording hardware, much of it built around capturing spontaneous ultrasonic
vocalisations.** A transient acoustic event that reliably precedes or coincides with
recording start is a real experimental confound, not just an inconvenience:

- **Classical conditioning.** A cue reliably correlated with session start (or animal
  introduction) is exactly the kind of incidental stimulus animals learn fast. Repeated
  across sessions, it risks contaminating whatever behavioural measure the rig exists to
  collect.
- **No safe frequency band exists.** Rodents hear well into the range this hardware is
  specifically configured to capture (AudioMoth at 192kHz to reach ~70kHz+ usable
  bandwidth, per the AudioMoth hardware-gotchas section of `CLAUDE.md`). Anything loud
  enough for an onset detector to reliably find above the noise floor is plausibly audible
  to the animal too — there's no "ultrasonic-but-inaudible-to-rats" escape hatch.
- **"Preflight before the animal's in the rig" doesn't fully rescue it** if the animal is
  held nearby and can hear it before every introduction — that's still a learnable
  anticipatory cue across sessions.

**The residual instead looks like a property of the hardware/software stack** (USB
device + kernel + PipeWire + `soundcard` version, for a given Pi + AudioMoth pairing) —
not something that should vary moment-to-moment with the room or the animal's presence.
That makes it a candidate for **periodic bench/maintenance calibration**, run with no
animal anywhere near the rig, rather than anything live-session. This trades "measured
fresh every session" for "measured periodically on the bench," which is the right trade
given the confound risk — provided the residual is actually stable across reboots/time
for a fixed device (an assumption this design explicitly calls out as needing validation,
not asserted).

## 3. The measurement chain, and what has to be controlled for

A naive bench test — fire a GPIO pin, note `time.time()`, find the click in the
recording — would just relocate the same class of error into the calibration itself. The
full chain is:

```
software fires trigger → GPIO toggle → buzzer's own electromechanical response
    → sound travels through air → AudioMoth's internal ADC/anti-alias pipeline
    → USB → PipeWire → our software timestamp (the thing we're calibrating)
```

Only the last three stages are the actual target. Everything before that — GPIO
scheduling latency, the transducer's own response time, air propagation — is a separate
additive delay that must be either near-zero-and-known or independently characterized and
subtracted, or it inflates the correction constant with noise that has nothing to do with
the AudioMoth/PipeWire chain.

Controls:

- **Trigger timestamp**: not a Python `time.time()` wrapped around a blocking GPIO call
  (scheduling jitter comparable in size to what we're measuring). Use a kernel-level GPIO
  edge timestamp (`libgpiod`'s edge-detection) or a hardware-timed pulse (PWM peripheral),
  so the logged fire time is close to the real electrical edge.
- **Transducer choice**: a piezo element driven directly (microsecond-scale
  electromechanical response), not a relay/solenoid clicker (single-to-tens-of-ms of real
  mechanical delay — the wrong choice here).
- **Fixed, short, known distance** between transducer and AudioMoth capsule on every rig,
  so air-propagation delay is both tiny (sub-ms at a few cm) and constant across
  deployments, rather than a per-rig unknown.
- **One-time, independent characterization** of the transducer+propagation delay as a
  fixed constant (e.g. tap the piezo's actual drive waveform on a second fast channel —
  an oscilloscope or logic analyzer — against a reference mic of known latency, or a
  laser vibrometer/accelerometer directly on the piezo surface). This is a one-off bench
  measurement per clicker design, not something redone on every calibration run.
- **Precisely locatable stimulus shape**: a sharp broadband impulse (which a piezo click
  naturally produces) analyzed by cross-correlation/matched filtering against the known
  drive waveform, rather than the coarse 50%-of-peak threshold method used to validate
  today's fix against a human clap (fine for a one-off human-generated sanity check, not
  precise enough for a repeatable calibration protocol).

## 4. Automated multi-trial protocol

Because this runs on the bench with no animal present, there's no reason to limit
repetitions the way a live-session marker would need to be limited:

- Fire dozens-to-hundreds of clicks per calibration run, at **randomized inter-click
  intervals** (avoids periodicity/resonance artifacts in the measurement).
- Repeat across **reboots and multiple days**, not just one run — this is the test of the
  open stability assumption from §2. If the residual drifts meaningfully run-to-run, that
  changes the design (periodic re-calibration cadence becomes load-bearing; a single
  "calibrate once at deployment" constant would not be trustworthy).
- Build an actual **distribution** (mean, spread, outlier rate) of the measured residual
  per device, not a single point estimate — this also averages out jitter in the
  trigger/transducer path itself, and gives a real confidence interval to attach to the
  correction constant rather than a guess.
- Run per-device, not per-model: USB hub, specific AudioMoth unit, kernel/PipeWire
  version on that specific Pi image can all plausibly shift the number (see §1) — a
  fleet-wide constant derived from one test unit shouldn't be assumed to generalize
  without checking a few different devices.

## 5. Where the correction plugs in

- Store the measured per-device correction (value + confidence bound + calibration
  date/firmware version it was measured against) somewhere the module can read at
  recording time — likely a small calibration record alongside `active_config.json`
  (mirrors how other per-device state is already handled, e.g. `dashboard_views` in
  `src/controller/dashboard_views.py`), not a hardcoded constant in `audio_align.py`.
- Apply it in `parse_mic_sidecar` (`src/controller/audio_align.py`) as an additive
  correction to `sample0_wall_ns`, alongside the existing `STARTED`-based anchor — not a
  replacement for it, a refinement on top of it.
- Surface calibration age/staleness somewhere visible (Settings → Thresholds is the
  existing home for comparable PTP-tuning knobs) so a controller running on stale
  calibration data is at least visible, not silent.

## 6. Open design questions

1. **Stability assumption**: does the residual actually hold stable across reboots and
   days for a fixed device, or does it drift enough to need frequent re-calibration? This
   is the load-bearing question for the whole design and should be checked with a short
   multi-day bench run before building any scheduling/hardware around it.
2. **Re-calibration cadence**: if stable, calibrate once at deployment and re-check only
   after a kernel/PipeWire update or hardware change (piggyback on the existing
   `mend.sh` maintenance pass)? If not stable, what triggers a re-calibration?
3. **Per-unit vs per-model**: how many distinct devices need calibrating before trusting
   a shared constant across the fleet, versus needing genuinely per-unit values?
4. **Hardware integration**: does the clicker live on every microphone module
   permanently (small added cost/complexity per unit, calibration always available without
   extra kit), or is it a shared bench fixture technicians attach during maintenance
   (cheaper per-unit, but calibration becomes a scheduled event requiring the module to
   be brought to the fixture)?
5. **Segment-boundary behaviour**: the fix this design refines applies fresh per
   recording segment (each segment restart opens a new `soundcard` recorder and rewrites
   `STARTED`). If the priming/residual behaviour is truly a function of "opening a fresh
   stream" rather than session-level state, does the bench-measured constant apply
   uniformly to every segment restart within a session? Reasonable to assume yes given
   what's already been observed, but not yet directly tested against a multi-segment
   recording.

## 7. Phased plan

- **Tier 0 (stability check, no new hardware)**: is there an existing way to fire a
  precisely-timestamped acoustic event on the bench without building the clicker first
  (e.g. a phone speaker + manual GPIO-adjacent logging), just to get a first read on
  whether repeated bench trials on the same device land in a tight cluster or scatter
  widely? Cheap gut-check before committing to hardware.
- **Tier 1 (clicker hardware + one-time transducer characterization)**: build the
  piezo+GPIO clicker, characterize its own trigger-to-acoustic-output delay once via an
  independent reference, per §3.
- **Tier 2 (automated multi-trial bench protocol)**: scripted multi-click, randomized
  interval runs producing a residual distribution per device; validate the §6.1 stability
  assumption across reboots/days.
- **Tier 3 (fleet integration)**: calibration record storage and `parse_mic_sidecar`
  integration per §5; a maintenance-pass hook if re-calibration cadence (§6.2) turns out
  to be needed.

## Non-goals

- No acoustic transient of any kind during a real (animal-present) recording session.
- Not a fix for camera-side sync (already tight via PTP) or for cross-camera phase offset
  (see `CLAUDE.md`'s Camera framesync section — a separate, already-understood
  phenomenon).
- Not a continuous/periodic in-recording marker (the fuller "genlock via TTL module"
  option considered and set aside earlier in the same investigation) — this design is
  deliberately the cheaper one-shot bench-calibration version, not that.
- Not an attempt to reach camera-tier (<50µs) sync. The realistic target here is closing
  most of the remaining ~150-200ms gap with a measured, confidence-bounded constant —
  genlock-tier sync would need PTP-aware audio hardware that doesn't exist in the
  USV-capable microphone space (see the options discussion that preceded this doc).
