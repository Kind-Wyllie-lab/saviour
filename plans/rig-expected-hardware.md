# Rig profiles: expected hardware per rig type, missing hardware flagged live

- **Status:** in progress (phase 1 built 2026-11-20, not yet on the rig)
- **Created:** 2026-11-20
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (one-liner)

## Where it stands

Phase 1 is built: `rig.expected` per variant, `src/controller/rig_check.py`
(`evaluate()` + `RigMonitor`), the `rig_check` Socket.IO event, `rig` on
`/api/v1/state` and `/readiness`, the session record and confirm, the
banner, the System block and the controller config **Rig** tab. Full write-up
in `docs/CHANGELOG.md` (2026-11-20). What follows is what's left.

## Validate phase 1 on the desk rig

- Unplug a module's PoE → banner within the heartbeat timeout (90 s), alert
  after `rig.alert_after_s` if required.
- Pull an AudioMoth → that mic module listed as faulty with its reason, not
  counted.
- Restart the controller with a module off → `pending` for
  `startup_grace_s`, then counted short.
- Fleet deploy (`tools/deploy_staging.sh`) → no banner while modules restart.
- View the banner, System block, Rig tab and the session-start confirm in a
  browser on each variant build (none of it has been viewed yet).

## Phase 2: remember the roster (named absences)

Counts are enough to say *something* is missing. To say *which* one
("apa_arduino_3f2a, last seen 2026-11-19 14:02") after a controller restart,
persist a small roster on the controller
(`/var/lib/saviour/known_modules.json`: id, type, name, last_seen), updated
from heartbeats and pruned when a module is removed in the UI
(`Modules.remove_module`). `evaluate()` then lists never-reappeared modules
under `offline` with their last-seen time.

## Phase 3: habitat by layout

For the habitat the real expectation is spatial, not a count: one camera per
box, one mic module per column, an AudioMoth per box. Once
`plans/habitat-layout-model.md` lands, derive the habitat's `rig.expected`
from the layout (box count → camera min, column count → mic min) and add
"box A3: no camera placed" to `placement.placement_state` problems, feeding
the same banner. Gate-unit absences stay in `plans/rfid-gate-mapping.md`.
When RFID and DMX are installed on the habitat, make them `required` in
`habitat_controller_config.json` (they ship `optional`).

## Open questions

1. Habitat and acoustic startle defaults were set without the rig owners:
   confirm the real hardware sets (is TTL required for acoustic startle? a mic
   for APA?).
2. Should a `required` shortfall ever **block** a session start (opt-in per
   rig), or always only warn (current behaviour)?
3. Existing rigs: a controller whose `active_config.json` predates this
   picks up its variant's `rig` defaults on the next start (missing keys are
   filled). A rig that needs a different set edits it on the Rig tab.
