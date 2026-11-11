# DMX lighting module (habitat day/night cycle via Enttec DMX USB Pro)

- **Status:** built 2026-11-11 (branch `feat/dmx-lighting-module`), not yet run on hardware; the patch waits on the installer (open questions below)
- **Created:** 2026-11-11
- **Owner:** Andrew SG
- **CLAUDE.md ref:** Reliability / UX - "DMX lighting module"

A new module type, `dmx_lighting`, for the habitat. It drives the
compartment LED strips through an Enttec DMX USB Pro on a Pi 5 and runs a
light/dark cycle with a sunrise and sunset ramp. It runs continuously in the
background, independent of recording sessions and of the controller being
up. It joins sessions only to report its health and light status. The
schedule and the strip patch are edited from the frontend.

## Built (2026-11-11)

Write-up in `docs/CHANGELOG.md` (2026-11-11). Everything in this spec exists
as described, tested against the mock widget only. Where it differs:

- **One new Socket.IO event, `lighting_state`.** The heartbeat (30 s) is too
  slow for an override countdown, so `web.py` relays the module's pushed
  state (every phase change, override, fault, config change) and the
  replies to the lighting commands. The heartbeat still carries the
  `lighting` block (guests can't send commands, so it's their source).
- **Session file** is `<prefix>_lighting_(<seg>_<time>).csv` like every
  other per-segment file, rotated with segments. Columns: `timestamp_ns`,
  `timestamp_utc`, `mode`, `phase`, `event`, `detail`, then
  `<box>_visible`/`<box>_ir` levels (percent, before trim). The schedule
  and patch reach the session through the existing `config.json` export.
- **`mode`** (shown in the UI and the CSV) is the schedule phase unless
  something else is in charge: `override`, `blackout`, `test`, `fault` (no
  widget), `waiting_for_clock`, `config_error`.
- **IR mode `off`** added beside `constant` / `night_only`.
- **Compact status** went on the habitat dashboard (a Lighting card in the
  left column, with "Lights on 30 min" / "Blackout 30 min" / End for
  admins), not the legacy `ModuleCard`.
- **Clock trust** = PTP offsets (ptp4l and phc2sys) within 1 ms over the last
  10 s, or `timedatectl` NTPSynchronized; latched for the life of the
  process. The widget is opened (and a missing one reported) while waiting.
- **Controller alert** (`health.py` `_check_lighting`): error on a widget
  fault or a refused config, warning after 10 min without a trusted clock.
- `dialout`: not needed, `saviour.service` runs as root.

Not done: hardware acceptance (the list under "Testing and acceptance");
lights on the habitat live map; dated schedule changes (out of scope).

## Background: DMX channels

DMX512 is a one-way serial bus. One cable carries one **universe** of 512
channels; each channel is one byte (0-255). The Enttec widget resends the
whole frame continuously, about 40 times a second, and each receiver listens
to its own slice of it, starting at its configured **start address**.

**Habitat install:** 16 compartments, each with one visible LED strip and one
infrared (IR) strip, so **32 dimmer outputs**. LED strips aren't DMX devices
themselves: they hang off DMX LED dimmer/decoder boxes, each with a few
outputs, and each output takes either 1 channel (8-bit) or 2 channels
(16-bit: coarse + fine) depending on the decoder's mode. So the install uses
32 or 64 of the 512 channels: one universe and one widget is plenty.

8 vs 16 bit matters for the sunrise: at 8 bits the lowest steps are 0.4% of
full each, and a 30 min ramp up from dark visibly steps near the bottom. Use
16-bit mode if the decoders offer it.

**Still needed from whoever installs the lighting** (blocks the patch, not
the rest of the build):
1. Decoder make/model, outputs per decoder, 8- or 16-bit mode, and the
   start address of each decoder (or each strip).
2. Which output drives which compartment's visible / IR strip.
3. The decoders' **DMX-signal-loss behaviour** (hold last level, go dark, or
   go to a preset). This decides what happens if the Pi or widget dies; see
   Failure modes.
4. IR wavelength (850 vs 940 nm) and whether IR should be on 24 h (see IR
   below).

## Hardware: Enttec DMX USB Pro

