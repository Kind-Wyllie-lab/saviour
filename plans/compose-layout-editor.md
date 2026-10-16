# Compose layout editor (Post-Process)

- **Status:** proposed
- **Created:** 2026-10-14
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX".

## Why

Post-Process's compose panel is a form: pick cameras, a layout keyword
(`auto` / `side` / `stack` / `grid` / `loom`), an audio mode (`none` /
`track` / `strip` / `panel`) and its settings. The layout is decided for you:
cameras side by side or in a grid, spectrograms under or over the video. It
can't do "arena camera large on the left, home cage top right, both
AudioMoths as strips under the arena camera", a square crop for a figure, or
ten minutes out of a fourteen-hour day. Every new arrangement means a new
layout keyword in code (`loom` is one).

Turn it around: an editor where you pick the output size, place the
session's data sources on a canvas, and set each one's format. The same
layout description drives the editor, the preview and the render, so what
you see is what you get, and a layout can be saved and reused across
sessions.

## The page

1. **Canvas.** Output size: presets (1920×1080, 1280×720, 1080×1080,
   1080×1920) or custom (width multiple of 16, even height), background
   colour, optional margin. Drawn at the output's aspect in the middle of the
   page.
2. **Sources palette.** Everything the selected session/day recorded:
   - each camera (`discover_streams` / `camera_segments`);
   - each AudioMoth (`list_mic_devices`), placeable as **spectrogram**,
     **waveform** or **audio only** (a track, no tile);
   - event tracks: TTL edges, RFID reads, REST markers (`markers.csv`), shown
     as ticks or labelled events on a strip (phase 3);
   - text: session name, wall-clock time, elapsed time, a free caption
     (phase 3).
   Drag a source onto the canvas to make a tile; a source can be used more
   than once (e.g. one camera twice, cropped differently).
3. **Tiles.** Move, resize, snap (grid + edges of other tiles), z-order,
   delete. Per-tile options:
   - camera: fit / fill, crop rectangle, rotation (0/90/180/270), label on/off;
   - spectrogram: band (fmin/fmax), colour map, gain, frequency scale, scroll
     window (seconds across the tile), playhead position, label;
   - waveform: window, amplitude scale, colour;
   - event track: which channels/pins, window, label;
   - text: content (with `{time}`, `{elapsed}`, `{session}`), size, colour,
     background box.
   Reuses the dashboard's `DraggableTile` (`basic/components/DraggableTile`)
   for drag/resize/snap, in canvas coordinates scaled to the on-screen size.
