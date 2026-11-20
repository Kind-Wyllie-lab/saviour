# TTL applications: say what a TTL module is for (ephys sync first)

- **Status:** proposed
- **Created:** 2026-11-20
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (one-liner)

## Why

Functionally a TTL module is a set of GPIO pins with modes. To most users it
is something more specific: their **ephys sync module**, their neurologger
sync, or the box that triggers stimuli. Today SAVIOUR knows *how* each pin
fires (`ttl.pins.<gpio>.mode`: `pseudorandom`, `experiment_clock`,
`interval_pulse`, `experiment_start`, `scheduled`, `input`) but not *why*, so:

- the TTL card shows every mode and parameter to everyone, with no guidance;
- the Open Ephys wiring advice ("Digital In only", the HDMI→BNC IO board, a
  pseudorandom train, "researchers have lost hours of data because a cable was
  plugged into the wrong port") lives only in
  `docs/readthedocs/open_ephys.md`;
- nothing confirms the sync line actually reaches the acquisition system
  before a session;
- post-processing has to **guess** the sync pin (`ephys_data.sync_pin`: the
  one `pseudorandom` pin, else the only pin, else give up);
- the rig check (`plans/rig-expected-hardware.md`) can't say "this session
  records ephys, so an ephys sync module is required".

Modules are almost always dedicated: they live on one rig and do one job.

## Design

### 1. `ttl.application` in TTL module config

A module-level flag in `src/modules/variants/ttl/ttl_config.json`:

```json
"ttl": {
  "application": "mixed",
  "_application_options": ["ephys_sync", "neurologger_sync", "stimulus_control", "mixed"],
  ...
}
```

- `mixed` (default, and what every existing module gets) is exactly today's
  TTL card: every mode, every parameter. Nothing changes for anyone who
  doesn't opt in.
- Set at device configuration (`saviour-config`: after choosing type `ttl`,
  ask "What is this TTL module for?") and editable afterwards on the module's
  card (Basic tab). Changing it never touches the module type or ID.
- Type stays `ttl` and the ID stays `ttl_xxxx`, so the ~22 places that check
  for exactly `"ttl"` (`ephys_data.py`, `protocol_library.py`, `rest_api.py`,
  `data_rate.py`, the dashboard, config card, protocol picker, session detail,
  `tools/ephys/`, `tools/analyse_audio_sync.py`), CSV naming and every
  post-processing path are untouched. To have it read "Ephys sync" where IDs
  show, set the module's *name*.

### 2. Pin roles within an application

Each pin gains an optional `role` on top of its `mode`. A role is a preset:
a mode, fixed or default parameters, a label and any wiring fields. The
application decides which roles are offered.

| Role | Mode | Extra fields | Offered by |
|------|------|--------------|------------|
| `sync` | `pseudorandom` (timing per the open question below) | `ephys_channel` (Digital In 1-8) or the logger's input | `ephys_sync`, `neurologger_sync` |
| `experiment_start` | `experiment_start` | `ephys_channel` | all |
| `stimulus` | `interval_pulse` / `scheduled` (protocol) | label | `stimulus_control`, `mixed` |
| `marker_in` | `input` | label | all |
| `custom` | any | as today | `mixed` |

Rules: at most one `sync` pin per module; an `ephys_sync` module must have
one (the card says so until it does). The module validates roles in
`configure_module_special` the same way it validates modes now. The per-pin
CSV already records `pin_mode`; add a `pin_role` column so the role travels
with the data.

### 3. Application-specific card and guidance (frontend)

`TTLConfigCard` branches on `ttl.application`:

- **`ephys_sync`: an "Ephys sync" card.**
  - Lines: the sync line, then optional experiment-start / marker lines, each
    "GPIO n → IO board BNC → Digital In channel N".
  - Wiring checklist with the doc's pictures (Digital In, IO board, one line
    per channel), linked to the full guide.
  - **Test line:** pulses that line for ~10 s (the existing one-shot pulse
    path, `POST /api/v1/modules/<id>/pulse` / its socket twin) so the user can
    watch for the coloured column in the Open Ephys GUI, then tick **"Seen in
    Open Ephys"**. Stored per line with the date and the wiring at the time
    (`ttl.verified`: `{gpio: {channel, at}}`); any wiring change clears it.
  - Pre-session reminder (the New Session confirm, below) when a sync line was
    never confirmed or was confirmed more than N days ago (default 7).
  - If the module has a loopback input wired (as on the desk rig, GPIO2 →
    GPIO3), Test line also reports "output seen on loopback": proof the line
    fires, though not that it reaches the ephys system.
