# Habitat simulator

Owner: Andrew SG. Status: step 1 built 2026-11-12 (habitat `dev:mock`); step 2 not started.

## Why

The habitat UI (dashboard live map, Gates, Placement, Monitor, lighting) has
only ever run against 2-3 real modules or a mock where every feed is random
and unrelated: gate reads at random gates, AudioMoth levels from microphone
modules that aren't in the module list, one static "MOCK CAMERA" tile. A
habitat has 16 cameras, 4 microphone modules × 4 AudioMoths, a 72-unit RFID
bus and the DMX lights, and the point of the dashboard is that they agree:
a rat leaving B2 for B3 shows on B2's camera, then a read on the B2-B3 wall,
then B3's camera.

One model of tagged rats moving through the boxes drives every feed.

## Step 1: live mock (`npm run dev:mock`, habitat variant)

Frontend only, nothing ships (only loaded under `VITE_MOCK=1`).

**Model** (`src/mock/habitatSim/model.js`, pure JS, no browser or Node APIs):
- Layout: the gate map's layout (`generateLayout`, moved here from
  `gateMapMock.js`), cols × rows boxes, 4 gates per wall, some walls with no
  gates. A rat can cross a wall with at least one fitted gate.
- N rats (default 10), each with a tag (10 hex chars) and a nest box.
- Event-driven: each rat stays in a box for a drawn dwell time, then crosses
  one gate of a passable wall (chosen at random) into the neighbour. Dwell
  mean depends on the light phase (short at night, long by day); the choice
  of box is weighted toward boxes with other rats (social) and, by day,
  toward the rat's nest.
- Deterministic from (seed, day): the browser mock and the dev server
  compute the same rats independently, so the camera frames (dev server)
  and gate reads / audio (browser) agree with no channel between them.
  Each "sim day" starts at `day_start` (mid light phase, default 13:00
  local) with every rat in its nest, resting; a rat awake at the boundary
  can jump once a day.
- Within a box: a rat's position is a smooth function of time (still and
  curled up in a corner when resting), walking in from the wall it entered
  through and out to the gate it leaves by.
- USV calls: deterministic per (box, 250 ms slot), more likely with two or
  more rats in the box (50 kHz), occasionally 22 kHz alarm calls.

**Parameters** in `src/shared/habitat_sim.json` (`@saviour-shared`), so a
later Python generator (step 2) reads the same numbers. The light schedule
there is also the lighting mock's default schedule.

**Feeds:**
- Fleet (`habitatSim/fleet.js`): 16 `habitat_camera` modules named after
  their box, 4 `microphone` modules named after their column, each with 4
  AudioMoths labelled with their box (`audiomoth_labels`), 1 `rfid` module,
  the DMX module. Health for each. Replaces the generic mock modules in the
  habitat build.
- Gate reads: each crossing is 1-3 reads of the rat's tag on that gate's
  unit (`gate_map_hit`). Every fitted gate has a "true" unit on the bus; the
  gate map starts with most gates mapped to it, a few left unmapped (their
  reads show as "from unmapped units", and the Gates page has something to
  map), one closed, one faulty.
- AudioMoth levels (`audio_levels`, per box): room noise + movement per
  rat + call bursts.
- Camera streams: the dev server (`mockMediaPlugin`) serves each box's
  stream as `multipart/x-mixed-replace` PNG frames rendered from the model
  (top-down box, gate openings, rats, time and label), daylight colour or
  IR grey from the light schedule. Labels that aren't boxes keep the old
  SVG placeholder.
- Microphone streams (Monitor page): a scrolling spectrogram per AudioMoth,
  4 stacked per microphone module, with the calls drawn in.

Console: `__mockSocket.sim` (rats, where they are, crossings so far).

Not in step 1: motion-trigger clips, recordings of simulated data.

**As built (2026-11-12):**
- The schedule in `habitat_sim.json` is reversed (dark 07:00-19:00) so the
  rats are busy during working hours; the lighting mock reads it.
- A Gates mapping run's probe is read by the gate's own unit on the
  simulated bus (the rest of the run is the existing scripted demo).
- Each mock stream comes from its own `<name>.localhost`: the browser allows
  6 connections per host and streams never finish.
- Chrome starts only 10 of the page's streams on first load (low-priority
  requests are capped while a page loads); the rest start when the live map
  reconnects. Probably true of the real 16-camera dashboard too; check on
  the habitat.

## Step 2: simulated sessions with ground truth (later)

A Python generator (`tools/` or `src/sim/`) reading the same
`habitat_sim.json`: writes a session folder (RFID read CSVs, lighting CSV,
motion clips, `config.json`) plus a ground-truth file of every rat's
position, to test gate mapping, occupancy and habitat post-processing.
Only worth building once those analyses exist. Generated media is never
committed; a few small fixtures are.

## Notes

- Box naming: the mock follows the gate map's current naming (letter =
  column). When `gate_map.py` moves to letter = row (see
  `plans/habitat-layout-model.md`), `generateLayout` here moves with it.
- If the animal model becomes useful outside SAVIOUR (Habitat Hub, other
  homecage rigs), split the model out as a small package then, not before.
