# Habitat post-processing (campaign renders on the habitat server)

- **Status:** proposed (later; not before `plans/compose-layout-editor.md`)
- **Created:** 2026-10-16
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (Post-Process compose).

## Context

A habitat is 16 boxes in a 4x4 grid, one `habitat_camera` per box and one
AudioMoth per box spread across 4 `microphone` modules (4 AudioMoths each).
Sessions run for days, and cameras record motion-triggered clips, not
continuous video.

The heavy renders (e.g. the 4x4 mosaic) don't run on the controller. Each
habitat has a server on its local network, which usually also acts as the
gateway (pilot habitat: `192.168.1.2`). Post-processing for a habitat
campaign runs there.

`plans/compose-layout-editor.md` is designed for a typical rig (a few
cameras, a few mics, TTL/RFID) composed on the controller. This plan records
what habitat needs on top, so the editor's design doesn't block it later.

## Why the controller compose path doesn't fit habitat

Found while reviewing the layout-editor plan against habitat (2026-10-16):

1. **Sparse clips.** `compose.camera_window()` renders only the time every
   selected camera covers. With 16 motion-triggered cameras that's usually
   empty ("selected cameras have no overlapping time window").
   `camera_segments.segment_at()` maps a time inside a gap to the clip
   before it, so a tile would freeze on its last frame, not show "no
   recording".
2. **Limits.** `compose.MAX_AUDIO_SOURCES = 8` (habitat has 16 AudioMoths);
   the layout editor's tile cap would need to be at least 32.
3. **No box identity.** Nothing records which camera module, mic module and
   AudioMoth serial belong to which box, or where a box sits in the grid.
   Role binding by camera type or mic device order doesn't work when all 16
   cameras are the same type and AudioMoths get swapped.
4. **Cost.** A 4x4 mosaic is up to 16 software H.264 decodes per output frame
   (the Pi 5 has no H.264 hardware decode), each scaled down to ~480x270,
   plus 16 spectrograms at 192 kHz. That belongs on the habitat server.
5. **Audio.** 16 AudioMoths mixed into one track is useless. The output wants
   one box's audio or one track per AudioMoth.
6. **Multi-day.** Compose works on one date folder; a campaign spans many.

## What habitat users want from post-processing

- **One box:** box N's camera, its AudioMoth and its events (RFID) over a
  time range.
- **Around an event:** a window of a few seconds either side of each clip /
  RFID read / marker, for one box or its neighbours, as joined-up ranges or
  one file each.
- **Overview:** the 4x4 grid over a long range with dead time skipped (or a
  time-lapse mosaic, one frame per N s per box), to see where and when
  activity happened.

## Proposed shape

1. **Box map.** A per-rig JSON on the controller (box id, grid row/col,
   camera module, mic module, AudioMoth serial), edited in the habitat UI.
   Useful beyond post-processing (dashboard placement, RFID reader to box).
2. **Box as a layout source.** In the layout editor, "Box 7" places its
   camera plus spectrogram together; templates bind by box id. Built-in
   templates: 4x4 grid from the box map, single box (camera, spectrogram,
   RFID strip).
3. **Gap-aware compositor.** Render over the union of the selected sources'
   coverage, draw a "no recording" placeholder in gaps, show per-source
   coverage bars on the timeline, optional "skip dead time" (only times when
   at least one tile has video). Decode only tiles with video at time `t`.
4. **Event-centred ranges.** "±N s around each clip / RFID read in box X".
5. **Campaign jobs issued to the habitat server.** The job is described in
   the SAVIOUR GUI (layout document + session/date range + event filter) and
   sent to the server, which runs the render against the share and reports
   progress back. The layout JSON from `compose-layout-editor.md` is the job
   format, so the same description works on both. Open: transport (REST on
   the server vs. the server polling the controller), the server's runtime
   (same `video_compose` code in a container?), and where outputs land.

## What the layout editor should do now so this stays possible

- Keep the layout document free of controller-only assumptions (paths
  relative to the session root; no controller state in it).
- Leave a `range` that can grow into a list of ranges.
- Make tile cap and audio source cap constants, not baked into the UI.
- Make the window choice (overlap vs. union) a compositor parameter, even
  if only overlap ships for typical rigs.

## Open questions

- Is the box map a habitat-only concept, or a general "subject/arena" map
  (APA arenas, multi-cage rigs) that habitat is one case of?
- What the habitat server already runs today for 4x4 renders, and whether it
  should converge on `video_compose` or stay its own tool.