- **`neurologger_sync`:** the same layout with the logger's own wiring and
  confirmation steps (placeholder until we know which logger; see open
  questions).
- **`stimulus_control`:** the Protocol tab first, stimulus lines, no ephys
  guidance.
- **`mixed`:** today's card unchanged.

Everywhere a module is listed (module list, dashboard, readiness, session
detail), a TTL module with an application shows it ("Ephys sync") beside its
name.

### 4. Ephys sessions and the rig check

- **Session flag:** the New Session form (standard, scheduled, habitat) gets
  **Records ephys** (and REST `POST /api/v1/sessions` `ephys: true`). It is
  stored on `RecordingSession` and in `session_metadata.json`, along with a
  snapshot of the ephys-sync wiring (module, sync GPIO, Digital In channel,
  last confirmation).
- **Rig profile items gain two optional filters** in `rig_check.py`:
  - `application`: matches a module whose config has `ttl.application`
    equal to it (the controller already holds each module's config in
    `facade.get_modules()`).
  - `when: "ephys"`: the item only counts for ephys sessions. The live banner
    ignores it (no session in view); the session-start confirm and the session
    record evaluate it with the session's flag.

  e.g. `{"label": "Ephys sync", "types": ["ttl"], "application": "ephys_sync",
  "min": 1, "severity": "required", "when": "ephys"}`. Added to the variant
  defaults of rigs that do ephys (to confirm; likely `basic` and `loom`).
- **Session-start confirm** (extends `confirmRigShortfall`): for an ephys
  session, lists a missing ephys sync module and an unconfirmed or stale sync
  line ("Sync line on ttl_2904 → Digital In 1 not confirmed in Open Ephys
  since 2026-11-02").

### 5. Post-processing

- `ephys_data.sync_pin` uses the declared `sync` role (`pin_role` column, or
  the session's wiring snapshot) first, and keeps the current guess only for
  data recorded before this, or as a cross-check that warns when they differ.
- When an ephys session's `_ephys/` upload is aligned, record the result on
  the module (`last_verified_session`: session, channel, fit quality) and show
  "Last session's sync verified" on the Ephys sync card. That is the only
  automatic proof the line reached the acquisition system.
- The session list flags ephys sessions with no `_ephys/` upload yet.

## Phasing

1. `ttl.application` + pin roles in the module (config, validation,
   `pin_role` CSV column), `saviour-config` prompt, the application shown in
   module lists. `mixed` behaviour unchanged. Tests in
   `src/modules/tests/test_ttl_module.py`.
2. Ephys sync card: lines with channels, wiring checklist, Test line, "Seen in
   Open Ephys" with expiry. `dev:mock` support.
3. Ephys session flag, `application` / `when` in `rig_check.py`, the
   session-start confirm and the session record.
4. Post-processing: declared sync pin in `ephys_data`, verification result
   back on the card, missing-upload flag.
5. `neurologger_sync` and `stimulus_control` cards once their guidance is
   known.

## Alternatives considered

- **A child module type (`ephys_sync` subclassing `TTLModule`)**, as
  `apa_camera` / `habitat_camera` are to the camera base. Gives the module its
  own ID prefix, card and rig-check type with no new matching. Rejected:
  every application would be another type (`neurologger_sync`,
  `stimulus_control`, ...), each widening the ~22 exact-`"ttl"` checks, and
  changing a module's job changes its ID and breaks continuity with its
  earlier sessions and config.
- **Pin roles only, no module-level application** (a module with a sync pin
  is shown as "Ephys sync"). Handles mixed-use modules naturally but gives no
  place to hang a streamlined card or application-specific guidance, and
  modules are almost always dedicated anyway. Kept as the inner layer.

## Open questions

1. Ephys flag on the session only, or also a rig-wide default ("this rig
   always records ephys") that pre-ticks it?
2. Should the `sync` role's pseudorandom timing be locked to known-good values
   for alignment, or a default the user can edit (maybe behind "Advanced")?
3. Which neurologger(s) do the labs use, and how does its sync input work
   (TTL into a digital input, IR LED)? Decides the `neurologger_sync` wiring
   and confirmation steps.
4. Which rig types get the `when: "ephys"` Ephys sync item in their defaults?
5. Confirmation expiry: 7 days, per session, or only when the wiring changes?
