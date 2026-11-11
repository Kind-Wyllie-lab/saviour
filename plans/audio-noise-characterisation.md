# Room noise reporting and electrical noise characterisation (AudioMoths)

- **Status:** planned, nothing built (2026-11-11)
- **Created:** 2026-11-11
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (one-liner)

## Why

Two related questions, both answered with the AudioMoths already on each
microphone module:

1. **How noisy is the room?** Background noise in the alarm (18-32 kHz) and
   appetitive (45-96 kHz) bands sets how faint a call can be detected, and it
   changes (IVC rack blowers, monitors, motion sensors, cage changes). Today
   the module reports only RMS/peak and clipping on the live monitor; nothing
   is kept with the session.
2. **What does our own hardware add?** PoE HATs, the Pi 5's own power chip,
   the PoE switch and other switch-mode supplies can put tonal lines and a
   raised floor into recordings, by three routes that need telling apart:

| Route | How it shows up |
|---|---|
| Conducted: USB 5 V rail into the AudioMoth's analogue front end | Lines that don't change with distance or acoustic covering; same on both mic inputs |
| Acoustic: power-supply components vibrating ("coil whine") | Real ultrasound that falls off with distance from the Pi / switch |
| Ground loops through the Ethernet shield or PoE | Lines that change with switch port, cabling, PoE on/off |

At 192 kHz the band ends at 96 kHz, and switching frequencies (typically
300 kHz-2 MHz) are mostly filtered out. What gets through is mainly
load-dependent: burst-mode / pulse-skipping whine at light load and
subharmonics. So varying the load is the main lever.

**Units:** everything is dBFS, not SPL. It compares the same unit over time
at the same gain (sidecars carry `GAIN`, and `INPUT`), and units roughly with
each other. Absolute SPL would need a reference source; the sync buzzer at a
fixed distance can serve as a relative one.

## Shared analysis: `src/shared/noise_stats.py`

One pure-numpy module used by the live monitor, the self-test, the survey
script and an offline tool, so the numbers agree everywhere.

- **Band levels** for `<18 kHz`, the alarm and appetitive bands (taken from
  `monitoring.call_bands` / the band presets), and `>96 kHz` where the rate
  allows: Leq, L90 (steady floor), L10 and peak (intermittent), per window.
- **Floor spectrum:** per-frequency median of the short-time power over the
  window (calls are short and drop out of the median).
- **Tonal lines:** frequencies where the floor spectrum stands ≥ N dB
  (default 6) above a median-filtered version of itself (±2 kHz), present in
  ≥ 80% of time slices. Report frequency, height above floor, persistence,
  bandwidth. Merge harmonics into one entry with a list of multiples.
- **Impulsive events:** count of onsets ≥ X dB above the running floor per
  minute (door slams, handling).
- Inputs: a block of samples + rate, or an FFT frame stream (the monitor
  already has one). Output: plain dicts, JSON-ready.
- Tests with synthetic audio: white noise + injected tones (found, right
  frequency/height), + short chirps (not reported as lines), + clicks
  (counted as impulses), levels against known RMS.

## Phase 1: `noise_selftest` (automatic, per module)

A module `@command()` alongside `sync_selftest`, and
`POST|GET /api/v1/modules/<id>/noise_selftest`. Records every AudioMoth to
scratch (same recorder path, `register=False`) through a fixed sequence of
conditions, ~30 s each:

1. idle (monitor running as normal)
2. CPU load: busy processes on all cores (a short-lived helper, killed in a
   `finally`)
3. disk load: sequential writes to a scratch file on the recordings drive,
   deleted afterwards
