# Multi-camera frame indexing + quantified sync for the aligned ethogram

- **Status:** in progress
- **Created:** 2026-09-07
- **Owner:** ascottg
- **CLAUDE.md ref:** expands the "In-flight" bullet *"`camera_base.py` `_encoder_active`
  gate…"* (the `_StreamCursor` `frame[i]==row[i]` half) and the Post-Process /
  ethogram bullet; sibling to `plans/audio-video-sync-residual-validation.md`.

## Resume here (state as of 2026-09-08)

**New since 2026-09-07 (all merged to `staging`, PRs #369–372):**
- **REST `GET`/`PATCH /api/v1/modules/<id>/config`** — per-module config read +
  partial (deep-merge) write, live-verified on the controller. This is what
  `tools/framesync_sweep.py` drives. (`docs/REST_API.md`)
- **`tools/framesync_sweep.py`** — automated hailo-load × framesync sweep:
  PATCH both cameras → gate on PTP → timed session → SSH-pull
  `framesync_report.json` + `_recording.json` + `ffprobe` the `.ts` → one
  results row + a mean/sd pivot. Config snapshot/restore, shuffled condition
  order, `-count_packets` for speed.
- **`hailo.infer_enabled`** config key (default true) — off = skip the HEF,
  plain camera. Drives the sweep's "inference off" arm.
- **`camera_base` live capture-cadence check** — rolling window of inter-frame
  deltas + `dropped_before`; `@check() _check_capture_cadence` (advisory) and a
  broadened `_check_recording_alive` (was silence-only → now also fails on a
  sustained elevated drop rate / half-speed capture, feeding the existing
  `recording_health_warning` → controller alert). `recording._cadence_*` keys.
- **Item 3 quantified** — see below. Provisional finding: the deployed ai
  camera runs `infer_every_n=1`, the one setting that costs frames; `≥2` is
  clean. n=5 sweep running to confirm.
- **New sibling plan `plans/hailo-inference-threading.md`** — move preview
  inference off the capture thread (the structural fix for item 3), with a
  cost/benefit + alternatives matrix (faster HAT, lower preview fps, smaller
  model, disable-during-recording, do-nothing).

**Done & on `staging` (2026-09-07):**
- **B3** — `_StreamCursor` proportional row→frame remap + per-stream mismatch
  warning → `ComposeJob.warnings` → `ComposeVideoPanel`. Bounds a client-camera
  skew to ~½ the deficit and never ships it silently. (`d8db4e57`, `1fe2e22d`)
- **A3** — `<stem>_recording.json` per-segment provenance sidecar, written
  pre-remux, staged for export. Carries `csv_rows_written`, `encoded_frames`
  (ffprobe), `deficit_vs_csv`, encoder window, `sync_mode`, `dropped_before_total`.
  (`700f6f5a`, `a518b6ae`, `ba1255c8`)
- **`recording.fix_positioning_timestamps`** config toggle + Camera card
  checkbox + base_config key. (`2b051951`, `bf956b7f`)
- **`tools/check_frame_counts.py`** — per-camera CSV-rows vs `.ts`-frames report.
  (`48240028`)
- **A4 measured** (`a4_test-174323`): the `.ts` remux is **not** the cause; the
  sync-client's H264 encoder drops handed frames under backpressure. See the A4
  result box below. A1a (fix the remux) is dropped.

**Next, in order:**
1. **B1** — overlay-timestamp repair in `_StreamCursor` (frame-accurate; the
   primary fix now that the module can't prevent the drops). + **B2** (consume
   `_recording.json` `encoded_frames` when present, skip the ffprobe/OCR).
   Detail: "Consumer side" section below.
2. **Sync-provenance block** — the `_align.json` / compose / ethogram caption
   with per-modality-pair method + residual + verdict. Detail: "Defect 2" below.
3. *(mitigation, not blocking)* hailo-camera load reduction.
   **Quantified 2026-09-08** (`tools/framesync_sweep.py`, REST-API driven,
   desk rig, sync client = `hailo_camera_3606`, 30 fps, 60 s, n=1 smoke):

   | `hailo.infer_every_n` | client encoder deficit | `dropped_before` | gap-CV |
   |---|---|---|---|
   | off (`infer_enabled=false`) | 0 | 0 | 0.0002 |
   | 8 | 0 | 0 | 0.0002 |
   | 2 (file default) | 0 | 0 | 0.0002 |
   | **1 (rig was running this)** | **1** | **6** | **0.056** |

   Sync *server* (`camera_d074`): 0 deficit / 0 dropped at every setting.
   PTP detrended-p95 ~38 µs, flat — throughput not timing. A **step at
   `infer_every_n=1`**, not a gradient → the cost is the `detect()` call, not
   the per-frame draw. n=5 shuffled sweep + a 60 fps block + a `sync_mode:none`
   arm are running / queued; numbers land in `plans/hailo-inference-threading.md`.
   - **Immediate mitigation (do regardless):** the file default `infer_every_n`
     is already 2 — stop overriding provisioned rigs to 1.
   - **Structural fix:** move preview inference off the capture-callback thread
     → **`plans/hailo-inference-threading.md`** (design + cost/benefit +
     alternatives: 26 TOPS HAT, lower preview fps, `yolov8n`,
     disable-during-recording).
   - The 26 TOPS Hailo-8 HAT is **not** the recommended fix — it halves only
     the NPU half of `detect()`, leaving the CPU-decode + encoder GIL
     contention. Instrument the NPU/CPU split first if seriously considered.
4. **Decide `camera.sync_mode` default** — free-run vs framesync. Recommendation
   + reasoning in the "Decision to make" section below; framesync is what
   *causes* the client skew, and behaviour work doesn't need sub-frame
   cross-camera identity.

**Open question for B1:** confirm the overlay text position/font is stable
enough per camera variant (`camera` vs `hailo_camera`) for a numpy
digit-template match, and whether a `friendly_name`/serial in the overlay
complicates the parse. Fallback stays B3's proportional map + warning.

## Why this exists

An **ethogram with aligned video + audio/spectrogram + ephys** is a target
deliverable (Post-Process page). That makes camera-to-camera and
camera-to-audio alignment a *scientific* output, not a QA convenience — and the
requirement is that the sync level is **stated upfront and quantified**, not
silently "looks fine".

Two concrete defects block that today:

1. **`video_compose.py::_StreamCursor` mis-indexes a libcamera sync *client*
   camera**, so it lags the sync *server* camera in the composite/ethogram by a
   few frames, drifting worse through the clip.
2. **Nothing states the achieved sync level.** `_align.json` has some of the
   numbers; the composite / ethogram carry none.

## Defect 1 — the client-camera frame skew

### What was observed (2026-09-07)

Two desk sessions, composed on the Post-Process page:

| session | `camera` d074 = **sync server** | `ai camera` 3606 (hailo) = **sync client** |
|---|---|---|
| test-143757 | 453 CSV rows / 453 `.ts` frames — exact | 457 rows / **455** frames → **−2** |
| NO-NAME-105539 | 544 / 544 — exact | 542 / **537** → **−5** |

Effect in the composite: the client camera runs **0 → 2 frames (0 → 67 ms)**
behind the server on test-143757, **0 → 5 (0 → 167 ms)** on NO-NAME — hands
visibly not yet together in one pane when they are in the other, gap widening
through the clip.

**Not a PTP / capture-sync problem.** Frame-0 `timestamp_ns` of the two cameras
agree to **~35 µs**; the session `framesync_report.json` detrended p95 is ~36 µs.
The two cameras capture the same instants; the composite just picks the wrong
decoded frame for the client.

### Root cause

`_StreamCursor` (`video_compose.py:78`) decodes each `.ts` sequentially and
assumes **decoded frame `i` ⟷ CSV row `i`**:

```python
self.timestamps_ns = ts[skip:]          # full CSV
def _advance(self):  ok = cap.read(); self.idx += 1
def sync_to(self, t_ns):
    # advance while timestamps_ns[idx+1] is closer to t_ns than timestamps_ns[idx]
    return self.frame                    # decoded frame `idx`
```

For a sync **client**, that assumption is false — the client's CSV has more
rows than its `.ts` has frames, and worse than the server's.

**Corrected root-cause read (2026-09-07, after reading the code):** the current
`camera_base.py` does **not** arm `sync_enable` / `SyncFrames` on the encoder —
`_start_new_recording` explicitly *"joins the existing phase state rather than
resetting it with SyncFrames/sync_enable"* (`:871`). So there is **no
encoder-side `SyncReady` frame-discard** to gate against; the earlier plan draft
was wrong on that. The frame loss is happening elsewhere. Two candidates, in
order of suspicion:

1. **`_stop_recording` re-muxes every `.ts` through ffmpeg** —
   `_fix_positioning_timestamps` (`camera_base.py:922`, called for every `.ts`
   in `session_files` at stop, `:969`) runs `ffmpeg -i f -map 0 -c copy
   -reset_timestamps 1` and `os.replace`s the file. A `-c copy` remux of an
   mpegts stream with a ~3600 s synthetic PTS offset can legitimately drop a
   partial leading/ trailing GOP, and a **sync client**'s stream — whose frame
   intervals are being jittered by the software-sync rate adjustment — has a
   less regular GOP structure, so it loses more at the cut. This fits every
   observation (deficit exists, worse on the client, `88251069`'s capture-side
   gate didn't touch it, frame-0 overlay 33 ms off = a dropped leading
   non-keyframe).
2. The pyav/`SplittableOutput` mpegts muxer dropping frames at `split_output`
   boundaries or at close.

**This is what the A4 measurement pass settles** — before/after frame counts
around the `_fix_positioning_timestamps` step, on a real rig. Until then, the
consumer-side fixes (B*) stand and the module-side fix (A*) is shaped by A4.

### What the overlay gives us (and its one caveat, now resolved)

Every frame has `overlay_timestamp: True`. In `_frame_precallback` the overlay
text (`ts_label`) and the CSV `timestamp` column are built from the **same**
`timestamp = self._get_frame_timestamp(meta)` — the frame's PTP-derived capture
instant, **not** `datetime.now()` (verified `camera_base.py:1119`, `:1150`).
`ts_label` = `"<module> YYYY-MM-DD HH:MM:SS.mmm+00:00"`, ms-truncated.

So the overlay isn't a *better* clock — it's the same number — but it is
**painted into the frame buffer**, so it is encoded or discarded *with* the
frame. There is no phantom overlay. It survives the exact operation (encoder
frame-drop) that breaks the CSV's row↔frame correspondence.

Therefore: **use the CSV `timestamp_ns` for the value (ns precision, no OCR),
use the overlay only to fix the index.**

## Fixes

### Source side — module, for future recordings

### A4 result (2026-09-07, desk rig `a4_test-174323`, remux OFF)

| camera | CSV rows | `.ts` frames | deficit | `dropped_before` | encoder window |
|---|---|---|---|---|---|
| `camera` (server) | 586 | 586 | **0** | 0 | 19.53 s |
| `ai camera` (client) | 579 | 574 | **+5** | 4 | 19.52 s |

Recorded with `recording.fix_positioning_timestamps = false` — **the `.ts`
remux is ruled out**. The client loses frames in *two* independent places:

1. **~7 at capture** (server got 586, client's CSV only 579; `dropped_before`
   explicitly logged 4 of them) — the ISP/pipeline dropped frames *before*
   `_frame_precallback`. Benign for `_StreamCursor`: no CSV row *and* no `.ts`
   frame, so `frame[i]==row[i]` still holds past the gap.
2. **+5 CSV-vs-`.ts`** — frames that got a CSV row but never reached the
   container. This is the encoder dropping *handed* frames under backpressure
   (H264 encode thread starved — the hailo camera also runs preview inference,
   and its livestream was visibly lagging in the same run). **This is the
   alignment skew**, and picamera2 exposes no per-frame "was this encoded?"
   signal to gate on.

**So A1a is dead** (not the remux) and **A1b is confirmed** (encoder
backpressure drops). The module *cannot* cleanly prevent it, so:
- the **consumer-side repair (B1 overlay / B3 proportional) is the fix**, for
  existing and future footage alike;
- **A3 extended** to probe the container and record `encoded_frames` /
  `deficit_vs_csv` in `_recording.json`, so the deficit is stated at record
  time and B2 is a lookup;
- **load reduction on the hailo camera** (don't run preview inference during a
  recording / more encoder buffers / lower preview fps) is a separate
  mitigation — shrinks the deficit, won't zero it. Follow-up, not the fix.

### A4 method (kept for re-runs)

Where is the deficit introduced?
On the desk rig (`camera` = sync server, `ai camera` = sync client), for a
~60 s recording, count frames at each stage:

| stage | how |
|---|---|
| CSV rows written | `wc -l <stem>_timestamps.csv` (− header), on the module before export |
| `.ts` frames **before** `_fix_positioning_timestamps` | temporarily `return` early from that method, or copy the raw `.ts` aside in the stop path, then `ffprobe -count_frames` / `-count_packets` |
| `.ts` frames **after** the remux | `ffprobe -count_frames` on the final file |
| decodable via OpenCV | loop `cv2.VideoCapture.read()` |

Read: if `CSV rows == pre-remux frames` and `post-remux frames < that` → the
`_fix_positioning_timestamps` remux is the culprit (A1a). If the `.ts` is
already short pre-remux → it's capture/encode-side (A1b). Do this on the server
too — if the *server* also loses frames in the remux but its overlay still
lines up, the loss is trailing-only and cosmetic; the client's is what matters.

**A1a — if the remux drops frames: make `_fix_positioning_timestamps`
lossless or drop it.** Options, cheapest first: (i) it exists to reset the
mpegts positioning PTS, but every downstream tool already ignores container PTS
and trusts the CSV — so it may be safe to **remove entirely**; (ii) if some
consumer does need sane PTS, use `-fflags +genpts -avoid_negative_ts
make_zero -copyts` or `-muxpreload 0 -muxdelay 0` instead of
`-reset_timestamps 1`, and assert the output frame count equals the input's
(log + keep the original on mismatch).

**A1b — if it's capture/encode-side:** narrower, hardware-only investigation
(pyav `SplittableOutput` at `split_output`; hailo inference starving the
encode). Out of scope until A4 shows it's real *and* survives A1a.

**A3. `<stem>_recording.json` provenance sidecar — build regardless of A4.**
Written per segment (at rotation and at stop, *before* any remux), staged for
export:
`{video_file, encoder_started_ns, encoder_stopped_ns, encoder_window_s,
csv_rows_written, fps_target, sync_mode, dropped_before_total,
positioning_timestamps_fixed}`. `csv_rows_written` is the **authoritative,
pre-remux** frame count; `_encoder_start_ns` / `_encoder_stop_ns` are already
captured (`camera_base.py:874`, `:942`). This is the "state the sync level
upfront" anchor: a downstream consumer compares `csv_rows_written` to the
actual `.ts` frame count and knows the exact drift without guessing (feeds
`_StreamCursor` B2 and the sync-provenance block).

### Consumer side — `video_compose.py`, for footage already recorded

**B1. `_StreamCursor` overlay-repair.**
Decode sequentially as now. OCR the burnt-in overlay on decoded frame 0 plus a
handful of checkpoints (every ~N seconds) — a numpy-only digit/character-template
match against the fixed overlay font/size/position (per camera variant; the
`camera` and `hailo_camera` variants render it slightly differently, calibrate
each). Match each OCR'd time to the CSV row with that timestamp → per-checkpoint
offset `k`; interpolate `k(i)` between checkpoints. Then, for decoded frame `i`,
its wall time = `csv_timestamps[i + k(i)]`. CSV precision, overlay's frame
binding, OCR at a few frames only.

**B2. Consume `<stem>_recording.json` when present** → skip OCR, use the
recorded encoded-frame-count / anchor directly.

**B3. Proportional fallback + loud warning (always).**
When neither B1 (OCR failed / no overlay) nor B2 (no sidecar) applies:
`cv2_idx = round(csv_idx * cv2_count / csv_count)` — assumes evenly-spread drops,
bounds error to ±½ the deficit (±33 ms test-143757, ±80 ms NO-NAME). And in
*every* case where `abs(cv2_count - csv_count) > 2`, emit
`camera <name>: N-frame .ts/CSV mismatch, up to M ms drift` into the compose job
result and the log — never silently produce a skewed composite.

## Defect 2 — the sync-provenance block (the "quantified, upfront" requirement)

Every aligned artefact — `_align.json`, the composite, the ethogram, a new
`<bundle>_sync.json` — carries a provenance block, **printed first** on the CLI
and **rendered as a title card / corner caption baked into the ethogram video**
so it travels with the file:

| pair | method | residual / uncertainty | verdict |
|---|---|---|---|
| camera ↔ camera | overlay-ts repair / proportional | framesync detrended p95 (µs) + post-repair `.ts`/CSV drift (frames/ms) | green if < 1 frame |
| audio ↔ video | `STARTED` anchor + rate fit | mic block-fit p95 + PTP offset over the window + **uncalibrated sensor-latency ≈ ±X ms** | **amber** until buzzer-calibrated (`plans/audio-video-sync-residual-validation.md` Phase A) |
| ephys ↔ video | `align_cli` fit | fit residual | **red** — never validated against a real paired Open Ephys + SAVIOUR session |

Plus an **overall rollup** naming the limiting factor.

Honest headline this produces today: *camera↔camera < 1 frame (after B1);
audio↔video a fixed but uncalibrated offset up to ~50 ms, audio lagging;
ephys↔video unvalidated end-to-end.* The manifest reports whatever Phase A
measures — the two efforts are complementary.

## Phasing

1. **`_StreamCursor` B3** (proportional + mismatch warning). Small, low-risk,
   stops silent skewed output on the whole backlog immediately.
   **DONE 2026-09-07** (`feat/compose-frame-remap-b3`): `_StreamCursor` takes a
   `frame_count`, decouples the CSV-row pointer from the decoded-frame pointer,
   and proportionally maps rows→frames when `|deficit| > 1`; `mismatch_ms`
   reports the bounded residual. `compose_session_video(warnings=[...])` appends
   one string per skewed stream; `ComposeJob.warnings` carries it to the
   frontend (`ComposeVideoPanel` renders them). `compose._prestage_skip` capped
   at `_MAX_PRESTAGE_SKIP = 2` (a big deficit isn't all leading rows). Tests:
   `test_video_compose.py` (6). Verified on `NO-NAME-105539` — warns
   `540 rows vs 537 frames (+3), remapped, up to ~50 ms residual`.
1. **A3 `<stem>_recording.json`** — **DONE 2026-09-07** (`feat/camera-recording-
   json-provenance` + follow-ups). Per-segment sidecar, pre-remux, staged for
   export. Extended to probe the container (`encoded_frames`, `deficit_vs_csv`)
   so the encoder-drop gap is stated at record time. Verified on
   `a4_test-174323`: `ai camera` json shows `csv_rows_written 579`,
   `dropped_before_total 4`.
2. **A4 measurement run** — **DONE 2026-09-07** (`a4_test-174323`, remux off).
   Result above: remux ruled out, encoder backpressure drops confirmed.
3. ~~**A1a**~~ — **dropped**, the remux isn't the cause.
4. **`_StreamCursor` B1** (overlay-timestamp repair) — now the primary fix,
   since the module can't prevent the encoder drops. Frame-accurate for the
   backlog and future. **B2** (consume `_recording.json` `encoded_frames` when
   present) rides along.
5. **Sync-provenance block** across `_align.json` / compose / ethogram + the
   ethogram caption.
6. *(mitigation, not a fix)* Reduce hailo-camera load during recording —
   preview inference off / more encoder buffers / lower preview fps — to shrink
   the encoder-drop count.

## Decision to make: default `camera.sync_mode` to `none` (free-run)?

**Proposed 2026-09-07, not yet decided.** The whole client-camera frame-drop
problem above is *caused by* libcamera software framesync: the sync **client**
continuously nudges its frame interval to phase-track the server, which makes
its encode cadence irregular and it drops handed frames under backpressure. The
**server** stays clean (A4: `586 == 586`); only the client skews.

**What framesync buys:** every camera captures the same instant to ~µs after
lock. Needed only for genuine multi-view geometry — stereo depth, 3D pose,
comparing the *exact same frame* across cameras.

**What free-run + PTP gives instead:** every camera at its natural rate, no
nudging → no sync-induced drops (both cameras behave like the A4 server).
`video_compose` already resamples all streams onto a common wall-clock grid by
nearest `timestamp_ns` (its core design). Cross-camera accuracy = **±½ frame**
(~16 ms at 30 fps) + the inter-module PTP offset (µs-scale) — invisible for
behaviour scoring / ethograms, and well inside the audio↔video budget (the
uncalibrated ~tens-of-ms sensor latency dominates that link anyway).

**Recommendation:** default the fleet to `sync_mode: none`; keep server/client
available per-rig for anyone doing stereo/3D. Upside: removes the client
skew at source, simpler pipeline. B1 is still worth building (a loaded camera
can still drop capture frames — `dropped_before` — independent of sync), but
the deficits become smaller and roughly symmetric rather than one-sided and
growing.

**Changes the recommendation:** a real plan for cross-camera 3D/stereo work.
Then framesync earns its keep *there* — per-rig, not fleet-default.

**Blocking checks before flipping the default:** (1) confirm no current analysis
depends on frame-exact cross-camera correspondence; (2) `analyse_framesync.py`'s
verdict logic and the session `framesync_report.json` still make sense for
`sync_mode: none` (they already run for it — "best-effort"); (3) the frontend
FrameSync card copy shouldn't imply sync is required.

## Acceptance

- Re-composing test-143757 / NO-NAME-105539 / a4_test-174323: ai-vs-main-camera
  clap within **±1 frame** (B1) or ±½-deficit (B3), and the job result names
  the mismatch.
- `_recording.json` on a fresh client recording states `deficit_vs_csv` > 0 and
  compose consumes it (B2) rather than probing.
- Every ethogram / composite carries the sync-provenance block; `audio↔video`
  shows amber + a number, `ephys↔video` shows red until it's validated.
- Regression test: a synthetic session where the client CSV has more rows than
  the `.ts` has frames → `_StreamCursor` output frame times track the server's
  to < 1 frame (B1) and the warning fires. **DONE for B3** in
  `test_video_compose.py`.

## Not doing

- Deep picamera2 / libcamera encoder-internals work to add a per-frame
  "was this encoded?" gate — A4 showed the loss is encoder backpressure with no
  clean signal; B1 handles it downstream.
- Trusting `.ts` container PTS for anything — it's synthetic (exactly
  40 ms/frame, ~3600 s start offset). CSV `timestamp_ns` is the only real
  per-frame clock.
- Re-encoding / re-muxing the recorded `.ts` files to "repair" them — alignment
  is a read-time concern; the recorded files stay untouched.
