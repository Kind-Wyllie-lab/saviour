# APA camera: take rat detection off the capture thread

**Status:** planned (2026-11-10). Not started.

## Problem

On the APA rig on 2026-11-10 (`apa_camera_3606`, Hailo-8L, 1000×1000 @ 25 fps,
`object_detection.enabled=true`, `backend=hailo`, rat HEF), every session
recorded at **21.6–22.0 fps against a 25 fps target, about 13% of frames
dropped**. This held from the first minute to the last, and in all four
sessions (Wildtype_Trial-231/232, training-1/2). The module raised `unstable
capture (11–15% frames dropped, gap CV ~0.48)` on every 10 s health check, and
the controller raised the matching session health warning plus `liveness gap …
still open after 10 min`. The operators saw a sluggish livestream.

Evidence:
- `_recording.json`: e.g. training-2 had an encoder window of 629 s (~15,735
  frames expected), 13,702 CSV rows and `dropped_before_total` 2,031.
- Frame gaps in `_timestamps.csv` come in clumps: about 3% each of 2×, 3× and
  4× the 40 ms period. That shape means the callback stalled; the sensor did
  not skip.
- On the camera, one Python thread (the picamera2 request callback) sat at
  **~79% CPU**, and the process as a whole at ~123%. No throttling, 68 °C.
- PTP was fine (±10 µs, gate 50 µs). It's the only framesync camera, so there's
  no client to lag. Although the warnings mention "sync", this is not a sync
  problem.

Cause: `ApaCameraModule._process_main_frame`
(`src/modules/variants/apa_camera/apa_camera_module.py`) runs the whole
tracking pipeline **synchronously in the capture callback, on every main
frame**: mask, then `HailoDetector.detect()` (CPU resize + BGR→RGB at full
1000×1000, then a blocking `Hailo.run()`), then the track update, then the zone
test and status sends. At 25 fps the callback has 40 ms per frame and averages
~31 ms, so any slow inference costs the next 1–3 frames. The preview path
shares the same callbacks and the GIL, so the livestream stalls with it.

`hailo_camera` solved the same problem already: a `hailo-infer` worker thread,
a one-slot drop-oldest queue, and `infer_every_n`. `apa_camera` never adopted
it. Detection is off in the shipped defaults, so this only appears on a rig
that has turned it on for automated shocking, which is the main use of the
APA rig.

## Goal

With detection on, recording and livestream hold the configured fps (≤0.5%
drops, gap CV in line with detection off). Tracking runs as fast as the Hailo
allows, at whatever rate that turns out to be. The automated-shocking
contract still holds: `zone_entered` / `zone_exited` edges, plus a
`zone_state` report at least every 0.2 s, against the controller's
`apa.auto_shock.zone_state_timeout_s` (0.5 s).

## Design

Copy the `hailo_camera` pattern and adapt it to tracking.

1. **Capture thread (`_process_main_frame`) does no inference.**
   - Apply the record mask as now.
   - Hand a frame to the worker through a `queue.Queue(maxsize=1)`, dropping
     the oldest if it's full. Send a `(frame_copy, timing.timestamp_ns,
     frame_id)` tuple. If the mask isn't burned in, copy the masked frame
     (`_masked_copy`) so detection still only looks inside the arena.
   - Optional `object_detection.infer_every_n` (default 1: hand over every
     frame and let the queue drop the excess). It's a knob for an 8L that
     still can't keep up.
   - Read the latest published track snapshot (see 3) for the CSV columns and
     the recorded overlay. Never block on the worker.
2. **Worker thread `apa-infer`** loops: get the frame, then `detect()` under a
   `_det_lock`, then `_update_track()`, then the zone test, then edge events
   and periodic `zone_state`. The tracker, smoothing, jump gate and lost
   timeout all move here unchanged. They then run at the inference rate, so
   check that `jump_confirm_frames` (3) and `lost_timeout_s` (0.5 s) still
   make sense at, say, 12 Hz rather than 25 (3 frames becomes ~250 ms).
   Event timestamps use the **inferred frame's** `timestamp_ns`, not the time
   of publication.
3. **Published snapshot.** The worker swaps in one immutable object per result,
   `(det_cx, det_cy, in_zone, conf, track_det, src_frame_id, src_ts_ns)`. A
   single attribute assignment is atomic, so the capture thread reads it
   without a lock.
4. **CSV.** `det_cx/det_cy/in_zone/det_conf` show the latest snapshot on every
   row as now, plus a new **`det_frame_id`** column: the frame the detection
   was computed on. Offline analysis can then tell fresh rows from repeated
   ones. `tools/apa_track.py` and the frontend's CSV readers must ignore an
   unknown column; check both.
5. **Lifecycle** (mirror `hailo_camera`'s `_start/_stop_inference_worker`):
   start the worker when a detector is built. `stop()` and a backend or HEF
   swap must join it **before** `detector.close()`, because closing HailoRT
   under an in-flight `run()` is a hard SIGABRT. Reset the tracker and drain
   the queue on `_start_new_recording`.
6. **Telemetry.** The worker keeps a rolling inference latency and rate. Log
   them once a minute next to `Recording alive:`, and add them to the
   `zone_state` payload (`infer_hz`) so the controller and UI can show when
   tracking is too slow for the shock timeout.
7. **Blob backend** (`backend=blob`) uses the same worker. It's CPU-only and
   still shouldn't sit on the capture thread.

## Out of scope

- Moving detection onto the lores stream. That would be cheaper, but the
  1000×1000 coordinates feed the zone test and the CSV. Revisit if the worker
  alone isn't enough.
- Exposure: on this rig `exposure_time` is ignored with `manual_exposure=false`
  and the sensor sits at 39.7 ms / gain 1.0, almost a full frame of motion
  blur. That's separate, so it goes to `backlog.md`.
- Controller `Error parsing status data … 'APAController' object has no
  attribute 'web'` (seen 6× on 2026-11-10). Separate bug, also backlog.

## Tests

- Unit (no hardware): a fake detector with a configurable `sleep`. Assert that
  the capture hook returns within a few ms whatever the detector latency is;
  that a slow detector drops queued frames instead of stalling; that the
  snapshot's `src_frame_id` only ever increases; that edge events carry the
  source frame's timestamp; and that `stop()` joins the worker before
  `close()`.
- Existing `apa_camera` and shock-automation tests stay green.

## Acceptance on the rig

1. Same config as 2026-11-10 (1000×1000, 25 fps, rat HEF, detection on). Run
   a 10 min session: `dropped_before_total` ≤0.5% of expected frames, no
   `unstable capture` warnings, livestream smooth.
2. Record the inference rate from the new telemetry. It must leave room under
   the 0.5 s zone-state timeout (aim for ≥8 Hz). Write the figure in
   `docs/HARDWARE_NOTES.md`.
3. A dry-run auto-shock session (`mode=dry_run`): zone entries and exits match
   `tools/apa_track.py` run offline on the same video (latency to `in_zone`
   ≤ one inference period plus `entry_latency_s`).
4. Run a session with detection off as a baseline for gap CV.

## Rollback

Ship as one commit. In the meantime the rig can switch tracking off
(`object_detection.enabled=false`), at the cost of auto shocking, or drop to
20 fps.