4. idle again (checks the room didn't change during the test)
5. per AudioMoth with an external input: the same idle on the other input
   (internal ↔ external), switched back in a `finally`

Result per AudioMoth: band levels and lines per condition, plus a
**differences** section: lines that appear or grow ≥ 3 dB under load (power
supply on the module), lines identical on both inputs (conducted), lines
common to all AudioMoths on the module (shared supply). Refused while
recording or during a sync test (shares `_sync_lock`). Results go in the
result JSON like `sync_selftest`; optional `keep_audio`.

**Fingerprint:** a clean idle run can be stored per module/AudioMoth
(`audiomoth_noise_baseline`, a config sidecar section like
`audiomoth_sync_offsets_ms`: lines, band L90s, gain, input, rate, date).
Later runs and phase 2 compare against it ("new line at 41.2 kHz since
baseline").

UI: a **Noise** section on the microphone module's Sync tab (or its own tab):
Run test, per-condition table, floor-spectrum plot per condition.

## Phase 2: continuous room statistics

In the monitor thread (it already computes spectra), per AudioMoth, using
`noise_stats` over rolling 60 s windows:

- heartbeat/health fields: per-band L90 and L10, line count, the strongest
  line, impulse count, "vs baseline" deltas when a baseline exists
- a **room** readout on the Monitor tab and a small floor/line indicator on
  the habitat dashboard's per-box meters
- per session: `<mic>_noise.csv` (one row per minute: band levels, impulse
  count, lines as `freq:height;…`) exported with the recordings, and a
  summary in the session's `_recording.json` / sidecar trailer
- optional warning (like the exposure warning) when the appetitive-band L90
  is ≥ N dB above baseline for M minutes

Cost check on the Pi: the FFT is already paid for; medians over a 60 s ring
of ~1 kHz-resolution spectra are small. Measure CPU before/after on the
desk rig.

## Phase 3: guided survey `tools/noise_survey.py`

Runs from a laptop or the controller over REST (same pattern as
`tools/framesync_sweep.py`): for each step it prints an instruction, waits
for Enter, runs `noise_selftest` (or a single-condition record), and pulls
the result. Builds one report per module (HTML or markdown + PNGs): lines ×
conditions table, floor spectra overlaid per condition.

Steps (each optional, skipped with a keypress):

| Step | Separates |
|---|---|
| **Dummy plug** in the external jack (3.5 mm plug with a resistor, or shorted), switched to external | No acoustic path at all: the AudioMoth's self-noise plus conducted supply noise. Probably the most useful single measurement |
| USB-C bench supply instead of PoE (Ethernet still connected, PoE off on that port) | The PoE HAT |
| USB isolator (ADuM-type, isolated supply) between Pi and AudioMoth | Conducted noise and ground loops |
| AudioMoth moved away on a long USB cable; then mic covered | Acoustic (falls with distance / covering) vs conducted |
| Switch: other port; other PoE loads unplugged; PoE off on the port; AudioMoth next to and away from the switch PSU | The switch and its supply |
| AudioMoth on a laptop on battery, same room | Baseline with no PoE anywhere |

Hardware to get: a dummy 3.5 mm plug (check the jack detects it as an
external mic: `microphone` should show `ext`), a USB isolator, a long USB
cable, a USB-C bench supply for a Pi 5.

## Offline tool

`tools/noise_report.py <session date dir>`: runs `noise_stats` over a
session's FLACs (per minute), writes the same `_noise.csv` and a report, for
recordings made before phase 2.

## Order

1. `noise_stats` + tests.
2. Phase 1 on the desk rig (load steps); dummy plug test by hand. If the
   dummy-plug floor and lines match the internal mic in a quiet room,
   electrical noise isn't the limit and the room is.
3. Phase 2 (monitor + session export).
4. Phase 3 survey on the desk rig, then the habitat rig.

## Open questions

- Does the external jack detect a dummy plug (switch contact vs bias
  current sensing)?
- Does `soundcard`'s recorder on a loaded Pi drop blocks under the CPU-load
  condition (would show as gaps, not noise)? Check the sidecar frame counts.
- Is a 30 s condition long enough for stable L90 / line persistence?
  Compare 30 s vs 120 s on the desk rig.
- Line threshold (6 dB / 80%) to be tuned on real recordings, including a
  session with real USVs to confirm calls aren't reported as lines.
