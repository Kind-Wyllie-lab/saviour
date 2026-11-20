# Rig profiles: expected hardware per rig type, missing hardware flagged live

- **Status:** proposed
- **Created:** 2026-11-20
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (one-liner)

## Why

Each rig type comes with a known hardware set: an APA rig is useless without
its `apa_arduino` (shocker + motor), acoustic startle needs its `sound`
module, the habitat expects a camera per box, a microphone module per column,
the RFID bus and the DMX lighting. Today nothing in the frontend says "this
rig is missing X". An operator only finds out by noticing a card isn't there,
or when a session records without it.

What exists covers only parts of this:

| Piece | What it covers | Gap |
|-------|----------------|-----|
| `recording.expected_module_counts` (`base_config.json`, read at `recording.py:2489`) | Per-type count, checked before a **scheduled** session starts | Only the session's single target type; only sends a webhook alert; empty by default (habitat sets `0`s); not in the UI (left out on purpose when the config tabs were reworked) |
| `hardware_fault` on `ModuleHealthSnapshot` → **NO HARDWARE** badge in `System.jsx` | A module that is **online** but lacks its own peripheral (no CSI sensor, no AudioMoth, no reader, no shocker, no HiFiBerry) | Says nothing about a module that **isn't there** |
| Heartbeat timeout → `OFFLINE` | A module seen since the controller started that then went away | The registry (`modules.py` `_modules`) is in memory only: after a controller restart a missing module is simply never listed |
| Habitat Placement (`placement.py` `placement_state` problems) | A placed camera that is offline; names that don't match the map | No "box A3 has no camera at all" |

## Goal

One place that answers **"does this rig have what it should?"**, live, in
every variant's frontend, and the same answer used by session pre-flight
checks and the REST API.

## Design

### 1. Rig profile in controller config

Replace `recording.expected_module_counts` with a `rig.expected` list in
controller config. Each variant ships defaults in
`variants/<variant>/<variant>_controller_config.json`, editable per rig
(layering as usual: base → variant → active).

```json
"rig": {
  "expected": [
    {"label": "APA camera",  "types": ["apa_camera"],  "min": 1, "severity": "required"},
    {"label": "Shock/motor", "types": ["apa_arduino"], "min": 1, "severity": "required"},
    {"label": "Microphone",  "types": ["microphone"],  "min": 1, "severity": "optional"}
  ],
  "startup_grace_s": 120
}
```

- `types` is a list so one entry can accept any camera type
  (`camera`, `hailo_camera`, `basler_camera`, …), matching how the
  acoustic_startle dashboard already treats `type.includes("camera")`.
- `severity`: `required` (a missing one shows red and asks for confirmation
  when starting a session) or `optional` (amber, informational).
- `min` counts modules that are **online and have no `hardware_fault`**.
  An online module with a fault counts as present-but-faulty, and the check
  says which. That reuses the existing per-module fault work rather than
  duplicating it.
- Migration: on load, a non-zero `expected_module_counts` entry with no
  `rig.expected` becomes `{"types": [t], "min": n, "severity": "required"}`.
  The old key is then dropped from `base_config.json` and the habitat
  config.

Proposed variant defaults (**counts to confirm with the rig owners** before
building; see Open questions):

| Variant | Required | Optional |
|---------|----------|----------|
| `basic` | nothing (empty list: no banner) | |
| `apa` | 1 × `apa_camera`, 1 × `apa_arduino` | `microphone` |
| `acoustic_startle` | ≥1 `sound`, ≥1 camera (any type) | `ttl`, `microphone` |
| `loom` | ≥1 `loom_camera` | `microphone`, `ttl` |
| `habitat` | 16 × camera, 4 × `microphone`, 1 × `rfid`, 1 × `dmx_lighting` | |

### 2. One pure check function on the controller

`src/controller/rig_check.py`:

```python
def evaluate(profile: list[dict], modules: dict) -> dict:
    """-> {"ok": bool, "items": [{label, severity, min, present, faulty:
    [{id, reason}], offline: [id], status: ok|short|faulty}], "summary": str}"""
```

- Pure (no facade, no I/O), unit-tested against fixture module dicts.
- Called whenever the module list changes (`broadcast_updated_modules`) and
  on health updates that change `hardware_fault`. The result is emitted only
  when it changes.
