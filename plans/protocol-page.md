# Protocols page: a capability-aware protocol designer

Status: planned 2026-11-26; phase 1 (the page) built 2026-11-26, not yet viewed in a browser. Builds on
`plans/session-protocol.md` (format, expansion, TTL + sound execution; its
"Capability manifest" section is designed but not built) and
`plans/sound-library.md` (recordings vs built sounds, per-step rendering).

## The model (agreed with the user, 2026-11-26)

- A **protocol** is a planned series of **actions** that modules take while
  it runs, e.g. "5 min after the start, play sound X every 60 s, 5 times";
  "hold TTL line 2 high for the first 10 min".
- A protocol has **requirements**: the hardware it needs to run, e.g. one TTL
  module (with lines 1-2 scheduled) and one sound module (with 2 outputs and
  sounds X, Y). A session's target is checked against them.
- Visually: **time on the X axis, one row per action target on the Y axis**:
  a row per TTL line (its state over time), a row per sound output (which
  sound plays when). Later rows for events (what was seen) beside the plan.
- The **designer is capability-aware**: each module type tells it what
  **actions** it can be told to do (TTL: set / pulse / train; sound: play)
  and which **parameters** each takes, with the values this module actually
  has (its scheduled lines, its sound files and outputs). The designer builds
  its menus and checks from that, not from frontend code per module type.

## Today

- The designer is a modal (`ProtocolDesigner.jsx` + `ProtocolEditor.jsx`)
  opened from the TTL card or the New Session form, and checked against one
  TTL module: the one it was opened from, or the session target's first.
- What TTL and sound modules can do is hard-coded in the frontend
  (`protocolModules.js`, `ProtocolEditor.jsx` ACTION_LABELS / PlayFields) and
  in `src/shared/session_protocol.py` + its JS twin.
- A play step picks from the files a sound module reports; built sounds play
  their 1 s library copy cut to the step (no per-step rendering yet).

## Phases

1. **Protocols page** (sidebar, every variant). The library on the left, the
   open protocol on the right with the step table and timeline at full
   width. Checked against the whole rig: every TTL and sound module the
   controller knows (a TTL module picker when there are several), and the
   Test routine runs on that TTL module. The TTL card's and New Session
   form's designer windows stay for now, with a link to the page; they
   become links once the page covers everything they do.
   **Since (2026-11-26):** the editor is by line: the protocol declares
   `outputs` ({ttl: [pins], sound: [outputs]}, picked from Scheduled lines
   and the sound module's outputs), steps are grouped and numbered per
   line, repeats are trial blocks with each line's steps inside (shared
   random gaps), other-mode TTL lines are grey reference rows on the
   timeline. `outputs` is the seed of phase 2's `requires`.
2. **Requirements.** Derived from the steps (module types, TTL lines, sound
   outputs, sound files), shown at the top of the page ("Needs: 1 TTL module
   with lines 1, 2 scheduled; 1 sound module with 2 outputs and dog_bark.wav")
   with each one ticked or flagged against the rig, saved with the protocol
   (`requires`) so the library list and the New Session form can show them
   without expanding. The session check (`check_for_session` + JS twin)
   checks the target against `requires`.
3. **Capability manifest** (the `plans/session-protocol.md` design): modules
   advertise actions with typed parameters (number with unit/range, enum,
   pin from its own list, file from its own list, output from 1..n) in a
   `capabilities` status message on connect and on config change; the
   controller keeps the latest per module and serves it. TTL and sound
   first. The designer's action menus, parameter fields and row labels come
   from it; `protocolModules.js` per-type code goes.
4. **Rows on the Y axis.** The timeline gets one row per action target from
   the manifest (TTL line N state, sound output N with the sound's name on
   each block); hover shows the step; clicking a block selects its step.
5. **Sound steps.** Per-step rendering of built sounds built 2026-11-27 (module renders the recipe at the step's length; recordings cut). Still to do: the library picker with preview and looping recordings. Library picker (Recordings / Built sounds) with ▶ preview,
   output, level, length. Built sounds rendered on the sound module at the
   step's length when the session loads (`plans/sound-library.md`), lifting
   the 1 s limit; recordings longer steps loop (crossfade) with a warning for
   tones and calls. Session record gets per-step recipe, seed and audio hash.
6. **Test across modules.** Built 2026-11-26 (`test_protocol_rig`, sound
   `test_protocol`); not yet heard on the rig. Test routine runs the TTL and sound parts
   together (sound modules get a `test_protocol` like TTL's).

## Open questions

- Several modules of one type (two TTL modules): steps already carry a
  `module` tag; requirements could name roles ("TTL A", "TTL B") that a
  session maps to modules. Not needed for the first rigs; phase 2 counts
  types and tags.
- Where events (TTL inputs, APA zone entries) fit: as rows in the same view,
  read-only, once the after-session planned-vs-actual timeline exists.
