# Field install feedback (1 camera + 1 mic, Oct 2026)

- **Status:** in progress (Phases A, C shipped 2026-10-09)
- **Created:** 2026-10-09
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (post-process preview,
  crop editor, session list, live mic monitor).

## Context

First outside install of a small rig (one `camera`, one `microphone`), used
as one session per trial (~48 sessions a day) and downloaded at the end of
the day. Nine issues were reported. Three are bugs with a cause found in the
code (1, 3), one is a bug not yet diagnosed (5), one is an unexplained
artefact (9), and five are feature requests (2, 4, 6, 7, 8).

| # | Item | Kind | Phase |
|---|------|------|-------|
| 1 | Post-process preview with spectrogram never finishes | bug, cause found | A |
| 2 | Spectrogram height not adjustable | feature | A |
| 3 | Crop distorts / stretches the image | bug, cause found | B |
| 4 | Crop: aspect-ratio presets, draggable selection | feature | B |
| 5 | "Clear Crop" does nothing | bug, needs logs | B |
| 6 | Timestamp at the bottom of the frame | feature | C |
| 7 | Download all sessions | feature | D |
| 8 | Group the session list by date | feature | D |
| 9 | Live mic spectrogram: periodic shifted vertical slice | artefact, needs evidence | E |

## Evidence still needed from the site

- **Item 1:** controller, `journalctl -u saviour -g "compose preview" --since today`
  (confirms slow-not-hung, and whether the spectrogram step errored).
- **Item 5:** camera module,
  `journalctl -u saviour -g "crop|ScalerCrop|live camera controls" --since today`.
- **Item 9:** a screenshot of a blip; whether it happens only while recording
  or also idle, and roughly how often; mic module,
  `journalctl -u saviour -g "discontinuity|Monitor thread" --since today`.

None of these block Phase A, C or D.

---

## Phase A: post-process preview (items 1, 2)

**Shipped 2026-10-09** (see `docs/CHANGELOG.md`). Defaults chosen: strip 20%,
panel 30% of the video height.

Branch `fix/compose-preview-speed`. ~1 day.

### 1. Preview never finishes

**Cause (from code).** The preview is slow by design rather than hung:

- The camera thumbnail is decoded by `video_compose._representative_frame`
  → `_StreamCursor.sync_to(t)` at the midpoint of the session. The cursor
  reads forward frame by frame, so the first preview of a 30 min / 30 fps
  recording decodes ~27k frames on the Pi, over the share. It's cached
  after that (`_stream_thumb`), but only once it finishes.
- `compose.discover_streams` runs `_prestage_skip` →
  `_video_frame_count` (`ffprobe -count_packets`, which reads the whole
  `.ts`) on **every** preview request, i.e. on every debounced settings
  change (colour, gain, freq range), not cached.
- `web.py` `compose_preview` starts a new thread per request with no
  cancellation or coalescing, so changing settings while the first one
  runs piles up concurrent decodes that compete for the same CPU and share.
- The result is a broadcast `socketio.emit`, not addressed to the
  requesting client, and carries no request id. The frontend clears its
  "Rendering…" state on whichever reply arrives first.
- `audio_align._run` (ffmpeg) has no timeout. The spectrogram itself is
  cheap (20 s of audio), but a stuck ffmpeg would hang the preview for good.

**Fix.**

1. Thumbnail via an ffmpeg keyframe seek instead of a sequential decode:
   `ffmpeg -ss <t_s> -i <video> -frames:v 1` (input-side `-ss` seeks to the
   nearest keyframe; good enough for a layout preview). `t_s` comes from
   the CSV time at the chosen fraction minus the first frame's time. Keep
   `_StreamCursor` for real renders, where exact alignment matters.
2. Cache `_video_frame_count` / `probe_dimensions` per
   `(path, size, mtime)` in `compose.py` (module-level dict, small LRU).
