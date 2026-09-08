# Hailo preview inference — move it off the capture thread

- **Status:** proposed
- **Created:** 2026-09-08
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work" → this file; sibling of
  `plans/multicam-frame-alignment-and-sync-provenance.md` (item 3, "hailo-camera
  load reduction"), which is where the measurements below came from.

## Problem

`hailo_camera` runs a stock model-zoo HEF over the **preview / lores** stream and
draws the detections on it. `_process_lores_frame()` does this **synchronously on
the libcamera capture-callback thread** — the same thread that feeds the H264
encoder for the recorded "main" stream. When one `detector.detect()` call
(resize → Hailo forward pass → CPU-side tensor decode/NMS) takes longer than the
frame interval, it stalls the whole pipeline: the sync **client** camera drops
capture frames *and* the encoder drops handed frames, and its capture cadence
gets jittery. That deficit is what makes the "ai camera" lag the frameserver by a
few frames in Post-Process compose.

### Measured (`tools/framesync_sweep.py`, desk rig, sync client = `hailo_camera_3606`)

Smoke run, **n=1**, 30 fps, 60 s, model `yolov8s`, `threshold 0.2`, `max_labels 10`:

| `hailo.infer_every_n` | client encoder deficit | `dropped_before` | gap-CV |
|---|---|---|---|
| off (`infer_enabled=false`) | 0 | 0 | 0.0002 |
| 8 | 0 | 0 | 0.0002 |
| 2 (file default) | 0 | 0 | 0.0002 |
| **1 (deployed rig setting)** | **1** | **6** | **0.056** |

- Sync **server** (`camera_d074`): 0 deficit / 0 dropped at **every** setting.
- PTP detrended-p95 ~38 µs throughout — **throughput, not timing.**
- It is a **step at `infer_every_n=1`**, not a gradient: a 2× cut in inference
  frequency fully fixes it, which says the cost is dominated by the `detect()`
  call, not the per-frame draw (unchanged n=1 vs n=2).

An n=5 shuffled sweep (30 fps) is running to put error bars on the small counts;
a 60 fps block and a `sync_mode:none` arm are follow-ups. Numbers here will be
updated when it lands.

### Why `detect()` is expensive

Two parts, only one of which a faster accelerator helps:

- **Hailo NPU** — the HEF forward pass. `yolov8s` @ ~640² on Hailo-8L is
  ~8–15 ms. The call releases the GIL during the hardware inference.
- **CPU (Pi 5 ARM)** — resize to model input, decode the raw output tensors
  (sigmoid / box-decode / NMS) in Python+numpy, buffer copies. Holds the GIL.
  For a small model at every frame this is often the larger half.

At 30 fps the preview is **not throttled** (`_stream_interval_s = 0` below 35 fps
— `_STREAM_FPS = 24` only kicks in above that), so the full
resize+infer+decode+draw+JPEG chain has a 33 ms budget on cores it shares with
the encoder.

## Proposed design — inference worker thread

Split the cheap-and-must-be-synchronous part (draw) from the expensive-and-
already-stale part (inference).

**Frame thread — `_process_lores_frame()` keeps only:**
1. `m.array.copy()` into a 1-slot "latest frame" buffer. The mapped DMA buffer is
   recycled after the callback returns, so a copy is unavoidable — ~1.2 MB,
   sub-ms, ~36 MB/s at 30 fps (nothing on a Pi 5).
2. Read `self._last_results` (plain attribute read — atomic under the GIL) and
   draw them onto `m.array` via the existing `_draw_*` helpers.
3. Draw the status line. Return.

No `detect()` call, no `_det_lock` held across inference on this thread.

**New inference worker thread** (started when streaming starts, stopped/joined on
stop and before any detector teardown):

```python
while not self._infer_stop.is_set():
    frame = self._latest_frame_get(timeout=0.5)   # queue(maxsize=1), drop-oldest
    if frame is None:
        continue
    with self._det_lock:
        det = self.detector                        # re-read; None during a rebuild
        if det is None:
            continue
        results = det.detect(frame, self._labels)
    self._last_results = results                   # atomic swap
    self._last_summary = _summarise(results)
    if self._infer_every_n > 1:                    # optional extra throttle
        self._infer_stop.wait(self._infer_every_n / max(self.fps, 1))
```

Runs as fast as Hailo+CPU sustain (~15–25 Hz for `yolov8s` on the 8L), naturally
self-throttling. `infer_every_n` becomes an optional power/thermal knob, not a
correctness crutch.

**Latest-frame slot:** `queue.Queue(maxsize=1)`. Frame thread:
`try: q.get_nowait() except Empty: pass` then `q.put_nowait(frame_copy)`
(drop-oldest). Worker: `q.get(timeout=…)`.

**Results / summary slots:** plain attribute assignment — atomic under the GIL,
frame thread reads lock-free. Same as today's `_last_results` / `_last_summary`.

**Detector rebuild (`_build_detector` swap) & shutdown — the one hard part.**
The current `_det_lock` comment warns that closing the Hailo device out from
under an in-flight `run()` aborts HailoRT (`std::system_error "Resource deadlock
avoided"` → SIGABRT). With a worker thread the rule is explicit:
**stop + join the worker before `detector.close()`, everywhere** — in `stop()`,
and in `_build_detector`'s swap path. Restart the worker after the new detector
is in place. `_det_lock` still guards the `detect()` ↔ `close()` race as a
belt-and-braces second line.

## Cost / benefit

**Benefit**
- Capture/encode thread does ~1 memcpy + draw per frame **regardless of model
  speed** → the `infer_every_n=1` capture drops and encoder backpressure go away
  (acceptance: re-run the sweep, `n=1` deficit/`dropped_before`/gap-CV return to
  the `off` baseline).
- Overlay lags one inference period (~40–65 ms) instead of stalling the pipeline
  — imperceptible for a preview overlay.
- **Neutralises the 13→26 TOPS hat question for frame drops** — a faster hat then
  only buys fresher overlays, which nobody has asked for.
- Same pattern later reusable for `habitat_camera` occupancy scoring and
  `apa_camera` on-camera detection (keep the worker generic-ish; scope to hailo
  first).

**Cost**
- ~80–120 lines net in `hailo_camera_module.py`. No `camera_base` change
  (`_process_lores_frame` is already a variant override point).
- New failure surface: thread lifecycle bugs. The teardown-ordering rule above is
  the load-bearing bit; get it wrong and it's a hard C-level abort, not a Python
  exception.
- Off-device tests can cover the worker loop with a fake detector, the
  frame-thread draw-only path, and teardown ordering; **the real behaviour
  (does it actually stop the drops, does teardown never SIGABRT) can only be
  validated on the Pi.**
- One extra ~1.2 MB frame copy per preview frame on the capture thread (bounded,
  measured negligible above).

## Alternatives

| Option | Effort | Fixes `n=1` drops? | Cost / downside | Verdict |
|---|---|---|---|---|
| **Do nothing — ship `infer_every_n=2`** | trivial (change the file default; stop overriding it to 1 on rigs) | Yes at 30 fps (0 deficit measured) | Overlay 1 frame more stale; unknown headroom at 60 fps / heavier models / hotter Pi; a future rig set back to 1 re-breaks it | **Do this now regardless** — it's the immediate mitigation. Not a structural fix. |
| **Throttle the preview** (cap `_STREAM_FPS` ~15 for the hailo variant, apply below 35 fps too) | small | Likely — halves *all* preview-path work (infer + decode + draw + JPEG) | Choppier live preview; still synchronous, so a slow model on a busy Pi can still spike | Cheap partial fix; complements the worker, not a substitute. |
| **Smaller model — `yolov8n`** | trivial (config) | Likely — ~3× faster inference + smaller output tensors (faster CPU decode) | Lower detection quality; still synchronous; doesn't help a future switch back to a big model | Worth trying; rig is already `max_labels=10` so `s` may be overkill. Orthogonal to the worker. |
| **`hailo.infer_enabled=false` for recording sessions** (shipped toggle; or a `pause_infer_during_recording` flag) | small (toggle exists; flag is ~15 lines) | Yes — zero inference load during the recording | No live overlay while recording — which is often fine, but loses it exactly when someone might want to watch | Good operational lever, already available manually. A per-session auto-pause is a reasonable small add. |
| **26 TOPS Hailo-8 HAT** | £ + a re-provision; HEF may need an `--hw-arch hailo8` recompile | Maybe — halves only the *NPU* half of `detect()`; CPU decode + GIL contention with the encoder unchanged. If the split is ~12 ms NPU + ~15 ms CPU, 33 ms budget → ~21 ms: marginal, not comfortable | Money for a partial, uncertain fix; doesn't address the architectural coupling | **Not recommended as the fix.** Instrument first (below) if seriously considered. |
| **Move inference to the worker thread (this plan)** | medium (~100 lines, on-device validation) | Yes — decouples inference latency from frame delivery entirely | Thread-lifecycle failure surface; teardown ordering must be exact | **Recommended structural fix.** |

### Cheap instrumentation to de-risk the hat question (do before buying)

Add timing to `_process_lores_frame` / the detector: log per-call `resize_ms`,
`infer_ms` (Hailo), `decode_ms` (CPU post), `draw_ms`, on a rate-limited line.
~20 lines. Directly shows the NPU-vs-CPU split and therefore the ceiling a faster
HAT could reach. Also useful as before/after evidence for the worker refactor.

## Recommendation

1. **Now:** change the `hailo_camera_config.json` default `infer_every_n` back to
   the file value (2 is already the default; the *deployed rig* had been
   overridden to 1) and stop overriding it to 1 on provisioned rigs. Zero-risk
   immediate mitigation.
2. **Small, parallel:** the `_process_lores_frame` timing instrumentation, so the
   NPU/CPU split is a known number.
3. **Structural:** build the inference worker thread on a branch, validate on the
   Pi with a re-run of `tools/framesync_sweep.py` (the same sweep that found the
   problem is the acceptance test).
4. **Skip** the 26 TOPS HAT as a fix for this. Reconsider only if the
   instrumentation shows `detect()` is >80 % NPU *and* the worker refactor is
   rejected.

## Acceptance

- Re-run `tools/framesync_sweep.py --repeats 5` (30 fps, and a 60 fps block):
  `infer_every_n=1` client `deficit`, `dropped_before`, and gap-CV are
  statistically indistinguishable from the `infer_enabled=false` arm.
- 10-plus detector rebuilds (`hailo.model` swaps) and 10-plus start/stop cycles
  under load with **zero** HailoRT abort / SIGABRT.
- Overlay still tracks the scene (visually: a moving hand's box is ≤2–3 frames
  behind).
- `_check_capture_cadence` (the live health check added alongside this
  investigation) stays "ok" during a recording with `infer_every_n=1`.

## Not doing

- Touching the recorded "main" stream path — the overlay is preview-only and
  stays that way.
- A generic cross-variant inference-worker abstraction up front — prove it on
  `hailo_camera`, generalise later if `habitat_camera` / `apa_camera` want it.
- GPU/accelerator scheduling tricks — the point is to stop *blocking* the capture
  thread, not to make inference itself faster.
