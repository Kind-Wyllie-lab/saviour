# RFID gate mapping: which antenna is which habitat gate

- **Status:** phases 1-2 built 2026-11-05 (`feat/rfid-gate-mapping`), tested
  in mock mode and against an `rfid` module's simulated bus; not yet run on
  the habitat bus. Phases 3-4 open.
- **Created:** 2026-11-04
- **Owner:** ascottg
- **CLAUDE.md ref:** Reliability / UX → "Habitat RFID: gate mapping routine"
- **Depends on:** `plans/habitat-rfid-db-sync.md` (`feat/rfid-db-sync`: the
  `rfid` module on the habitat bus, live `rfid_read` forwarding). Becomes
  part of the wider habitat layout plan (not yet written; see "Relation to
  the habitat layout plan" below).

## Built (phases 1-2)

- **Controller** (`src/controller/variants/habitat/`):
  - `gate_map.py`: plain logic, no Socket.IO. Covers the layout (generate,
    validate, scope), `GateMapStore` (the files below, atomic writes,
    append-only change log) and `MappingRun`, the routine's state machine.
    It also has the review checks: a unit shared by two gates, units on the
    bus but not mapped, mapped units missing from the bus, and a changed
    serial number.
  - `gate_map_service.py`: connects the logic to Socket.IO and the RFID
    modules, and keeps the run on the controller. Every browser sees the
    same run, a draft resumes after a restart, and the settle window runs
    on a timer.
  - `web.py` gained `module_status_listeners`, which lets a variant see
    `rfid_read` and `cmd_ack`, the messages `web.py` handles itself.
- **Module** (`rfid_module.py`): `rfid_mapping_mode(enabled, timeout_s)`
  (90 s per renewal, 10 min cap) and `rfid_units()`. Mapping mode forces the
  `rfid_read` message, never the CSV row.
- **Frontend** (habitat variant): a **Gates** page
  (`habitat/pages/Gates/`) with four tabs: Map (bus check, gate table,
  recent changes), Walk-through, Mapping run and Layout. The grid diagram,
  `habitat/components/HabitatGrid`, is the piece meant to grow into the
  layout-aware habitat view. `dev:mock` simulates a run
  (`src/mock/gateMapMock.js`) for UI work without a habitat controller:
  `VITE_VARIANT=habitat npm run dev:mock`. Its **Simulated bus** panel
  (2026-11-12) lets you play the probe by hand, antenna by antenna. The
  same panel shows on a real habitat controller whenever an `rfid` module
  runs with `rfid.mock.enabled`: it injects reads on the module's
  simulated bus (`rfid_mock.py`, `rfid_mock_read` / `rfid_mock_unit`), so
  the real module, service and `MappingRun` are exercised without readers
  (set `rfid.mock.units` to the habitat's unit count, e.g. 84).
  `test_gate_map_simulated_bus.py` runs the same thing in pytest.
- Tests: `src/controller/tests/test_gate_map.py`,
  `test_gate_map_simulated_bus.py`, plus mapping-mode tests in
  `test_rfid_module.py` and `test_rfid_mock.py`.
- Built differently from the text below: a live read already shows its
  gate in the walk-through, but not yet in the RFID MJPEG view or in
  normal running (phase 4).

## Why

A habitat is 16 boxes in a 4x4 grid. Each wall between two boxes has four
gates, each with an RFID antenna on its own reader unit (an address on the
RS485 bus). A read says "unit 34 saw tag 000807DC1F", but nothing records
which gate unit 34 is, so reads can't be turned into "animal X went from box
A1 to B1". Today that knowledge lives in people's heads, or nowhere.

Some gates may not be fitted, some may be closed, and antennas fail or get
swapped. So the mapping has to be built by testing on the rig, and rebuilt
or checked whenever hardware changes, with a record of what changed and
when.

The old Pi 4 logger had a crude version (`rfid-rats/map_habitat_terminal.py`:
put the probe in an antenna, its number is printed, press Enter), with no
gate names, no checks and no record.

## What it does

A guided routine in the habitat frontend:

1. **Start.** The operator enters their name or initials and picks the
   scope: the whole habitat, one wall, or listed gates. The controller asks
   the RFID module for a bus scan, so it knows which units are on the bus
   before starting.
2. **Register the probe tag.** "Hold the probe tag at any antenna." The first
   tag read becomes the probe for this run, and every other tag is ignored.
   That way animals still in the habitat can't map a gate by walking through
   it at the wrong moment.
3. **One gate at a time.** "Pass the probe through gate **A1B1_a**", with that
   gate highlighted on a diagram of the grid. The first unit to read the probe
   is captured. Reads keep being collected for a short settle window
   (~1.5 s) to catch a neighbouring antenna also picking it up. The operator
   then picks **Confirm**, **Retry**, **Skip: gate closed**, **Skip: no
   antenna fitted** or **Mark faulty**, and the routine moves to the next
   gate.
4. **Review.** A summary on the grid diagram before anything is saved (see
   "Problems it catches"). **Save** writes the new mapping and the change log;
   **Discard** leaves the old mapping in place.

Also, a **walk-through check** mode that changes nothing: pass the probe
through gates in any order, and each read lights up the gate the current
mapping says it is ("unit 34 → A1B1_a"). Five minutes after a hardware change
confirms the mapping, or shows where it's wrong.

### Problems it catches

| Problem | When | Shown as |
|---|---|---|
| A unit that is already mapped earlier in this run (or kept from the old mapping) reads at a new gate | step 3 | "Unit 34 is already A1B1_a." Options: retry, move it here (A1B1_a becomes unmapped), or keep both (only if the two gates genuinely share an antenna) |
| More than one unit reads the probe in the settle window | step 3 | "Units 34 and 35 both read it." Retry, or pick one |
| Nothing reads the probe | step 3, after ~30 s | "No antenna has read the probe." Retry, Mark faulty, or Skip |
| A unit that answered the bus scan was never mapped | review | Listed as "on the bus, not mapped to any gate" |
| A mapped unit didn't answer the scan | review | Listed as "mapped but not on the bus" |
| A unit's serial number changed since it was mapped (decoder swapped onto the same address) | review, and in normal running | "Unit 34 has new hardware since it was mapped; check it" |

### Normal running (after the routine)

- Live reads and the RFID view show the gate name, not just the unit number.
- Gates marked faulty, and mapped gates with no reads for a configurable
  period (days, since a quiet gate can be normal), show on the layout view.
  This links to the antenna self-test item in the RFID README backlog.

## Data

### Identity of an antenna

The key is `(module_id, unit_address)`. The unit's hardware serial number
(`rfid_bus` already asks each unit for its 4-byte serial after a logon) is
recorded at mapping time too, so a decoder swapped onto the same address gets
flagged, not silently trusted.

### Files on the controller (`/var/lib/saviour/controller/habitat/`)

- `layout.json`: boxes, walls, gates (see the next section). Rarely changes.
- `rfid_gate_map.json`: the current mapping, one entry per gate:
  `{gate, status: mapped|closed|not_fitted|faulty, module_id, unit,
  unit_serial, mapped_at, run_id}`, plus `version` and `updated_at`.
- `rfid_gate_map_changes.jsonl`: append-only change log, one line per change:
  `{ts, run_id, operator, gate, before, after, reason}`. `reason` is
  `mapping_run`, `walk_through`, `manual_edit` or `hardware_changed`.
  Never rewritten.

Saved runs are atomic (write to a temp file, then rename). An unfinished run
is kept as `rfid_gate_map.draft.json` so it can be resumed after a browser
reload or a controller restart.

### Where analysis gets it

- **Every session's export includes a snapshot** of `layout.json` and
  `rfid_gate_map.json` as they were at session start, next to the other
  session metadata, so a session's data is self-describing even after a
  later remap.
- **MariaDB:** a `rfid_gate_map` table with `valid_from` / `valid_to`, one row
  per (gate, unit) period, written by the controller on Save. Read rows stay
  keyed by unit (the hardware truth), and a gate is resolved by joining on
  the read's time. A remap never rewrites history, and old reads keep their
  correct gate. This needs controller → DB access (the modules write reads;
  the controller would write the mapping), or the RFID module syncs a
  mapping the controller pushes to it. To decide.

## Relation to the habitat layout plan

The wider plan (to be written) makes SAVIOUR aware of the physical habitat:
boxes, which camera / mic / RFID gate belongs where, and a frontend that
shows data laid out as the habitat is.
`plans/habitat-post-processing.md` already proposes a "box map" for
cameras and mics. This plan should define only the part of that layout it
needs, in a shape the wider plan extends rather than replaces:

```json
{
  "version": 1,
  "boxes": [{"id": "A1", "row": 0, "col": 0}, ...],
  "walls": [{"id": "A1B1", "boxes": ["A1", "B1"]}, ...],
  "gates": [{"id": "A1B1_a", "wall": "A1B1", "index": 0}, ...]
}
```

Cameras, mics and box metadata get added to `boxes[]` later. The grid
diagram component built for the mapping routine becomes the base of the
layout-aware frontend.

## Phasing

1. **Layout + mapping file + walk-through check.** Layout editor kept
   minimal (generate the 4x4 grid; tick which walls have gates); read-only
   live "which gate is this" view. Useful on its own, small.
2. **Mapping routine** with the problem checks, change log, draft resume.
3. **Session snapshot + MariaDB mapping table.**
4. **Normal-running gate health** (silent gates, hardware changed).

### Module changes needed

- A mapping mode on the RFID module (`rfid_mapping_mode(on, probe_tag)`):
  while on, every read is sent to the controller as `rfid_read`, even with
  presence tracking set to `record: visits`, which suppresses raw reads
  today. It turns itself off after a timeout so a closed browser tab can't
  leave it on.
- Report unit serial numbers to the controller (heartbeat or a command), so
  the scan-vs-map review and the hardware-changed check can run.

## Open questions

1. ~~**Naming.**~~ **Superseded 2026-11-06:** letters are rows, numbers
   are columns (checked on the rig), matching the researchers' Habitat Hub;
   geometry rework in `plans/habitat-layout-model.md` phase 1. Was, decided 2026-11-05: letter = column (A-D left to right),
   number = row (1-4 top to bottom), seen from above. A wall is named by its
   two boxes, left or top box first (A1B1, A1A2). The gate order a-d (top to
   bottom along a vertical wall, left to right along a horizontal one) is
   the default as built; still to confirm on the rig.
2. **Which walls have gates.** Up to 4 gates per wall, internal walls only
   (decided 2026-11-05). Which walls and gates are actually fitted is set in
   the Layout tab, which would account for 72 units rather than 96.
3. ~~**One antenna per gate, or two.**~~ One (decided 2026-11-05).
4. **One RFID bus / module for the whole habitat?** The design allows several.
5. **Is there a dedicated probe tag**, and will animals be in the habitat
   during mapping?
6. **The MariaDB mapping table**: controller writes it directly, or pushed
   via the RFID module (see "Where analysis gets it")?