3. One preview worker per controller: a single thread plus a
   "latest request wins" slot. A request arriving while one runs replaces
   the queued one. Work in progress isn't cancelled, but at most one
   follow-up runs.
4. Reply only to the requesting client (`to=request.sid`) with a
   `request_id` echoed back. The frontend ignores stale replies and only
   clears `previewing` for its latest id.
5. A timeout on preview ffmpeg/ffprobe calls (e.g. 30 s), so a failure
   becomes an error message rather than an endless spinner.

### 2. Spectrogram height

Today it's hard-coded: `panel` is 40% of the video height, `strip` 18%, in
`video_compose._attach_audio_preview` (preview) and in the real render path
(`compose._apply_audio` → `audio_align`), and the preview spectrogram PNG is
rendered at `width * 0.28`.

**Fix.** Add `audio.spectrogram.height_pct` to `AudioSpec` (default 40 for
panel, 18 for strip; clamp 10-100 for panel, 5-50 for strip). Use it in
both preview and render, and include it in the preview PNG cache key.
Frontend: a "Spectrogram height (% of video)" number input, or a slider, in
`ComposeVideoPanel.jsx` next to Gain, persisted with the rest of `spec`.

**Tests.** `compose`: probe cache hit on a second call; `height_pct` reaches
both preview and render; clamping. `video_compose`: keyframe-seek thumbnail
on a short fixture `.ts`. Frontend: `npm run build` + lint.

---

## Phase B: crop editor rework (items 3, 4, 5)

Branch `fix/crop-editor`. ~1-2 days, plus a hardware check on a camera.

### 3. Distortion: two causes

1. **Aspect mismatch.** `ScalerCrop` selects a sensor rectangle that the
   ISP then scales to the fixed output size (`camera.width × camera.height`).
   Any rectangle whose aspect ratio differs from the output's is stretched.
   "Lock aspect ratio" in `CropEditorModal.jsx` is off by default.
2. **Editing on an already-cropped image.** The editor's snapshot
   (`/snapshot.jpg`) shows the *currently cropped* view, but
   `camera_base._compute_scaler_crop_rect` maps the drawn rectangle onto
   the *full* sensor (`crop_limits`). A second crop therefore lands in the
   wrong place, and the error compounds with each edit.

### 4. Aspect presets + draggable crop

ScalerCrop can't change the output's shape, so a crop with a different
aspect ratio needs either a locked ratio or a different output size.

**Decided 2026-10-09: (b).**

- **(a)** Crop is always locked to the output's aspect ratio. Simple and
  stops the stretching. Presets would then only pick the output resolution.
- **(b)** Choosing a preset (Free / 1:1 / 4:3 / 16:9 / 3:4 / 9:16) also
  sets the recorded resolution to match the crop, keeping roughly the same
  pixel count as the current output, rounded to even dimensions (multiples
  of 16 preferred for the encoder). This is what was asked for. It
  restarts the camera (`camera.width/height` are `_CAMERA_RESTART_KEYS`,
  deferred while recording) and changes the file dimensions, which compose
  already handles (it probes per-stream dimensions).

"Free" under (b) means the output follows whatever rectangle is drawn,
again rounded.

**Editor changes (either option).**

- Edit against the **full field of view**: the modal asks the module for
  an uncropped snapshot (new `snapshot.jpg?full=1`, or a temporary
  full-frame ScalerCrop while the modal is open, restored on close) and
  shows the current crop as an overlay on it. This removes cause 2.
- The rectangle can be moved by dragging inside it and resized from corner
  and edge handles, with the chosen ratio held while resizing. Clamp it
  to the image. Show the resulting output resolution in the sidebar.
- Store the crop **normalised** (0-1 of the full FoV) plus the preset,
  rather than in preview pixels. `_compute_scaler_crop_rect` then scales
  by `crop_limits` with no dependence on the snapshot size, which also
  removes the stale-`preview_width` case. Keep reading the old
  pixel-space shape for existing configs.
- Pointer events instead of mouse events, so it works on a touchscreen.