- FTDI chip; shows up as `/dev/ttyUSB*` through the stock `ftdi_sio` driver,
  stable path `/dev/serial/by-id/usb-ENTTEC_DMX_USB_PRO_<serial>-if00-port0`.
  No vendor driver or library needed; `pyserial` is already a dependency.
- Has its own processor. The host sends framed messages; the widget generates
  DMX timing and **keeps retransmitting the last frame on its own** until told
  otherwise. The host only needs to send when levels change (plus an
  occasional keepalive so a reconnect is noticed).
- Message frame: `0x7E, label, len_lsb, len_msb, data..., 0xE7`.
  - Label 6 "Output Only Send DMX Packet": data = start code `0x00` + 24-512
    channel bytes. The only message needed for output.
  - Label 3 "Get Widget Parameters": firmware version, break/MAB time,
    refresh rate. Use at connect as a "the widget is really there and
    talking" check and log the values.
  - Label 10 "Get Widget Serial Number": log it, so a swapped widget is
    visible.
- Serial baud setting is ignored (USB virtual COM). Not the "Open DMX USB"
  (no processor, bit-banged by the host), which this plan doesn't support.
- The widget is bus-powered from the Pi; the decoders and strips are on
  separate power, so a Pi reboot drops the DMX signal while the strips stay
  powered.

## Behaviour

### Schedule

One daily cycle, in local lab time:

- `lights_on` / `lights_off` (HH:MM). Either order: `19:00` on / `07:00` off
  is a reversed cycle crossing midnight, which must just work.
- `sunrise_minutes` (default 30): starting at `lights_on`, the visible strips
  ramp from their **night (low)** level to their **day (high)** level over
  this period. Lights on at 07:00 with 30 min means the ramp runs
  07:00-07:30.
- `sunset_minutes` (default 30): the same in reverse, starting at
  `lights_off`.
- `curve`: shape of the ramp.
  - `perceptual` (default): cosine ease in perceived brightness, converted to
    output with the CIE L* inverse. Perceived brightness is roughly
    logarithmic, so a linear ramp looks like it jumps on in the first minutes
    and then barely changes. This one looks like a steady dawn.
  - `cosine`: cosine ease in output units.
  - `linear`.
- `day_level` and `night_level` (0-100%) for the visible strips. Night can
  be 0 or a dim level.

The level at any instant is a pure function of `(now, config)`. No state
machine and no "transition started at" memory, so restarts, clock steps and
config edits all resolve to the right level immediately.

Daylight saving: compute in the configured IANA time zone (`zoneinfo`,
default = system zone), so `07:00` means 07:00 local wall time; on the two
change days the dark phase is 1 h shorter/longer. `timezone: "UTC"` keeps
the animals on non-shifting time. **Decide with the facility which they
use.**

Dated schedule changes (phase shifts, constant dark/light blocks) are out of
scope; the pure-function design leaves room to add them later if needed.

### IR strips

Rodents can't see near-IR, and the cameras need it to see at night, so the
IR strips don't follow the visible cycle by default. Each channel role has a
mode:

- visible: `cycle` (default) - follows the schedule above.
- IR: `constant` (default, at `ir_level`) or `night_only` (on during the dark
  phase, ramping opposite to the visible strips, off during the day).

Note 850 nm IR emitters show a faint red glow that rodents may detect; 940 nm
doesn't. If the habitat uses 850 nm, `night_only` or a lower level may be
preferable. Ask the researchers.

### Patch (compartments to channels)

One row per compartment, keyed by the habitat box name (letter = row,
number = column, the same names as the habitat layout and Placement panel):

```json
"compartments": [
  {"box": "A1", "visible": 1, "ir": 2},
  {"box": "A2", "visible": 3, "ir": 4}
]
```

The numbers are DMX start addresses. `channel_bits: 8 | 16` sets the mode
for all strips (in 16-bit mode each strip takes `address` and `address+1`).
Optional per-strip `trim` (0-100%, default 100) scales that strip's output so
compartments can be matched by eye or with a lux meter, since strips age and
vary.

Validation: duplicate or overlapping addresses, addresses past 512, duplicate
box names. The UI shows the resulting channel map.

### Output loop

- A dedicated thread computes the frame at 10 Hz and sends label 6 only when
  a byte changed, plus a keepalive resend every 2 s.
