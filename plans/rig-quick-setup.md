# Quick setup: one button that walks through the rig before recording

- **Status:** proposed
- **Created:** 2026-11-20
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (one-liner)

## Why

Getting a rig ready is spread over several pages: the rig type lives in
`saviour-config`, expected hardware on the controller config Rig tab and the
banner, external sync on the New Session form, sync lines on each TTL card,
and "does every camera actually see the arena" on the dashboard. A
researcher setting up has no single place that says "this is an APA rig
recording ephys; these modules should be here; here is what each one sees;
all good".

**Quick setup** is an on-demand button (not a daily requirement, never
blocks a session) that opens a step-by-step modal through all of that, and
remembers the last run so it can be shown, not enforced.

## Design

### Where the button lives

- Recording page, beside the New Session form header ("Quick setup").
- System page header, and the rig-check banner's popover ("Run quick
  setup").
- Every variant (basic, APA, loom, acoustic startle, habitat): the modal is a
  shared `basic/components/QuickSetup/` component.

### Steps (a modal with a step rail; Back / Next; any step can be skipped)

1. **Rig type.** Shows the controller's variant ("APA rig") with a one-line
   description of what that rig type expects. Read-only: changing it means
   `sudo saviour-config` on the controller (rebuilds the web UI and switches
   the controller variant), which the step says. Switching from the browser
   is out of scope (see "Later").
2. **External sync.** None / Ephys (Open Ephys) / Neurologger (the
   `external_sync` kinds in `ttl_applications.json`; pyControl may join them
   later, not in this plan). The choice becomes the New Session
   form's default (`saviour_session_form_external_sync`), so the form opens
   on it; it doesn't change a session already created. Shows what the choice
   needs ("an Ephys sync TTL module") using `ExternalSyncField`'s logic.
3. **Expected modules.** The live rig check (`useRigCheck`), judged with the
   chosen external sync as condition: each expected item with present /
   missing / faulty / offline modules. **Add expected item** appends to
   `rig.expected` (same editor as the Rig tab, saved to controller config;
   admin only). A connected module that matches no item is listed as
   "connected, not expected" with **Add as expected** (prefills the item from
   its type). Naming a module that has never connected needs the roster from
   `plans/rig-expected-hardware.md` phase 2; until then, items are by type.
4. **Previews.** One connected module at a time, large: camera
   (`:8080` MJPEG), microphone (`:8081` spectrogram), TTL (`:8082`), RFID
   (`monitoring._port`), using `MJPEGStreamCard` and the Dashboard's
   `streamInfo`. Per module: **Looks OK**, or **Open settings** to fix what
   the preview shows (camera out of focus, aimed wrong or too dark; mic silent
   or clipping; a module in the wrong box) on that module's card, then come
   back. No separate "problem" state: a module is confirmed or not yet.
   Modules with no preview are listed with their readiness only. Arrow keys /
   buttons to move between modules; a thumbnail strip shows which are
   confirmed.
5. **Sync lines** (only when step 2 chose a sync kind with TTL lines): each
   sync module's lines with **Test line** and **Seen in Open Ephys / the
   logger** (the same controls and store as the TTL card's Lines tab,
   `ttl_lines.py`), so confirming wiring happens here too.
6. **Summary.** What was confirmed and what wasn't, who and when. **Done**
   saves it.

### Record of the last run (informational only)

- Controller-side store `src/controller/quick_setup.py`
  (`/var/lib/saviour/controller/quick_setup.json`): the last run's
  `{at, by (admin/guest), variant, external_sync, rig (summary), modules:
  {id: confirmed bool}, sync_lines: {...}}`. Only the last run is kept. Socket.IO `get_quick_setup` /
  `save_quick_setup` → broadcast `quick_setup`. Guests can run it; saving
  needs no admin (it changes nothing on the rig), editing `rig.expected` in
  step 3 does.
- Shown, never enforced: the New Session form's footer ("Quick setup run
  today at 09:12 · 1 module not confirmed" / "not run since 18 Nov"), and the
  System page.
- Each new session copies the latest run into `session_metadata.json`
  (`quick_setup`), so a dataset says whether and when the rig was checked and
  what was flagged.

## Phasing

1. Modal shell + steps 1, 2, 4, 6 (rig type, external sync default,
   previews with Looks OK / Open settings, summary) and the store; button on the
   Recording and System pages. `dev:mock` support (mock streams already
   exist).
2. Step 3 editing (add expected item / add as expected) and step 5 (sync
   lines, reusing `TTLLinesEditor` pieces).
3. Session metadata copy and the "last run" line on New Session.

## Decisions

- pyControl is left out for now; if added it would be another external sync
  kind.
- A preview that looks wrong sends the user to that module's settings card;
  there is no "problem" flag and nothing reaches the rig check.
- One last run is kept (no history).

## Later

- Switching rig type from the browser (`system_update.py` already has
  build-and-restart primitives; needs the controller variant switch too).
- Per-module checks beyond eyeballing: camera exposure / focus score,
  microphone noise floor (`plans/audio-noise-characterisation.md`), TTL
  loopback.
