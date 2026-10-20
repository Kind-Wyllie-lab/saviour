# APA shock current: only valid setpoints, and say so when one is rejected

- **Status:** not started
- **Created:** 2026-10-20
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (one-liner)

## Why

A researcher running APA trials (note `shocksbug_201026.txt`, 2026-10-20)
saw rats not reacting at a configured 0.2 mA and asked whether the shocker
really delivers what the UI says. Tracing the setpoint path found that 0.2 mA
is set exactly, but that other typed values are silently changed or silently
ignored, and that nothing in the UI limits the input to what the hardware can
do.

## How the setpoint path works today

1. `arduino.shocker.current` is a plain number field in `GenericConfigCard`
   (`ConfigFields.jsx`: `type="number"`, no `step`/`min`/`max`). It can also
   be set with `PATCH /api/v1/modules/<id>/config`.
2. `Shocker.configure_shocker()` → `set_shock()` (`shock.py`) sends
   `C:<mA>`; Python's only check is `> 5.1`, which is dropped with a log
   warning.
3. Firmware (`shock_controller.ino`, `handleCommand` / `calculateCurrentOutput`)
   rejects `< 0` or `> MAX_CURRENT_MA` (1.0) with an `E:` message, otherwise
   `(int)(mA * 1000) / 20` → 8-bit value on the DB25 data lines.
4. HSCK100A DB25 (manual: `arduino/shock_controller/manual.md`): Data0-7 =
   0.02, 0.04, … 2.56 mA, active low. **Valid setpoints are multiples of
   0.02 mA**; the firmware's 1.0 mA cap leaves 50 of them (0.02-1.00).
5. The Arduino reads its own output pins back every 50 ms; `calculate_shock()`
   turns them into `current_from_arduino`, shown as "Current" on the APA
   panel. This is what was sent down the cable, **not** a measurement, and is
   meaningless if the shocker's REMOTE/LOCAL switch is on LOCAL (the front
   knob then sets the current and DB25 is ignored).

0.2 mA → 200 µA → 10 → `00001010` → 0.04 + 0.16 = 0.20 mA, exact. A
simulation of the float → int path over 0.01-1.00 showed every multiple of
0.02 lands exactly.

## What goes wrong

| Typed | Result | Surfaced? |
|---|---|---|
| Not a multiple of 0.02 (0.25, 0.15, 0.29…) | floored (0.24, 0.14, 0.28) | no |
| 0.01 | 0 mA (firmware accepts it) | only as "cannot activate" |
| > 1.0 and ≤ 5.1 | firmware `E:`, **previous setpoint kept** | module log |
| > 5.1 or < 0 | dropped / `E:`, **previous setpoint kept** | module log |

Knock-on: `validate_state()` (`shock.py`) compares `100*current_from_arduino`
against `100*current - round((100*current) % 2, 2)` with float `!=`. It flags
a false mismatch for 0.07, 0.15, 0.29 and 0.55-0.59 mA (0.58 is a valid,
exact setpoint), and for every rejected value above. Each mismatch calls
`configure_shocker()`, which sleeps 0.2 s **inside the serial listen thread**
and re-sends C/T/Y, so it repeats on roughly every state packet. While a shock
sequence is running, that delays the state packets that delivery detection
depends on.

Misleading docs: `_current_step: 0.2` in `apa_arduino_config.json` (unread;
the real step is 0.02), and the firmware header/comments say "0.2mA steps" and
"0.0-5.1mA" while the code caps at 1.0.

## Plan

### 1. Backend: one source of truth for valid levels (`shock.py`)

- Add `CURRENT_STEP_MA = 0.02` and `MAX_CURRENT_MA = 1.0` (matching the
  firmware cap) plus a helper that turns mA into an integer step count
  `round(mA / 0.02)` and rejects the value unless it is within 1e-6 of an
  exact step and between 1 and 50 steps.
- `set_shock()` uses it: send the value rebuilt from the step count, so the
  string the firmware sees is exactly a multiple of 0.02.