- **Slew limit** on scheduled output (default: no faster than full scale
  over 60 s), so a clock step, a config edit mid-day, or a restart after a
  power cut fades to the right level instead of flashing. Manual override
  and blackout bypass it (they're deliberate).
- Frame computation in a pure function (`schedule.py`), I/O in `enttec.py`,
  so both are unit-testable without hardware.

### Manual override

For husbandry checks or cleaning: "set these compartments (or all) visible
and/or IR to N% for M minutes, then return to the schedule". The duration is
**required** (max e.g. 4 h): there's no permanent override, so nobody can
leave the lights on overnight by forgetting. Overrides survive a module
restart (stored with their expiry time) and are logged. "Return to schedule
now" ends one early. Admin-only in the frontend.

### Strip test

To build and check the patch: flash one strip while the rest hold their
scheduled level, from the patch table. Same auto-expiry as an override
(30 s).

### Recordings

Output never changes during sessions. The module joins sessions (like
`external_host`) to report health and light status:

- Gets the generic per-session PTP/health CSV for free.
- Writes `lighting.csv` per session: one row per second while levels are
  changing (ramps, overrides) and one per event otherwise (phase change,
  override start/end, fault, config change). Columns: PTP wall time, phase
  (`day` / `night` / `sunrise` / `sunset` / `override` / `fault`), and the
  level of each strip.
- Writes the active schedule and patch into the session metadata.

Two consequences of joining:
1. `web.py` refuses config edits to a module that's recording
   (`module_config_error`, around `web.py:729`). The schedule must stay
   editable whatever is recording, so exempt `dmx_lighting` from that gate
   (the edit lands in `lighting.csv` instead).
2. A missing widget must **not** block session start. Report it as a health
   fault (`hardware_fault`, the System page's "NO HARDWARE" badge, and a
   fault alert), not as a failing readiness `@check()`. A lighting fault
   matters, but losing the recording too makes it worse.

Outside sessions it keeps a rolling local event log
(`/var/lib/saviour/lighting/events.csv`: transitions, overrides, faults,
config changes, retained N days) so lighting history exists between sessions
too.

## Failure modes

| Failure | What happens | Mitigation |
|---------|--------------|------------|
| Controller down | Nothing changes: the schedule lives in the module's active config and runs on the module's own clock. | By design. |
| Pi/process dies | Widget keeps sending the last frame: lights **freeze** at their current level. | systemd restart + watchdog (existing `sd_watchdog`); slew limit makes the resume smooth. |
| Widget unplugged / Pi off | DMX stops; decoders do their own signal-loss behaviour. | Ask the installer to set decoders to "hold last" (or a safe preset). Module retries the port with backoff, sets `hardware_fault`, alerts. |
| Clock wrong at boot | A Pi 5 RTC without a battery boots at a stale time; the schedule could put day lighting on at night. | Don't drive the schedule until the clock is trusted (PTP locked, or `timedatectl` NTP-synchronised). Until then send nothing (decoders hold/loss behaviour) rather than guess. Log how long it waited. |
| Clock step mid-run (PTP re-lock) | Level would jump. | Slew limit. |
| Bad config pushed | Wrong lights. | Validate on the module before applying; reject with an error and keep the old config. The UI previews the 24 h curve before saving. |

Freeze-on-crash is the main animal-welfare risk (lights stuck on overnight
or off all day). The watchdog makes it minutes, not hours; the controller's
existing module-offline alert covers the rest.

## Frontend (habitat)

New `DmxLightingConfigCard` in `basic/components/ConfigCard/` (so it's
available to any variant, but habitat is the target), dispatched in
`ConfigCard.jsx` on `module.type === "dmx_lighting"`. Use an exact match:
`lightning_camera` is a different module and a substring test is an easy
mistake. Tabs:

1. **Schedule:** lights on/off times, sunrise/sunset minutes, curve, time
   zone, day/night levels, IR mode and level. A 24 h SVG preview of the
   visible and IR curves with a "now" marker, redrawn as fields change,
   before saving.
2. **Compartments:** patch table (box, visible address, IR address, trim),
   validation errors inline, the resulting channel map, a "test" button per
   strip.
3. **Status:** current phase, levels, next transition and when, active
   override with time remaining, widget serial/firmware, last fault.

Plus a compact status on the module card (phase, level, next transition) and
the override control (admin only). Later: show each compartment's light
state on the habitat dashboard's live map, since the patch is keyed by the
same box names.

Live state rides on the existing heartbeat/health payload (a `lighting`
block: phase, levels, next transition, override). No new Socket.IO events
for read-only state; override/test/clear go through the existing command
path, so `/facade/send_command` can drive them too.

## Backend pieces

| File | Contents |
|------|----------|
| `src/modules/variants/dmx_lighting/variant.conf` | `NAME="DMX Lighting"`, description. No apt packages. |
| `.../dmx_lighting_config.json` | Defaults (below). |
| `.../dmx_lighting_module.py` | `Module` subclass: output thread, override persistence, session CSV, health block, commands. |
| `.../schedule.py` | Pure: `(now, config, override) -> per-strip levels`, phase and next transition; patch to 512-byte frame. |
| `.../enttec.py` | Widget I/O: open by-id path, framing, labels 3/6/10, reconnect. Plus a mock widget for tests and frontend work (`dmx_lighting.mock.enabled`, like `rfid.mock`). |
| `src/modules/tests/test_dmx_lighting_*.py` | Schedule math, framing, module behaviour with the mock. |
| `src/controller/web.py` | Exempt `dmx_lighting` from the config-while-recording gate. |
| `saviour-config` | Hardware probe: match the Enttec by udev `ID_VENDOR`/`ID_MODEL` (`DMX_USB_PRO`) and suggest `dmx_lighting`; today any `/dev/ttyUSB*` suggests `rfid`. Check `pi` is in `dialout`. |

Commands: `get_lighting_state`, `set_override(boxes, visible, ir, minutes)`,
`clear_override()`, `test_strip(box, role, seconds)`, `blackout(minutes)`
(visible to zero everywhere, IR unchanged; same auto-expiry).

Default config sketch:

```json
{
  "module": {"group": "lighting"},
  "dmx_lighting": {
    "serial_port": "",
    "timezone": "",
    "schedule": {
      "lights_on": "07:00",
      "lights_off": "19:00",
      "sunrise_minutes": 30,
      "sunset_minutes": 30,
      "curve": "perceptual",
      "day_level": 100,
      "night_level": 0
    },
    "ir": {"mode": "constant", "level": 100},
    "channel_bits": 8,
    "compartments": [],
    "slew_full_scale_s": 60,
    "max_override_minutes": 240,
    "require_synced_clock": true,
    "mock": {"enabled": false},
    "_send_hz": 10,
    "_keepalive_s": 2
  }
}
```

## Testing and acceptance

Unit (no hardware):
- Level at sampled times for normal and midnight-crossing schedules and each
  curve; ramps start exactly at `lights_on`/`lights_off` and reach exactly
  the day/night level after `sunrise_minutes`/`sunset_minutes`.
- IR `constant` and `night_only` modes.
- DST spring-forward and fall-back days in `Europe/London`; `UTC`
  unaffected.
- Override expiry, persistence across a simulated restart, slew limiting
  after a simulated clock step, clock-not-synced hold, trim scaling.
- Frame encoding: label 6 bytes for a known patch, 16-bit coarse/fine split,
  overlap/out-of-range/duplicate-box validation.

On hardware (desk fleet with a DMX tester or one decoder, then the habitat):
1. Widget detected by `saviour-config`, by-id path opened, label 3/10 values
   logged.
2. Each of the 32 strips responds to its "test" button, and only that strip.
3. Run a compressed schedule (e.g. 2 min ramps) and watch a full cycle,
   checking the cameras still see at night with the IR setting.
4. Kill the process mid-ramp: lights freeze, service restarts, output fades
   to the correct level.
5. Unplug the widget: fault shows on the System page, reconnects on replug.
6. Reboot the module with the controller down: lights resume on schedule
   once the clock is trusted.
7. 72 h unattended run (could ride along with the test D soak).

## Open questions

1. Decoder model, 8/16-bit mode, addresses, strip-to-compartment wiring,
   signal-loss behaviour (from the installer).
2. Local time with DST vs fixed time for the animals.
3. IR wavelength and whether IR is on 24 h or only at night.
