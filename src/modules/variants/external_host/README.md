# External Host module

A SAVIOUR module for a device that should be a real, visible, PTP-synced
member of the fleet, but has no recording data of its own.

## Use cases

- **A pyControl host Pi**, so a pyControl session's own event log (nosepokes,
  trial markers, ...) can be compared against SAVIOUR recordings after the
  fact. The two systems only need to agree on wall-clock time — once this
  Pi is on the same PTP domain as the rest of the fleet, any wall-clock
  timestamp pyControl records (its standard session output already anchors
  to `datetime.now()` at session start) lines up with SAVIOUR's per-frame
  timestamps to the same precision every camera/mic/TTL module gets
  (documented fleet figures: <20µs mid-convergence, <5µs settled).
- Any other lab PC/Pi that wants **fleet-shared PTP time and dashboard
  visibility** without contributing a data stream — a stimulus-delivery
  box, a second acquisition system, a bench test rig.

## What it actually does

Nothing beyond what every SAVIOUR module gets from the `Module`/`Recording`
base classes: PTP slave discipline, Zeroconf registration, ZMQ heartbeat,
health reporting. `_start_new_recording`/`_start_next_recording_segment`/
`_stop_recording` are no-ops — there's no per-type data to capture — but
that's enough to make it a completely normal session participant otherwise:

- Included in `target: "all"` sessions like any other module.
- **Blocks session start if its PTP offset isn't converged**
  (`Recording._check_ptp_sync` on the controller) — the whole point of
  putting it on the fleet in the first place.
- Flagged if it degrades mid-session (`Recording._check_ptp_mid_recording`).
- Every session it's part of gets a
  `<session>_external_host_<mac>_health_metadata_(...).csv` — the base
  `Recording` class's generic per-second health sample, which already
  includes `ptp4l_offset_ns`/`phc2sys_offset_ns` (with min/max over the
  sample window). **This CSV is the actual artefact a timestamp comparison
  leans on** — durable, exported alongside the real data, not just a
  live dashboard reading you'd have had to catch in the moment.

## Setup

`sudo saviour-config` on the device → role `module`, type `External Host
(no recording)`. PTP client setup is generic to the module role, nothing
type-specific to configure. Nothing needed on the third-party software's
side beyond making sure it timestamps its own events from this Pi's system
wall clock at some point (most acquisition software already does, e.g.
pyControl's own session output).

Full design writeup: `plans/pycontrol-timestamp-sync-module.md`.
