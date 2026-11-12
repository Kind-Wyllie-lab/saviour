# TTL session schedule and a session timeline (Gantt) view

- **Status:** planned 2026-11-12, nothing built. Waiting on the requesting
  user's actual protocol (see "Open questions").
- **Created:** 2026-11-12
- **Owner:** ascottg
- **CLAUDE.md ref:** Reliability / UX → "TTL session schedule"

## Why

A user wants the TTL module to run a timed protocol from session start:
"set this pin high at session start, after 3 minutes do this, then this".
Today each output pin has one fixed mode (`experiment_clock`,
`pseudorandom`, `interval_pulse`, `experiment_start`), so a sequence of
different steps across pins can't be expressed.

The second half is a **timeline view**: rows per module/pin, time from
session start, showing what was planned and what actually happened. It is
useful well beyond TTL (recording on/off and gaps, lights, APA grid armed,
habitat clips, sync pulses), so it's designed as a general session timeline
with TTL as its first planned-vs-actual source.

## Decisions

- **The module runs the schedule; the controller stores and edits it.**
  The schedule is TTL module config (`ttl.schedule`), edited from the
  frontend like other module config. The module runs it against the
  session's start on its PTP-disciplined clock, so timing doesn't depend on
  the network, and a controller outage mid-session doesn't stop it. This is
  the DMX lighting module's model (`plans/dmx-lighting-module.md`), not APA
  shocking's (that needs live tracking, a schedule doesn't).
- **Fixed timelines only, no conditionals.** No "when input X fires, do Y":
  that grows into a state-machine language, and pyControl already is one
  (`external_host` module, `plans/pycontrol-live-event-bridge.md`). The
  editor and docs say so and point there.
- **No edits while a session runs.** Same rule as other module config while
  `RECORDING` (`save_module_config` already rejects it).
- **Millisecond timing, stated.** Steps run from a Python thread; good to a
  few ms, ample for minutes-scale protocols. Sub-ms needs (precise pulse
  trains, high rates) are out of scope for v1; the editor warns on pulse
  widths or rates below a stated floor (start with 5 ms / 50 Hz, measure).
- **Absolute deadlines.** Every step's time is `session_start + offset`, and
  the worker sleeps until that deadline, so lateness never accumulates.
  (The existing generators sleep relative intervals, so a long
  `interval_pulse` run drifts by the pulse widths and scheduling delays;
  worth fixing the same way, separately.)
- **Built for TTL, shaped for any module.** A step's target is
  `{module, action, params}`; v1 accepts only the TTL module's own pins.
  Later the same format could schedule a tone on `sound` or a light change,
  but that needs the controller (or one module) to drive other modules, so
  it's a later decision, not v1.

## Schedule format (v1)

`ttl.schedule` on the TTL module:

```json
{
  "version": 1,
  "name": "habituation then tones",
  "seed": 1234,
  "end_state": "all_inactive",
  "steps": [
    {"at_s": 0,   "pin": 17, "action": "set",   "value": "high"},
    {"at_s": 180, "pin": 17, "action": "set",   "value": "low"},
    {"at_s": 180, "pin": 22, "action": "pulse", "width_s": 0.5},
    {"at_s": 300, "pin": 22, "action": "train", "rate_hz": 10,
     "width_s": 0.02, "count": 50},
    {"at_s": 600, "action": "repeat", "times": 10,
     "every_s": 60, "jitter_s": [0, 20],
     "steps": [{"at_s": 0, "pin": 23, "action": "pulse", "width_s": 1.0}]}
  ]
}
```