- Replace `validate_state()`'s current check with an integer step comparison
  (`round(current_from_arduino / 0.02) == steps`). Drop the float `%`.
- Move the reconfigure-on-mismatch out of the listen thread (set a flag, act
  on it from the state loop) or at least rate-limit it, so a persistent
  mismatch can't loop on every packet.

### 2. Backend: reject bad values instead of keeping the old one

`Module.set_config()` has no per-module validation hook today; it just calls
`config.set_all()`. Add an overridable `validate_config(config) -> str | None`
called before `set_all`, returning an error that `set_config` passes back as
`{"result": "error: …"}`. `APAArduinoModule` implements it for
`arduino.shocker.current` (and sanity checks `duration`/`intershock_latency` >
0 while there). The REST `PATCH` and Socket.IO paths both go through
`set_config`, so both get it. Check how `web.py`/`rest_api.py` handle a
non-success `set_config` result, and that it reaches the operator.
`module_config_error` exists but the frontend has no listener (CLAUDE.md open
item), so route it through whatever the config card already shows for a
failed save, or add the listener as part of this.

### 3. Frontend: a drop-down of levels

- New `APAArduinoConfigCard` (same pattern as `TTLConfigCard` /
  `APACameraConfigCard`, dispatched from `ConfigCard.jsx` on
  `module.type === "apa_arduino"`), rendering `arduino.shocker.current` as a
  `<select>` and everything else through the generic fields.
- **Open decision:** which levels are listed. Recommendation: every valid
  level, 0.02-1.00 in 0.02 steps (50 options), to match the hardware and
  firmware cap. Alternative: the lab's working set (e.g. 0.1-1.0 in 0.05 or
  0.1 steps). Either way, generate the list from the step/max constants. A
  stored value not in the list (an old config) shows as "0.25 mA (invalid:
  will be rejected)" rather than silently snapping.
- Label the APA panel's "Current" as the commanded setpoint
  ("Setpoint (DB25)"), and add a hint that the shocker must be on REMOTE.

### 4. Cleanup

- `_current_step` → `0.02`; add `_max_current: 1.0` to match the firmware
  (currently `5.1`), or drop both keys if step 1 makes them redundant.
- Firmware header and comments: 0.02 mA steps, 1.0 mA cap. Comment-only,
  so no reflash needed.
- `set_shock()`'s `> 5.1` check is superseded by step 1.

## Tests

- `src/modules/tests/test_apa_arduino_module.py`: the step helper accepts
  every 0.02 multiple in 0.02-1.00 and rejects 0, 0.01, 0.25, 1.02, 5.2, -0.2;
  `validate_state` raises no mismatch for any valid level (regression for
  0.58); `validate_config` returns an error for a bad current and `set_config`
  leaves the stored value unchanged.
- Frontend: `npm run build` for all variants; the APA build shows the
  drop-down (check with `npm run dev:mock` fixtures if they cover
  `apa_arduino`).
- On the rig: set 0.2 and 0.58, confirm the panel and the module log agree,
  with no repeated "Current set to … Arduino reports …" warnings.

## Related, out of scope here

- **Delivery verification for the researcher's question** already exists:
  the shocker's TEST_OUT (DB25 pin 11 → Arduino pin 2) drives "delivered" on
  the APA panel and `SHOCK_DELIVERY` rows in `<prefix>_shock_events.csv`; the
  shocker's front STATUS LED flashes red on delivery. Worth a per-session
  delivered/attempted summary, and showing "delivered" more prominently,
  as a separate item.
- **The 50-shock cap counts pulses, not entries**, and after 50 the
  hold-to-shock button is disabled for the rest of the trial. Researchers
  need to know this when reading shock counts. Document it in the UI tooltip.
- `attempted_shocks_from_arduino` is always 0 (the firmware only counts
  Timer1 pulses; the Pi drives the trigger pin directly).
- Arduino pin 2 (`SELF_TEST_IN`) is a plain `INPUT`; a loose TEST_OUT wire
  floats and could count phantom deliveries.