4. **Timeline** (bottom): scrub to any time and the canvas shows the real
   frame at that moment (today's preview is fixed at mid-session); in/out
   points so a 14 h day can render 10 minutes; playback speed (0.5×-8×);
   optional jump to next event/marker (phase 3).
5. **Templates.** Save the arrangement as a named layout ("APA trial: arena
   cam + 2 mics"), stored on the controller like dashboard Saved Views
   (`dashboard_views.py` pattern: one JSON per layout under
   `/etc/saviour/controller/compose_layouts/`). Applying a template to another
   session binds its tiles to that session's sources by role (camera type /
   module name, mic folder + device order); unmatched tiles are flagged, not
   dropped.
6. **Render queue** as today (`ComposeJobRow`): progress, time left, history,
   Download, Re-run, Delete.

## The layout description

One JSON document replaces `ComposeSpec`'s `streams` + `layout` + `audio`:

```json
{
  "session_name": "NO-NAME-143435", "date_dir": "20261014",
  "canvas": {"width": 1920, "height": 1080, "background": "#000000"},
  "range": {"start_ns": null, "end_ns": null, "speed": 1.0},
  "fps": 15, "fmt": "mp4",
  "tiles": [
    {"id": "t1", "kind": "camera", "source": "ai camera",
     "rect": [0, 0, 1280, 720], "z": 0,
     "options": {"fit": "fill", "crop": null, "rotate": 0, "label": true}},
    {"id": "t2", "kind": "spectrogram", "source": "audiomoth/<device>",
     "rect": [0, 720, 1280, 180], "z": 0,
     "options": {"window_s": 20, "playhead": 0.5, "fmin_hz": 15000,
                 "fmax_hz": 96000, "color": "intensity", "gain": 2.5}},
    {"id": "t3", "kind": "text", "rect": [1300, 20, 600, 40], "z": 1,
     "options": {"text": "{session} {time}"}}
  ],
  "audio_tracks": ["audiomoth/<device>", "audiomoth/<device2>"]
}
```

Rects are in output pixels. Validation (`compose.py`) checks rects lie inside
the canvas, sources exist, kinds/options are known, at most N tiles. Old
requests (`layout` keyword + `audio` mode) are translated into a layout
document server-side, so existing callers and saved jobs keep working, and
`auto` / `grid` / `loom` become the editor's built-in starting templates.

## Rendering

- **One compositor.** `video_compose` already places camera frames per output
  frame from `_SegmentedCursor`s. Extend it to draw every tile kind per frame:
  - camera: today's `_fit_into`, plus crop / rotate / fill;
  - spectrogram: pre-render the device's aligned audio to one wide PNG
    (today's `pic_filter_nolegend` step in `audio_align.render_overlay`),
    then per frame blit the column window for time `t` into the tile and draw
    the playhead, the same maths as the ffmpeg pan used now;
  - waveform: pre-compute per-pixel min/max of the aligned audio, draw per
    frame;
  - event track: pre-load events, draw ticks in the window;
  - text: `cv2.putText` (or a small bundled TTF via PIL if the Hershey fonts
    look too crude).
  ffmpeg is then only used to align/stitch audio (unchanged:
  `render_aligned_audio_segments`) and to mux `audio_tracks` onto the
  compositor's video. The separate ffmpeg overlay pass
  (`render_overlay` / `drawtext`) goes away, so any tile can sit anywhere.
- **Range and speed:** the output clock runs `start_ns → end_ns` at `speed`;
  cursors already seek forward by time, so a later start costs one skip, not
  a full decode (seek to the segment containing `start_ns`, then decode
  forward). Audio is trimmed to the same range (`atrim`/`atempo` for speed,
  or audio dropped above 2× with a warning).
- **Preview at time t:** the same per-tile draw code renders one frame at the
  scrubbed time, with camera frames from a seek (`_representative_frame`,
  already segment-aware) and spectrogram/waveform from the cached wide PNG /
  peaks. Cache key = source + options + canvas size, as today.
- **Performance:** per-frame blits are cheap next to the H.264 decode that
  already dominates; wide spectrogram PNGs for a 14 h day at 1 px/frame would
  be huge, so render them per segment and blit across the boundary, or cap
  the pixel rate (`_strip_pixels_per_second` already caps it).

## Phases

1. **Layout model + editor (cameras + spectrograms).** Layout JSON and
   validation; old-request translation; compositor tile drawing for camera
   and spectrogram; preview at mid-session; the canvas editor with
   drag/resize/snap, canvas presets, per-tile option panels; built-in
   templates replacing the layout keywords. Replaces today's panel.
   Accept: an `auto` render through the new path is frame-identical in
   layout to today's; a hand-placed two-camera + two-spectrogram layout
   renders as drawn; preview matches a frame of the render.
   *Estimate: a few days.*
2. **Timeline.** Scrubbable preview time, in/out points, speed. Accept: a
   10-minute range from a multi-segment day renders only that range, starting
   in the right segment, audio in sync; the scrubbed preview matches the
   rendered frame at that time. *About a day.*
3. **Event tracks and text.** TTL / RFID / markers strips, text tiles with
   placeholders, jump-to-event. Accept: a TTL pulse lands on the frame its
   timestamp says, within one output frame. *About a day.*
4. **Templates.** Save/load/apply named layouts, binding by role, flagging
   unmatched tiles. Accept: a layout saved on one session applies to another
   session from the same rig with no edits. *About half a day.*

## Not in scope

Arbitrary video effects, transitions, multiple output files per render,
editing on the module side, or a timeline with clips on tracks (this is a
layout for one continuous window of a session, not a video editor).

Habitat (16 motion-triggered cameras + 16 AudioMoths, multi-day, rendered
on the habitat's own server) is out of scope here; this plan targets a
typical rig (a few cameras / mics / TTL). What habitat needs on top, and the
few things this design should keep open for it, are in
`plans/habitat-post-processing.md`.

## Open questions

- Waveform and event-track tiles: worth it in phase 1, or only spectrograms?
- Text rendering: Hershey (`cv2.putText`) vs a bundled TTF through PIL (adds
  Pillow to the controller env; nicer labels).
- Should templates be per variant (APA, habitat) or one shared list with
  role binding doing the matching?