- `at_s`: seconds from session start (for a `repeat`'s inner steps, from
  that repetition's start).
- Actions: `set` (high/low, held until changed), `pulse` (one, width),
  `train` (rate, width, count), `repeat` (a block of steps, `times`,
  `every_s`, optional `jitter_s` range drawn from `seed`).
- Pins used by the schedule must be configured with a new pin mode,
  `scheduled` (an output owned by the schedule), so a pin can't be both a
  generator and scheduled.
- `end_state`: what outputs do when the schedule ends, the session stops,
  or the controller is lost: `all_inactive` (default) or `hold`. Possibly
  per pin (open question).

**Expansion.** The module (and the frontend, from the same rules) expands
the schedule into a flat list of edges `(t_s, pin, high|low)` before the
session starts: repeats unrolled, jitter drawn from `seed`, trains to
individual edges. Validation runs on that list: overlapping steps on one
pin (a `pulse` while `set` holds it high), widths ≥ the period in a train,
pins not in `scheduled` mode, schedule longer than a set session duration.
Errors block saving; a schedule that outlasts an open-ended session is a
warning. One shared implementation: `src/shared/ttl_schedule.py` plus a JS
twin in the frontend, both tested against the same fixture cases
(`src/shared/` already serves both sides).

## Running it (module)

- On recording start, the module expands the schedule, writes it next to
  the TTL CSV as `<session>_ttl_schedule.json` (the schedule as given, the
  seed, and the expanded edge list), and starts one worker thread.
- The worker sleeps until each edge's deadline (`time.time()` against the
  session start time the module already uses for the CSV), sets the pin and
  writes the edge with `_write_ttl_event`, tagged `source=schedule` and the
  step index, so planned and actual edges pair up in analysis.
- If it wakes late by more than a threshold (start with 20 ms), it still
  fires and logs the lateness; the per-edge lateness is in the CSV.
- On stop, controller loss (the existing disconnect grace) or an error, the
  worker applies `end_state`. `_set_all_output_pins_inactive` already
  exists.
- Heartbeat / status: the current step, next edge time, and late-edge count,
  so the live view and a health check can show it.

## The timeline (Gantt) view

One component, three uses:

1. **Preview while editing** (TTL config card): the expanded schedule as
   bars per pin, with validation problems marked on the bars. Editing is a
   table of steps; the chart is the preview. Dragging bars is a later nicety.
2. **Live** (session page): a "now" line; planned bars in a light tone, the
   actual edges (from the module's status) drawn over them.
3. **After the session** (session detail / Post-Process): planned from
   `_ttl_schedule.json`, actual from the TTL CSV. Late or missing edges are
   marked. Later, other rows from data that already exists: each module's
   recording span and recorded gaps, DMX lighting CSV, APA armed/shock
   events, habitat clips, sync pulses. A compose tile version can follow.

Rows: one per pin (named by the pin's label if it has one), grouped by
module. Time axis in mm:ss from session start, with wall time on hover.
Follows the frontend's existing chart conventions; check the dataviz rules
(one axis, colour by row identity, a legend, a table view of the edges).

## Phases

1. **Format + expansion + validation** (`src/shared/ttl_schedule.py`, the
   JS twin, shared fixtures, tests). No UI, no hardware.
2. **Module runner**: `scheduled` pin mode, the worker, CSV tagging, the
   schedule export, end-state handling, status fields. Tests with mocked
   pins and a fake clock; then an on-device check with a scope or a second
   TTL module recording the outputs as inputs (lateness distribution).
3. **Editor + preview Gantt** on the TTL config card, in all variants that
   show TTL config; `dev:mock` support.
4. **After-session timeline** from the session files.
5. **Live timeline** on the session page.
6. Later: other modules' rows in the timeline; deadline-based timing for the
   existing generators; scheduling other modules' commands.

## Open questions (for the requesting user)

- What does the real protocol look like: how many pins, how long, how many
  steps? Do they need repeated trials, randomised gaps, pulse trains?
- What should outputs do if the session stops early or the controller is
  lost: all off, or per pin (e.g. a light that should stay on)?
- Is "from session start" always the reference, or do they need "from the
  first input edge" (that's a conditional: pyControl territory)?
- What timing precision do they need for pulse widths?

## Tests

- Shared fixtures: expansion (repeats, trains, seeded jitter reproducible),
  each validation error, both implementations agree.
- Module: edges fire at deadlines with a fake clock; no drift over a long
  schedule; late edges logged; stop mid-schedule applies `end_state`; a
  `scheduled` pin rejects test pulses during a session (or doesn't: decide);
  CSV and `_ttl_schedule.json` contents.
- Frontend: preview renders the expanded list; validation marks; lint/build
  for every variant.