### 5. Clear Crop does nothing

The code path looks correct: `set_camera_crop(None)` → `config.set` →
`configure_module(["camera.crop_rect"])` → a full-frame `ScalerCrop` in
`live_controls` when streaming (`_full_frame_scaler_crop`). When **not**
streaming, `_configure_camera()` just omits `ScalerCrop`, and libcamera may
keep the previous crop across reconfigure. Best guess pending the module
log; fix that regardless by always passing an explicit full-frame
`ScalerCrop` from `_configure_camera` when no crop is set. Also confirm the
modal refreshes after `camera_crop_updated` and that `initialCropRect` in
`CameraConfigCard` / `APACameraConfigCard` isn't stale on reopen.

**Tests.** `test_camera_base.py`: normalised → ScalerCrop mapping, legacy
pixel-space shape, (b) output-size computation per preset (even/16-aligned,
pixel budget), full-frame ScalerCrop present in `_configure_camera` when
cleared. Hardware: draw 1:1, 16:9 and 9:16 crops on the desk camera and
check the recorded `.ts` for undistorted geometry (a printed grid in frame);
crop → re-crop → clear.

---

## Phase C: timestamp position (item 6)

**Shipped 2026-10-09** (see `docs/CHANGELOG.md`).

Branch `feat/timestamp-position`. ~½ day.

`camera_base._apply_timestamp` always places the text at the top
(`y = text_height + padding`), or, with a skipped 90°/270° rotation
(`compensate_k`), on the edge that becomes "top" after the viewer rotates.

**Fix (decided 2026-10-09: top stays the default, per-camera setting).**
New `camera.timestamp_position` (`"top"` | `"bottom"`, default
`"top"`) in every camera variant's config JSON, so existing rigs are unchanged.
Bottom: `y = view_height - padding`. In the `compensate_k` branch, paste
onto the opposite edge. Add it to the cache key and to `_cb_keys` in
`configure_module_special` so it applies live. Frontend: a select next to
the existing timestamp/text-size controls in `CameraConfigCard`. Check the
other overlays drawn at the top (exposure warning at `(10, 22)`) don't
collide when the timestamp moves. The MJPEG preview uses the same function,
so it follows automatically.

**Tests.** The layout for top/bottom with `compensate_k` 0, 1, 3 lies inside
the frame on the expected edge.

---

## Phase D: session list by date + bulk download (items 7, 8)

Branch `feat/session-list-by-date`. ~1 day. Pairs with
`plans/session-list-multi-select-delete.md`; build both on the same grouping.

### 8. Group by date

`SessionList.jsx` renders a flat newest-first list. Group by the local date
of `start_time`. Sessions with no start yet (pending, scheduled) go in an
"Upcoming" group at the top. Each group header is collapsible and shows the
date (`2026-10-09`) and a count. Today and Upcoming are open by default,
older dates collapsed. The open/closed state is remembered per browser
(`usePersistedState`), and the group holding the selected session is always
kept open. Apply the same grouping to the Post-Process session `<select>`
with `<optgroup>`s.

### 7. Download all / by day

There's a streaming per-session zip already (`/api/sessions/<name>/download`,
`_stream_zip_response`, `ZIP_STORED`, built incrementally, token-authed).

**Decided 2026-10-09: tick-based selection.** Each session row gets a
checkbox, and each date header gets a checkbox that ticks every session in
that day (tri-state when only some are ticked). A selection bar appears
when anything is ticked: "N selected · <size> · Download · Clear". The
same selection model is what `plans/session-list-multi-select-delete.md`
needs for bulk delete, so build it once here and that plan adds a Delete
button to the bar.

