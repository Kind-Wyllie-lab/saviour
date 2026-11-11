# Per-AudioMoth settings

- **Status:** in progress: phase 1 (per-AudioMoth gain) built 2026-11-11 and on
  the desk rig; device command syntax confirmed, UI save not yet tried.
  Phase 3 (external mic when available, internal for the self-test) built
  2026-11-11, untested on a unit with an external input (the desk units have
  none). Phase 2 not started.
- **Created:** 2026-11-11
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (one-liner)

## Why

A microphone module can have up to 4 AudioMoths, and until now every one got
the same sample rate, gain and filter (`configure_audiomoth` sends one
`AudioMoth-USB-Microphone config ...` to all of them). Different mics need
different gain (placement, distance to the animals, unit-to-unit variation),
and some setups may want a different rate or filter per device.

## What the AudioMoth tool supports (checked on the rig, 2026-11-11)

- A trailing serial number targets one device: `read 2474750264200FAD`
  printed only that AudioMoth; an unknown serial gives `[ERROR] Could not
  find device ID ...`. **`update gain <g> <serial>` confirmed** by hand on
  the desk rig (2026-11-11): `update gain 3 24FCBD0864934CA8` → "Sent UPDATE
  command to device ID 24FCBD0864934CA8", and `read` showed only that unit at
  gain 3. `config ... <serial>` (needed for phase 2) not yet tried.
- **Microphone input** (Open Acoustic Devices application note "Configuring
  the AudioMoth USB Microphone from the Command Line"): `MICROPHONE` on its
  own lists each unit's inputs, `int` always, plus `ext` "when an external
  microphone is available", with parentheses round the one in use.
  `MICROPHONE INTERNAL|INT|I` / `EXTERNAL|EXT|E` switches. Like every
  command it takes trailing device IDs. Needs USB Microphone firmware
  ≥ 1.3.2 and hardware ≥ AudioMoth 1.2.1, AudioMoth Dev 1.0.0 or AudioMoth
  USB Microphone 1.0.0. A separate command from `CONFIG`; `CONFIG`/`UPDATE`
  set a *temporary* configuration (`PERSIST` writes it to the unit).
- Desk rig (read-only checks, 2026-11-11): both units run firmware 1.3.2,
  and `microphone` reports `(int)` only, so neither offers an external input
  (hardware older than 1.2.1, or no external mic detected).

## Phase 1: per-AudioMoth gain (built)

- `audiomoth_settings` (serial → `{gain: 0-4}`), a config sidecar section like
  `audiomoth_labels`, so it survives prunes and resets. An AudioMoth with no
  entry uses `audiomoth.gain`.
- `configure_audiomoth` runs the module-wide config as before, then
  `update gain <g> <serial>` for each connected AudioMoth with an override.
  `update_gain` (the non-disruptive path) does module-wide then overrides.
  A change to `audiomoth_settings` alone, or with `audiomoth.gain`, takes the
  non-disruptive path while streaming. Bad values (not 0-4) are logged and
  skipped; a failed per-device update is returned as an error.
- Each recording's `_timestamps.txt` sidecar gets `GAIN <n>` (the gain that
  AudioMoth ran at), as a `KEY value` trailer line `parse_mic_sidecar` skips.
- AudioMoth tab (reworked 2026-11-11): an **AudioMoths** list at the top,
  one row per known AudioMoth with a connected dot, its name (the old Labels
  tab, now removed) and its own gain; a Refresh button rescans. There is no
  module-wide gain on screen: `audiomoth.gain` stays in config only as the
  starting gain for an AudioMoth that has never been set. `update_gain` sets
  each connected AudioMoth by serial to its own gain (no untargeted update,
  which would briefly set every unit alike).
- Tests: `test_microphone_module.py` (`_gain_for`, invalid overrides,
  ordering, failed override, non-disruptive path).

**Open on the rig:** the command syntax is confirmed (above); still to do
is a save from the AudioMoth tab, a `read` showing the per-device gain, and a
recording whose sidecar carries the matching `GAIN` line.

## Phase 2: per-AudioMoth sample rate and filter

Harder, because the module assumes one rate:

- `configure_audiomoth`: one `config` per AudioMoth when any override exists.
- Recording: `_record_microphone_segment` / `soundfile` take that device's
  rate; the sidecar already lets `audio_align` read each file's rate (ffprobe).
- Monitor: `_monitor_audiomoth` FFT, Nyquist and plot range per device;
  `_render_monitor_frame` cells already per serial.
- Re-discovery: the USB device name encodes the rate, so after a per-device
  config the stored ID for that serial goes stale (`_find_audiomoths`).
- Sync calibration is keyed by serial and already stores the rate it was
  measured at; the Sync tab flags a mismatch.
- UI: per-device rate/filter next to gain, per-device file-size estimate;
  the clip warning threshold stays module-wide.

## Phase 3: microphone input (internal MEMS / external) -- built

**Decided 2026-11-11:** no setting. A unit that offers an external input
always records through it; the self-test uses the internal mic.

Built: `_refresh_inputs` (read-only `MICROPHONE` query, parsed per serial);
`_ensure_external_inputs` at module start (before streaming opens the
devices), after `configure_audiomoth`, and from `_check_audiomoths` whenever
the set of connected units changes (not while recording); device IDs are
re-scanned after any switch. `_run_sync_selftest` switches external units to
internal, runs the test, and switches back in a `finally`; the result and the
stored calibration carry `input`. Each recording sidecar gets
`INPUT internal|external`. `list_audiomoths` returns `inputs` and the
AudioMoths list shows "external mic" / "internal mic". Tests in
`test_microphone_module.py`. A no-op `microphone int <serial>` on a desk unit
showed no USB disconnect in the kernel log.

Original design notes:

Use case (2026-11-11): a rig whose AudioMoths record through an external mic
placed far from the AudioMoth, while the A/V-sync buzzer sits next to the
AudioMoth. Switching to the internal mic for `sync_selftest` puts the
microphone right at the buzzer, so the acoustic flight time is ~0 and
`mic_distance_m` doesn't need measuring.

- Discovery: run `MICROPHONE` (read-only) with the AudioMoth scan; record
  per serial whether `ext` is available and which input is active. Show an
  Input column on the AudioMoths list only for units that have `ext`.
- Setting: `audiomoth_settings.<serial>.input` (`internal`/`external`),
  applied with `MICROPHONE <INT|EXT> <serial>` after `CONFIG` (CONFIG is
  temporary, so re-apply on every configure, like the gains).
- Self-test: a `sync_selftest` option "use the internal microphone"
  (default on for units with `ext`): note each unit's active input, switch to
  internal, run the test, switch back in a `finally`. Store the input used
  with the calibration (`audiomoth_sync_offsets_ms.<serial>.input`).
- Recording sidecar: `INPUT internal|external` next to `GAIN`.

**To check before relying on it**, on a unit with an external mic:
1. Whether `MICROPHONE` switching re-enumerates the USB device (like a
   sample-rate change) or is seamless; that decides whether the monitor
   stream needs a restart and the stored device ID a refresh.
2. That the calibration offset carries across inputs: run the self-test on
   both inputs with the external mic at a measured distance and compare after
   removing flight time. The digital path (ADC → USB → PipeWire) is shared,
   so they should agree; the analog front ends differ, so measure rather
   than assume.
