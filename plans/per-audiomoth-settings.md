# Per-AudioMoth settings

- **Status:** in progress: phase 1 (per-AudioMoth gain) built 2026-11-11 on
  `feat/per-audiomoth-gain`, not yet confirmed on the rig; phases 2-3 not started
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
  find device ID ...`. Not yet confirmed for `config` / `update` (the
  permission checker refused a write from an agent session). Confirm with
  `AudioMoth-USB-Microphone update gain 4 <serial>` then `read`.
- Its keyword table (`strings` on the 1.0.2 binary) includes `MICROPHONE`
  with `INTERNAL`/`INT` and `EXTERNAL`/`EXT`, next to `GAIN`, `LPF`, `LGR`
  etc., and `read` output formats `(int) ext` / `int (ext)`. So the tool can
  choose the onboard MEMS mic or an external one. Our `read` output shows no
  input field, which may mean the hardware/firmware on the desk AudioMoths
  doesn't offer it. Exact syntax and hardware support unverified.

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
- AudioMoth tab: "Gain per AudioMoth", one row per known AudioMoth (label +
  serial, disconnected ones marked), default "Same as above (n)".
- Tests: `test_microphone_module.py` (`_gain_for`, invalid overrides,
  ordering, failed override, non-disruptive path).

**Open on the rig:** that `update gain <g> <serial>` is the right syntax
(if not, the module logs `AudioMoth <serial> gain update failed` and the
config card save reports the error), and that a gain read back with `read`
matches.

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

## Phase 3: microphone input (internal MEMS / external)

If the hardware supports it (see above): `audiomoth_settings.<serial>.input`
(`internal`/`external`), passed on that device's `config`. First confirm the
syntax and which AudioMoth revisions/firmware accept it, on a unit with an
external mic fitted.