**Fix.** Add `GET /api/sessions/download?names=a,b,c&token=…` (POST with a
JSON body if 48 names make the URL too long). It
validates each name with the existing regex and realpath check, and streams
one zip with each session as a top-level folder. Zip64 is already on, and
the archive is built as it's sent, so 48 sessions of video (tens of GB) don't
need disk or memory on the controller. The selection bar shows the total
size (from the existing file-info data) and asks for confirmation above a
threshold (e.g. 5 GB). Ticking is disabled for sessions that are still
active or exporting, with a tooltip saying why.

Watch-outs: the download competes with module exports for the share's
bandwidth. Fine at the end of the day, but say so in the confirmation if
any session is still exporting. Very long zips can hit browser or proxy
timeouts. The Tailscale `serve` path has been fine for single sessions;
test a ~10 GB multi-session download over it.

**Tests.** Route: invalid name rejected, path traversal rejected, two
sessions → two top-level folders in the zip. Frontend build + lint.

---

## Phase E: live mic spectrogram blip (item 9)

Branch `fix/mic-monitor-blocks`. Diagnose first, then ~½ day if it's the
expected cause.

**Observation.** In the AudioMoth live monitor, periodically one vertical
slice of the spectrogram appears shifted down on the Y axis.

**What the code says.** Each column is one Hann-windowed FFT of a 4096-sample
block (`_monitor_audiomoth`, ~21 ms at 192 kHz), from a second `soundcard`
recorder on the same device as the recording thread. The renderer stacks
stored columns and `cv2.resize`s them, treating every column the same, so a
single odd column means one odd block of audio, not a drawing bug.

**Hypotheses.**

1. **Monitor thread falls behind (most likely).** A GIL or CPU stall (the
   recording thread's block write, or the MJPEG render in the same process)
   lets the PulseAudio/PipeWire buffer overrun, so samples are dropped or
   the gap is filled with silence. A block with a zero run is quieter
   overall (the column shifts down the colour scale). A block with a jump
   smears energy across frequencies. Both read as "a shifted slice". A
   regular period would point at something periodic in the recording path
   (`microphone.frame_num` block cadence, segment rotation, export). `soundcard`
   warns `data discontinuity in recording`, but Python shows a given
   warning only once.
2. **Real frequency shift.** If a steady tone moves to a lower frequency in
   that column, the block was resampled differently: PipeWire rate
   matching with two readers on one device. Less likely; investigate only if
   the screenshot shows this.

**Diagnosis.** Screenshot + journal (see "Evidence" above). Then on the desk
mic: log every discontinuity (`warnings.simplefilter("always",
SoundcardRuntimeWarning)` scoped to the monitor thread, rate-limited) with
a timestamp, and correlate with the recording thread's block writes and
segment rotation. Play a fixed tone to tell hypothesis 1 from 2.

**Fix for hypothesis 1.** Split the monitor into a capture thread that does
nothing but `recorder.record()` into a bounded `queue.Queue`, and a
processing thread that does the FFT and updates `monitor_data`. Count
discontinuities and queue overflows and show them in the monitor header
(e.g. "dropped 3") rather than hiding them, and drop a block with a
known gap rather than drawing it. Also check the recording thread
itself: if the monitor drops samples, confirm the *recording* doesn't (its
own block-timing check at `microphone_module.py` ~L418-443 already flags
late blocks).

---

## Order and effort

| Phase | Branch | Effort | Blocked on |
|-------|--------|--------|-----------|
| A | `fix/compose-preview-speed` | ~1 day | nothing |
| C | `feat/timestamp-position` | ~½ day | nothing |
| D | `feat/session-list-by-date` | ~1 day | nothing |
| B | `fix/crop-editor` | 1-2 days | item 5 log, hardware check |
| E | `fix/mic-monitor-blocks` | ½ day after diagnosis | screenshot + journal |

Each phase merges to `staging` on its own; frontend changes get
`npm run build` and lint for all five variants (crop and session list live
in `basic/` and are shared).

## Decisions (2026-10-09)

- Crop: (b), presets change the recorded resolution.
- Download: tick individual sessions; a date header's tick selects the
  whole day.
- Timestamp: top stays the default; per-camera setting.
