# dmx_lighting

The habitat's light/dark cycle. One visible and one infrared LED strip per
compartment, on DMX LED decoders, driven from a Pi 5 through an **Enttec DMX
USB Pro** (the one with its own processor; not the "Open DMX USB"). Design and
open questions: `plans/dmx-lighting-module.md`.

- `schedule.py`: the maths, pure: `schedule_at(now, cfg)`, overrides,
  `Slew`, `build_frame`, `validate`.
- `enttec.py`: widget framing (labels 3/6/10), port discovery
  (`/dev/serial/by-id/*DMX_USB_PRO*`), and `MockWidget`.
- `dmx_lighting_module.py`: the output thread, clock gate, overrides,
  state file, event log, session CSV, commands.

It runs whether or not the controller is up or anything is recording. It
joins sessions only to write `<prefix>_lighting_(<seg>_<time>).csv`.

| Path | What |
|------|------|
| `/var/lib/saviour/lighting/state.json` | active overrides + last output levels (resume after a restart) |
| `/var/lib/saviour/lighting/events_<date>.csv` | every transition, override, fault, config change (60 days) |

`dmx_lighting.mock.enabled` runs against a simulated widget, with its own
state directory (`lighting_mock`).

Commands (`/facade/send_command` works too): `get_lighting_state`,
`set_override(boxes, visible, ir, minutes, note)`, `blackout(minutes)`,
`test_strip(box, role, seconds)`, `clear_override(override_id)`.