- **Startup grace:** for `startup_grace_s` after the controller starts (and
  after a fleet deploy restart) every item reads `pending` rather than
  `short`, because modules take a minute or two to register. That avoids a
  red banner on every restart.
- Offline-but-known modules are named (`offline: [...]`). Unknown-absent ones
  can only be counted ("1 of 2 present"); naming them needs phase 2.

### 3. Where the result goes

- **Socket.IO:** `rig_check` event (pushed on change, and in the initial state
  bundle a client gets on connect).
- **REST:** add a `rig` block to `GET /api/v1/state` (so pyControl etc. can
  refuse to start on a short rig) and document it in `docs/REST_API.md`.
- **Session start (UI + REST):** if any `required` item is short, the New
  Session form shows the shortfall in `ReadinessSummary` and asks for
  confirmation. **Warn, don't block:** a researcher may knowingly run
  without a mic. The REST start route returns the shortfall in its response
  but still starts (or add an opt-in `require_rig_ok` flag).
- **Scheduled sessions:** `recording.py`'s "Expected module count" block
  switches to `rig_check.evaluate`, and the alert lists every short item,
  not just the target type.
- **Session metadata:** write the evaluation into the session's
  `_recording.json` at start, so a missing module is on record next to the
  data.
- **Alerts:** a `required` item short for more than N minutes (outside a
  session too) sends one webhook alert per item per day behind a new
  `notify_rig_incomplete` toggle on the Alerts tab.

### 4. Frontend

- **`RigCheckBanner`** in `basic/components/`, mounted in the shared
  `Header` so every variant gets it (including acoustic_startle, which lacks
  `FaultAlertModal`). Hidden when `ok`. Otherwise a one-line strip: red for
  a required shortfall, amber for optional only. "Missing: shock/motor
  controller (0 of 1)". Click opens a popover with every item, present
  modules, faulty ones with their `hardware_fault` reason, offline ones by
  name.
- **System page:** a "Rig" summary row above the module list with the same
  data.
- **Settings → controller config:** a **Rig** tab (admin-only) to edit
  `rig.expected`: label, type multi-select from known module types, min,
  severity, plus "reset to variant defaults".
- **`dev:mock`:** the mock server emits `rig_check`; add a mock toggle that
  drops a module so the banner can be built and checked without hardware.
- After the change: `npm run build` (all 5 variants in CI).

### 5. Phase 2: remember the roster (named absences)

Counts are enough to say *something* is missing. To say *which* one ("apa_arduino_3f2a,
last seen 2026-11-19 14:02"), persist a small roster on the controller
(`/var/lib/saviour/known_modules.json`: id, type, name, last_seen), updated
from heartbeats and pruned when a module is removed in the UI (`remove_module`
already exists). `evaluate` then lists never-reappeared modules by name even
after a controller restart.

### 6. Phase 3: habitat by layout

For the habitat the real expectation is spatial, not a count: one camera per
box, one mic module per column, AudioMoth per box. Once
`plans/habitat-layout-model.md` lands, derive the habitat's `rig.expected`
from the layout (box count → camera min, column count → mic min) and add
"box A3: no camera placed" to `placement_state` problems, feeding the same
banner. Gate-unit absences stay in `plans/rfid-gate-mapping.md`.

## Tests / acceptance

- `test_rig_check.py`: empty profile → ok; count short; faulty module not
  counted and listed with reason; offline listed; multi-type entry; grace
  period → pending; migration from `expected_module_counts`.
- `test_recording.py`: the scheduled pre-flight alert uses the new check and
  lists every short item.
- REST: `rig` block in `/api/v1/state`.
- Desk rig: unplug a module's PoE → banner within the heartbeat timeout
  (90 s); pull an AudioMoth → mic shows as faulty, not present; restart the
  controller with a module off → banner after the grace period, counted (and
  named once phase 2 lands).

## Open questions

1. Habitat and acoustic_startle counts: confirm the real hardware sets (is TTL
   required for acoustic startle? is a mic required for APA?).
2. Should a `required` shortfall ever **block** a session start (opt-in
   per rig), or always only warn?
3. Should the per-rig "expected" list also pin specific module IDs/names
   (e.g. habitat box names) rather than counts, or leave that entirely to
   the habitat layout (phase 3)?
