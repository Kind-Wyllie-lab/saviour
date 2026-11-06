# Habitat layout model, animals and the Habitat Hub

- **Status:** proposed
- **Created:** 2026-11-06
- **Owner:** ascottg
- **CLAUDE.md ref:** Reliability / UX → "Habitat RFID gate mapping", "Habitat RFID"
- **Extends:** `plans/rfid-gate-mapping.md` (this is the "wider habitat layout
  plan" it refers to). Related: `plans/habitat-rfid-db-sync.md` (MariaDB),
  `plans/habitat-post-processing.md` (box map).

## Context

The habitat researchers built **Habitat Hub** (`habitat_hub.html` plus a
quick-start guide): one HTML page that reads and writes JSON files in a
SharePoint-synced `Habitat/` folder. It has three tabs:

| Tab | Holds | File |
|---|---|---|
| Layout design | antennas per panel hole, tunnels, balance beams, merged boxes, one-way doors, box contents, all as dated layouts | `experiments/<exp>/<exp>_RFID_address_info.json` |
| Lab book | rats (Tick@Lab import, tail marks, RFIDs, DoB, sire/dam), weekly weights, events (cleaning, enrichment, RFID error, welfare, cull …) | `experiments/<exp>/<exp>_labbook.json` |
| Planner | weekly calendar of cohorts, builds, leave across all habitats | `habitat_diary.json` |

Their analysis pipeline (`apply_layout_export.py`, `apply_labbook.py`)
reads those files. SAVIOUR meanwhile built its own layout and gate map
(`variants/habitat/gate_map.py`, the Gates page), which disagrees with the
Hub on naming and geometry (below).

## Decisions (2026-11-06)

1. **RFID gate mapping happens in SAVIOUR.** The Hub's "type the antenna
   number into a hole" / "Fill IDs from sheet" is replaced by SAVIOUR's
   mapping run and walk-through check.
2. **RFID tags and animal IDs are captured in SAVIOUR**, so live reads and
   recorded data resolve to animals.
3. **SAVIOUR's layout gains the Hub's topology** as options: tunnels,
   balance beams, open (removed) panels, one-way doors, per-antenna zone
   overrides, and layouts that change over time.
4. **Researchers may reach SAVIOUR directly one day, or through a bridge
   server.** Until then, SAVIOUR imports and exports the Hub's file formats
   so their pipeline keeps working unchanged. The Lab book (weights,
   welfare) and Planner stay in the Hub for now.

## Where the two models disagree today

| | Habitat Hub | SAVIOUR (`gate_map.py`, decided 2026-11-05) |
|---|---|---|
| Box name | letter = **row** (A top), number = column | letter = **column**, number = row |
| Viewpoint | **from the front**: rows are levels, horizontal panels are floors; "view from the back" matches the video | "seen from above" |
| Panel name | `AV1` (between A1 and A2), `AH1` (floor between A1 and B1) | `A1B1`, `A1A2` |
| Holes per panel | 4 in a **2×2** grid. Vertical: a top-back, b top-front, c bottom-front, d bottom-back (seen from the lower-numbered box). Floor: a left-back, b left-front, c right-front, d right-back (seen from the box above) | 4 **in a line**, top to bottom / left to right ("still to confirm on the rig") |
| Gate state | `open` flag, one-way door, hardware duplicate | mapped / closed / not_fitted / faulty, serial check |
| Topology | tunnels, beams, merged boxes, zone add/drop | wall between two boxes only |
| Time | dated layouts; export = per-gate `Periods` | one current map + change log |

Only the diagonal boxes share a name: Hub **C2** is SAVIOUR **B3**. No map
has been saved on the habitat hardware yet, so changing SAVIOUR's naming
now costs a migration of test data only.

**Naming settled 2026-11-06 (checked on the rig): letters are rows,
numbers are columns**, as in the Hub. This supersedes the 2026-11-05
decision in `rfid-gate-mapping.md`.

## Design

All files stay under `/var/lib/saviour/controller/habitat/`, written
atomically, with an append-only change log as `gate_map.py` does today.

### 1. Geometry (fix first)

- Box names: letter = row (A-D top to bottom), number = column (1-4),
  as in the Hub (decided 2026-11-06). The rest of the proposal follows the
  Hub too (decided 2026-11-06): rows are levels, seen from the
  front; vertical panel `XVn` between `Xn` and `X(n+1)`, floor panel `XHn`
  between `Xn` and the box below; holes `a-d` in the 2×2 lettering above.
  Gate id = `<panel>-<hole>` (`AV1-a`), matching the Hub's "Panel Location"
  and "Position" columns.
- `layout.json` v2 replaces `walls`/`gates` with `panels`/`holes`;
  `fitted` per hole stays. A loader migrates v1 (test data only).
- `HabitatGrid/geometry.js` draws the front view with holes at 2×2
  positions, plus a back-view toggle so it can match the cameras. The
  dashboard live map, Placement and the Gates page all use it.
- Camera names and AudioMoth labels follow the new box names; Placement's
  "mic module = column" check becomes "mic module = row" (each microphone
  module serves a row).

### 2. Animals and tags

New `animals.json` plus `animals_changes.jsonl`:

```json
{"version": 1,
 "animals": [{"id": "CRL_LT3M-07", "experiment": "CRL_LT3M",
              "tail_mark": "7", "sex": "M", "dob": "2026-05-25",
              "long_id": 1234567, "short_id": 42, "ear_notch": "3",
              "strain": "...", "sire": "...", "dam": "...",
              "housing": "habitat|standard", "cage": "Standard cage 1",
              "end_date": null, "note": ""}],
 "tags": [{"tag": "000807DC1F", "animal": "CRL_LT3M-07",
           "from": "2026-06-01T10:12:00", "to": null}]}
```

- Tags carry validity periods, because a tag can be replaced or reused.
  Resolving a read = the tag's assignment at the read's time.
- **Register a tag by reading it:** on the Animals page, pick an animal
  and hold its tag to any antenna. This reuses the mapping run's
  probe-capture path (`rfid_mapping_mode`). Typing the tag in also works.
- **Import** from the Hub lab book's `rats[]` (tail marks, RFIDs) and from
  a Tick@Lab Breeding Stock export (port the Hub's `parseStock`: group by
  cage / habitat tag, pick the group).
- **Live use:** the walk-through, dashboard live map and RFID view show
  the animal, not just the tag. A tag with no animal shows as "unknown
  tag", which also catches stray or probe tags.
- **Export:** a snapshot goes with every session; MariaDB gets `animal` and
  `animal_tag` tables (the ones `habitat-rfid-db-sync.md` deferred), written
  by the controller like the gate-map table.
- Scope is identity only. Admin and regulatory fields (PPL, 19b protocol,
  project code) and genotype stay in the Hub for now; genotype in
  particular is hidden by the Hub's blind mode, and SAVIOUR has no
  equivalent.

### 3. Topology options

Per layout period, all optional, so a plain grid stays as simple as it is
today:

- `open_panels`: panels with the wall removed; their boxes become one space.
- `tunnels`: `{name, box, holes: [h1, h2]}` joining two holes through a
  box; adds zone `A2T` (`A2T2` …).
- `beams`: `{name, holes: [h1, h2]}`; adds zone `BB1` …
- `one_way`: per hole, `{from, to}` box.
- `zone_extra` / `zone_drop`: per-hole manual zone edits.
- `duplicate`: a unit legitimately read at two holes (the mapping run's
  "keep both").

Zone and adjacency logic is a Python port of the Hub's `mergedGroups`,
`zonesOf` and `adjacency`, with tests written from Hub layouts (expected
zones taken from a real `_RFID_address_info.json`). Checks are ported too:
an open panel that still has an antenna, an antenna with fewer than two
zones, the wheel in two boxes, and so on.

### 4. Layouts over time

- `layout.json` holds a list of periods `{name, start, end, …topology}`;
  the last one is open-ended (`end: null`). "Split here" starts a copy from
  a given time.
- The gate map (unit → hole) keeps its change log; a gate's history is
  rebuilt from layout periods × map changes.
- **Export** in the Hub's schema: `{gates: {<unit>: {Periods: [{Start, End,
  Open, adjacent_zones, adjacent_gates, one_way}]}}}`. Written into every
  session export, and on demand from the Gates page. The Hub's `_editor`
  block can be included so the Hub can still open the file.
- **Import** a Hub `_RFID_address_info.json` (its `_editor.layouts`) to
  seed SAVIOUR from existing experiments.
- This absorbs the session-snapshot part of `rfid-gate-mapping.md` phase 3.
  The MariaDB `rfid_gate_map` table gets the same periods.

### 5. Box contents (later)

Per period, per box: wheel, enrichment, ladder, furniture, water, food,
other, with the Hub's change rows (`wheel_move`, `<type>_removed`,
`panel_removed`, `beam_added`, `one_way_door` …) exported as
`event_log.csv` rows. The Hub's furniture icons (SVG + PNG) can be reused
in the frontend.

### 6. Experiment link

A controller setting, "experiment on this habitat" (e.g. `CRL_LT3M`),
recorded in session metadata, used for the export file names and to scope
the animal list. Later it could come from the Hub's diary (`habitat_diary.json`
→ the block covering today on this habitat).

## Frontend

- **Gates → Layout** grows into the layout editor: modes for place hole,
  open panel, tunnel, beam, one-way; a period list with Split here; the
  checks list; zones per hole. The Hub's interaction model is the
  reference. Antenna IDs come from the gate map, never typed.
- **Animals** page (habitat variant): animal table, tag assignment by read,
  imports, change log.
- Live map, walk-through and RFID views show animal and zone names.
- `dev:mock` support for both, as for the gate map.

## Phasing

1. **Geometry and naming** (all decided 2026-11-06). Must land before the
   first mapping run on the habitat bus.
2. **Animals and tags**: registry, read-to-register, imports, live
   display, session snapshot.
3. **Topology options** + zone/adjacency derivation + checks + editor.
4. **Layout periods** + Hub-format export/import + session snapshot +
   MariaDB periods.
5. **Box contents** + event rows.
6. **Later, once researchers can reach SAVIOUR or a bridge exists:** the
   Lab book (weights against the red line, welfare, cleaning/enrichment
   events, with RFID-error and disruption events generated from gate
   health and recording gaps), and the Planner. Needs a decision on where
   welfare/PPL data lives, and per-user identity in the web UI (today one
   shared admin password; see `plans/remote-access-auth-hardening.md`).

## Open questions

Decided 2026-11-06:

- **Box naming:** letter = row, number = column (checked on the rig).
- **Viewpoint and names:** the Hub's. Rows are levels seen from the front;
  panels `AV1` / `AH1`; gate id `<panel>-<hole>` (`AV1-a`).
- **Hole lettering:** the Hub's a-d positions match the physical panels.
- **AudioMoth grouping:** each microphone module serves a row, so a mic
  module is named after its row letter.
- **Ownership:** SAVIOUR owns the layout (antennas and topology), being
  closest to the hardware; the Hub reads SAVIOUR's export.

Still open:

1. **Canonical animal ID.** Experiment + tail mark (what the Hub and
   pipeline key on), or the Tick@Lab long ID? Needed before phase 2.
2. **One tag per animal at a time?** Assumed yes, with history.
