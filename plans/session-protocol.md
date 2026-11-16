# Session protocols: module capabilities, timed schedules, event rules and a session timeline

- **Status:** proposed 2026-11-16. Supersedes the TTL-only
  `ttl-session-schedule.md` (2026-11-12). **First slice built 2026-11-16,
  not yet on hardware** (see "Built so far"): the shared expansion and
  validation, and the TTL runner with a `scheduled` pin mode, with the
  protocol carried in the TTL module's config for now. Phase 1's final
  format still waits on the requesting user's protocol.
- **Created:** 2026-11-16
- **Owner:** ascottg
- **CLAUDE.md ref:** Reliability / UX → "Session protocols"

## Why

A user wants the TTL module to run a timed protocol from session start ("set
this pin high at session start, after 3 minutes do this, then this"). The
sound module needs the same thing (a tone at fixed times, startle trials with
random gaps), and today it only plays on command, with the file chosen
beforehand as a setting. Building a TTL-only scheduler would add a fifth
one-off version of something SAVIOUR already has four of:

| Existing | Trigger | Action | Runs on |
|---|---|---|---|
| Habitat recording plans (`recording_plans.py`) | time windows | start/stop recording | controller |
| DMX lighting schedule | time of day | light levels | module |
| APA automated shocking (`shock_automation.py`) | `zone_entered` event | shock | controller |
| Habitat motion trigger | motion | record a clip | module |

So build one model: modules advertise what they can do and what they report,
a session carries a protocol made of timed steps and (later) event rules, and
one timeline view shows what was planned against what happened.

## Concepts

### Capabilities, not a sensor/effector split

A fixed sensor/effector category breaks at once: TTL is both, a camera senses
but "record" is something you'd schedule, the APA camera reports zone events.
Instead each module advertises two lists:

- **Actions**: what it can be told to do, with typed parameters, e.g.
  `ttl.set(pin, level)`, `ttl.pulse(pin, width_s)`,
  `ttl.train(pin, rate_hz, width_s, count)`, `sound.play(file, volume, duration_s)`,
  `<camera>.record`.
- **Events**: what it reports, with fields, e.g. `ttl.edge(pin, level)`,
  `apa_camera.zone_entered`, `rfid.read(antenna, tag)`.

"Effector" is then just a module with actions. The schedule editor builds its
menus from the advertised lists, so a new module's actions appear without
frontend work.

### Instant actions and spans

- **Instant**: happens at a time and is done (`pulse`, `play`, a `train` is a
  short burst of instants).
- **Span**: holds a state over an interval, with a start and an end (`set`
  holding a pin high, `record`, a light level). The timeline draws spans as
  bars; a span also says what state the module should be in, which the
  liveness check can test against.

### Recording is a span; the session stays the container

The session is not a step in its schedule. Its start is t = 0 for every step,
and it keeps everything it owns today: readiness checks, the PTP start gate,
membership, liveness, gap records, export.

Inside it, recording is a `record` span per bound module, and what exists
today becomes the simplest protocol:

- **A plain session**: every bound module records from 0 to the session's
  end. **A protocol with no `record` rows means exactly this**, so existing
  sessions, the REST API and pyControl integration don't change.
- **Habitat recording plans**: per-module `record` windows (later migrate the
  plans onto this; not phase 1).
- **The motion trigger**: an event rule firing `record_clip(duration_s)`
  (phase 3).

## Built so far (2026-11-16)

The TTL half of phase 1's runner, testable with one TTL module:

- `src/shared/session_protocol.py`: v1 format for `set` / `pulse` / `train`
  and `repeat` (seeded jitter via mulberry32, checked against JavaScript so
  the frontend twin can match), expansion to time-ordered edges, validation
  (overlaps per pin, train width ≥ period, unknown actions/params, pins
  that aren't `scheduled`, a 200k edge cap) and timing-floor warnings.
- TTL module: pin mode `scheduled`; the protocol in `ttl.protocol` (module
  config, a stand-in until protocols live on the session). An invalid
  protocol refuses the recording (`recording_start_failed`, reason
  `ttl_protocol`). One SCHED_FIFO worker fires each edge at
  `session_start + t` (sleep to 2 ms before, then spin) and logs it in the
  TTL CSV as `[protocol step=<path> <action> late_ms=<x>]`;
  `<csv stem>_protocol.json` holds the protocol, t0 and every planned edge.
  Stop drives held pins low (logged `protocol stopped`); natural end
  applies `end_state`; a runner crash fails liveness. Commands
  `validate_protocol` and `protocol_status` (via `/facade/send_command`).
  `pulse_pin`/`test_pin` on a scheduled pin are refused while recording.
- **Re-arm keeps the session clock.** The controller's first
  `start_recording` carries `start_at`; its re-arms don't. The module
  saves t0 in `/var/lib/saviour/ttl_protocol_state.json` (session name +
  protocol hash), and a start without `start_at` in the same session with
  the same protocol resumes: past edges are skipped and counted as missed,
  held levels restored (`protocol resume`). When protocols move to the
  session, the controller should send the session t0 explicitly instead.

Rig test (one TTL module, a jumper from a scheduled output to an `input`
pin): timing (lateness of each input edge against its planned time), drift
(1 Hz for 30-60 min), early stop, manual pulse refused, same seed same
gaps, re-arm mid-protocol. Set the late threshold from the results (the
code uses 20 ms, `LATE_THRESHOLD_S`).

## Two execution tiers

1. **Timed steps run on the modules.** The controller expands the session's
   protocol and sends each module its own part before the session starts.
   Each module runs its part against the session start on its PTP-disciplined
   clock, so cross-module timing is as tight as PTP (tens of µs), nothing
   depends on the network mid-session, and a controller outage doesn't stop
   it. This is the DMX module's model and the old TTL plan's.
2. **Event rules run on the controller.** "Camera A records 60 s when TTL B
   sees a button press" crosses modules, so something has to route the event;
   that's the controller, as APA shocking does today. Routing adds ms to tens
   of ms: fine for starting a clip (more so with a pre-roll buffer), not for
   fast closed-loop work, which stays in pyControl (`external_host` module,
   `plans/pycontrol-live-event-bridge.md`). Rule language stays small:
   trigger event + field filter, one action, a refractory period, a maximum
   count. No variables, no state machines.

Every action in the manifest can be used by a timed step or an event rule;
there is no per-rig allow-list (decided 2026-11-16). Safety checks therefore
live inside the action, not in the rule layer: a shock action goes through
the same armed/disarmed and mode checks as today (`shock_automation.py`),
whoever calls it, so a rule can't pulse a disarmed grid.

## Capability manifest

- `@command()` already registers module RPCs (`module.py`). Add an
  `@action(params={...}, kind="instant"|"span")` decorator for the schedulable
  subset (an action is also a command, so `/facade/send_command` can still
  fire it by hand) and an `@event(fields={...})` declaration for what a module
  reports.
- Parameters are a small typed schema (number with unit and range, enum,
  pin from the module's own pin list, file from the module's own file list),
  enough for the editor and for validation on both sides; not JSON Schema.
- The module sends a `capabilities` status message on connect and when its
  config changes (pins, sound files). The `hello` frame stays a bare
  registration. The controller keeps the latest per module and serves it to
  the frontend.

## Protocol format (v1)

A protocol belongs to the session (created with it, saved in
`sessions.json`, exported as `<session>_protocol.json`), so it can name
several modules. A reusable protocol can be saved as a template (controller
config), the same way compose layouts are.

```json
{
  "version": 1,
  "name": "habituation then tones",
  "seed": 1234,
  "end_state": "all_inactive",
  "steps": [
    {"at_s": 0,   "module": "ttl_1a2b", "action": "set",   "params": {"pin": 17, "level": "high"}},
    {"at_s": 180, "module": "ttl_1a2b", "action": "set",   "params": {"pin": 17, "level": "low"}},
    {"at_s": 180, "module": "sound_9f00", "action": "play", "params": {"file": "tone_8k.wav"}},
    {"at_s": 300, "module": "ttl_1a2b", "action": "train",
     "params": {"pin": 22, "rate_hz": 10, "width_s": 0.02, "count": 50}},
    {"at_s": 600, "action": "repeat", "times": 10, "every_s": 60, "jitter_s": [0, 20],
     "steps": [{"at_s": 0, "module": "sound_9f00", "action": "play",
                "params": {"file": "startle_110db.wav"}}]}
  ]
}
```

- `at_s`: seconds from session start (inside a `repeat`, from that
  repetition's start). A span step has `until_s` or `duration_s`.
- `repeat`: a block of steps, `times`, `every_s`, optional `jitter_s` drawn
  from `seed`, so a protocol with random gaps is reproducible.
- `end_state`: what outputs do when the protocol ends, the session stops
  early or the controller is lost: `all_inactive` (default) or `hold`.
  Possibly per target (open question).
- A TTL pin used by a protocol must be in a new pin mode, `scheduled` (an
  output the protocol owns), so a pin can't be both a generator and
  scheduled. While a session runs, manual commands on a `scheduled` pin
  (test pulses, set) are refused (decided 2026-11-16): the protocol is the
  only thing driving it, so the data matches the plan.

**Expansion.** The controller (and the frontend, from the same rules)
expands the protocol into a flat list per module: repeats unrolled, jitter
drawn, trains split into edges. Validation runs on that list: overlapping
steps on one target (a `pulse` while `set` holds the pin high), widths ≥ the
period in a train, a sound longer than the gap to the next play on the same
module, unknown actions or out-of-range parameters (against the manifest), a
module not bound to the session, a protocol longer than a set session
duration. Errors block saving; a protocol that outlasts an open-ended
session is a warning. One implementation in `src/shared/session_protocol.py`
plus a JS twin, tested against the same fixture cases.

## Running timed steps (module side)

- At recording start the module receives its expanded list (with the
  session start time it already uses), writes it next to its data as
  `<session>_<module>_protocol.json`, and starts one worker thread.
- Absolute deadlines: each step fires at `session_start + t`, and the worker
  sleeps until that deadline, so lateness never accumulates. (The existing
  TTL generators sleep relative intervals and drift; fix them the same way,
  separately.)
- Each fired step is logged in the module's data with `source=protocol`, the
  step index and its lateness (TTL: `_write_ttl_event`; sound: a
  `<session>_sound_events.csv` like the TTL CSV), so planned and actual
  steps pair up in analysis. A step more than 20 ms late (to be measured)
  still fires and is counted.
- On stop, controller loss (the existing disconnect grace) or an error the
  worker applies `end_state` (TTL already has `_set_all_output_pins_inactive`).
- Heartbeat: current step, next step time, late-step count.
- Millisecond timing from a Python thread, stated in the editor: ample for
  minute-scale protocols; it warns on pulse widths or rates below a floor
  (start at 5 ms / 50 Hz, measure). Sub-ms work is out of scope.

## Liveness and recording spans

Today liveness, gap records and auto re-arm assume every bound module
records for the whole session. Once `record` spans can be shorter than the
session (phase 4), each module's expected recording state comes from its
spans: not recording outside a span is correct, not recording inside one is
a fault and a gap, a span that never started is a fault. Until then every
protocol keeps the whole-session default `record`, so nothing in the
liveness path changes in phases 1-3. Triggered recording waits for this
because it touches the v1.0 "no silent data loss" bar.

## The timeline (Gantt) view

One component, three uses:

1. **Preview while editing**: the expanded protocol as one row per target
   (TTL pin, sound module, camera record), grouped by module, with
   validation problems on the bars. Editing is a table of steps; the chart
   is the preview.
2. **Live** (session page): a "now" line, planned steps in a light tone,
   actual steps from module status drawn over them.
3. **After the session** (session detail / Post-Process): planned from
   `_protocol.json`, actual from the modules' data files, late or missing
   steps marked. Then rows from data that already exists: recording spans
   and recorded gaps, the DMX lighting CSV, APA armed/shock decisions,
   habitat clips, sync pulses. A compose tile can follow.

Time axis in mm:ss from session start with wall time on hover; colour by
row; a table view of the steps alongside (dataviz rules).

## Phases

1. **Manifest + timed steps for TTL and sound.** `@action`/`@event`, the
   `capabilities` message, the shared format/expansion/validation with JS
   twin and fixtures, the module runner on `ttl` (with the `scheduled` pin
   mode) and `sound` (`play(file, volume, duration_s)` as an action), per-module
   export, `end_state`. Two different modules from the start is what keeps
   the design general. Tests with mocked pins/audio and a fake clock; then
   an on-device lateness check (a scope, or a second TTL module recording
   the outputs).
2. **Editor + preview timeline**, then the after-session timeline, then
   live. In every variant that shows TTL/sound; `dev:mock` support.
3. **Event rules on the controller**, with APA automated shocking moved onto
   them as the first real case (already built and validated on the rig, so
   it shows whether the rule model is enough).
4. **Recording spans and triggered recording** (`record` windows,
   `record_clip`), with liveness reading the spans; then habitat recording
   plans and the motion trigger migrated onto the same model.

The DMX module keeps its own time-of-day schedule: its cycle runs
independently of sessions, a different reference clock from session start.

## Open questions

For the requesting user (shape phase 1's format):

- What does the real protocol look like: how many pins/sounds, how long, how
  many steps? Repeated trials, randomised gaps, pulse trains?
- What should outputs do if the session stops early or the controller is
  lost: all off, or per target (a light that should stay on)?
- Is "from session start" always the reference, or "from the first input
  edge" (that's an event rule: phase 3, or pyControl)?
- What timing precision do pulse widths need?

For us:

- Protocol on the session vs. on each module's config: per session (above)
  so one protocol spans modules; templates cover reuse. Confirm.

Decided 2026-11-16:

- Manual test pulses on a `scheduled` TTL pin are refused mid-session.
- Sound level per play: yes. Today `_play_sound` takes no arguments and reads
  `sound.volume` (an ffmpeg gain factor, 1 = 100%), `sound.duration` and the
  file picked by `_use_this_sound_file` from module state. Phase 1 makes
  `file`, `volume` and `duration_s` optional `play` parameters, falling back
  to those settings, and logs the values actually used in the sound CSV (it
  already has volume and duration columns). `_play_sound` blocks until
  `aplay` exits, so the runner times each play from its deadline, and
  validation flags a play longer than the gap to the next one.
- All actions are schedulable, by timed steps and event rules alike; no
  allow-list (safety checks stay inside the actions, see "Two execution
  tiers").

## Tests

- Shared fixtures: expansion (repeats, trains, seeded jitter reproducible),
  each validation error, Python and JS agree.
- Module runner: steps fire at deadlines with a fake clock; no drift over a
  long protocol; late steps logged; stop mid-protocol applies `end_state`;
  exported protocol and event CSV contents; the `capabilities` message
  matches the decorators.
- Controller: per-module split; a session with no protocol behaves exactly
  as today; validation against the manifest.
- Frontend: preview renders the expanded list; validation marks; lint/build
  for every variant.
