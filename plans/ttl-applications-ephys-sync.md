# TTL applications: say what a TTL module is for (ephys sync first)

- **Status:** phases 1-4 built 2026-11-20 (write-up in `docs/CHANGELOG.md`);
  rig validation and phase 5 open
- **Created:** 2026-11-20
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (one-liner)

## What's built

`ttl.application` + pin roles (defined once in
`src/shared/ttl_applications.json`), the Lines tab with wiring guide, Test
line and "Seen in Open Ephys" confirmations kept on the controller
(`src/controller/ttl_lines.py`), **Records ephys** sessions with
`application` / `when: "ephys"` rig items, the declared sync pin in
`ephys_data.sync_pin`, alignment results recorded on the module, and the
"Ephys not uploaded" session badge. Full detail in the changelog.

Decisions taken on the open questions:

1. The ephys flag is per session only (remembered by the form, not a rig-wide
   default).
2. The sync line's pseudorandom timing is editable, folded away under "Pulse
   timing" with the module defaults.
3. Neurologger: a placeholder guide (wire to the logger's TTL input, test,
   confirm) until we know which logger.
4. The conditional Ephys sync item ships in basic, APA, loom and acoustic
   startle; not habitat.
5. A confirmation lapses after 7 days (`confirm_max_age_days`) or on any
   change to the line's pin, mode or channel.

Confirmations are stored on the controller, not in module config, so a
module reset or re-clone doesn't fake or lose them and a config save can't
race them.

## Remaining

- **Rig validation** (desk rig, with Open Ephys if one is to hand):
  - set the desk TTL module to `ephys_sync` via `saviour-config` and via the
    card; check the readiness check, the Lines tab, Test line and the
    confirmation (and that a channel change marks it "Wiring changed");
  - create a Records-ephys session with and without the module: confirm
    dialog, `Rig check: Sync —` lines in `session_events.log`,
    `ephys_wiring` in `session_metadata.json`;
  - record, upload a real Open Ephys folder, align: `pin_role` in the CSV,
    `sync_pin` picks it, "Sync verified" appears on the card, the session's
    badge clears.
- **Loopback on Test line:** where an input is wired back to the output (desk
  rig GPIO2 → GPIO3), report "output seen on loopback" after Test line. Proves
  the line fires, not that it reaches the acquisition system.
- **Cross-check in post-processing:** warn when the declared sync pin and the
  old pseudorandom guess disagree; fall back to the session's `ephys_wiring`
  for CSVs that predate `pin_role`.
- **Phase 5:** `neurologger_sync` guidance once the labs' logger and its sync
  input are known (TTL in or IR LED); a `stimulus_control` layout (Protocol tab
  first, stimulus lines only).
- Show the application on the dashboard readiness rows and session detail
  (System, Settings and module cards have it).
