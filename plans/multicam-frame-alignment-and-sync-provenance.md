# Multi-camera frame indexing + quantified sync for the aligned ethogram

- **Status:** proposed
- **Created:** 2026-09-07
- **Owner:** ascottg
- **CLAUDE.md ref:** expands the "In-flight" bullet *"`camera_base.py` `_encoder_active`
  gate…"* (the `_StreamCursor` `frame[i]==row[i]` half) and the Post-Process /
  ethogram bullet; sibling to `plans/audio-video-sync-residual-validation.md`.

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

For a sync **client**, that assumption is false. On the first segment the
encoder is armed with `sync_enable` / `SyncFrames` and **discards every frame
until `SyncReady` fires** (CLAUDE.md → Camera framesync). But
`_frame_precallback` (`camera_base.py:1195`) writes a CSV row for each of those
discarded frames — its only gate is `_encoder_active` (True the moment
`start_encoder()` returns, `camera_base.py:875`), which does **not** know about
the `SyncReady` discard window. So the client's CSV gets rows for frames that
never enter the `.ts`, and `csv_row[i]` runs ahead of `.ts_frame[i]` by the
discard count (≈1 frame at the start in NO-NAME: frame-0 overlay `43.720` vs CSV
row 0 `43.687`), plus whatever accumulates later.

`88251069` (`fix(camera): lock per-frame CSV rows to the encoder-active window`,
on `main`/`staging`) closed the *pre-`start_encoder` / post-`stop_encoder`*
window. It did **not** close the `SyncReady` acquisition window, and it only
*logs* any residual container-side surplus.

The server camera has an exact match because it isn't discarding anything.

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

**A1. Gate the CSV row on `SyncReady`, mirroring the encoder.**
In `_frame_precallback`, for a sync **client**, do not append a row until
`SyncReady` has been seen for the current segment (track a per-segment
`self._sync_ready` bool, set on the first frame whose `meta.get("SyncReady")` is
truthy; `SyncTimer >= 0` is an equivalent signal). Server / `sync_mode == "none"`
keep today's behaviour. This removes the acquisition-discard phantom rows — the
constant, dominant part of the skew.

**A2. `sync_discarded` CSV column (belt-and-braces).**
If a row *is* written while the encoder's discard gate is closed (edge cases,
non-first segments that still re-arm), stamp it `1` so downstream can filter
without guessing. Zero cost, additive column.

**A3. `<stem>_recording.json` sidecar at `stop_recording`.**
`{encoder_started_ns, encoder_stopped_ns, encoded_frame_count, csv_row_count,
drift_frames, sync_mode, sync_ready_frame_index}`. `_encoder_start_ns` is
already captured (`camera_base.py:874`); `encoded_frame_count` from probing the
just-closed `.ts` (`ffprobe -count_packets`) or the encoder object. This makes
**any residual mismatch detected and quantified at record time** — the core of
the "state the sync level upfront" requirement — and gives the compose consumer
an authoritative anchor instead of an inference.

**A4. Measurement pass (not a code change — do before deciding anything deeper).**
Is the *mid-recording* residual surplus (the 0→5 accumulation, not the frame-0
offset) real encoder drops or a container/probe artifact? Record 60 s on a real
rig, client + server, sync on and off; count **CSV rows vs `ffprobe
-count_packets` vs `-count_frames` vs `cv2.VideoCapture` reads vs the encoder's
own count**. If they disagree only at the container/probe layer → the CSV is
fine and the residual is a compose-side probe choice. If the encoder genuinely
dropped frames post-`SyncReady` (hailo inference starving the encode is the
suspect) → that needs picamera2-internal work and is out of scope until it's
shown to be real *and* to matter after A1.

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
2. **Module A1 + A2 + A3** (`SyncReady` CSV gate, `sync_discarded` column,
   `_recording.json`). Future data; needs on-hardware validation (client + server
   rig).
3. **`_StreamCursor` B1 + B2** (overlay repair + sidecar consume). Backlog
   frame-accuracy.
4. **Sync-provenance block** across `_align.json` / compose / ethogram + the
   ethogram caption.
5. **A4 measurement run**; decide whether any deeper encoder work is warranted.

## Acceptance

- A fresh client+server recording: server `.ts` frames == CSV rows exactly;
  client within ±0 after A1, or the residual quantified in `_recording.json`.
- Re-composing test-143757 / NO-NAME-105539: ai-vs-main-camera clap within
  **±1 frame** (B1) or ±½-deficit (B3), and the job result names the mismatch.
- Every ethogram / composite carries the sync-provenance block; `audio↔video`
  shows amber + a number, `ephys↔video` shows red until it's validated.
- Regression test: a synthetic session where the client CSV has more rows than
  the `.ts` has frames → `_StreamCursor` output frame times track the server's
  to < 1 frame (B1) and the warning fires.

## Not doing

- Deep picamera2 / libcamera encoder-internals work to get a per-frame "was this
  encoded?" signal — only if A4 shows the post-`SyncReady` residual is real
  dropped frames *and* still matters after A1.
- Trusting `.ts` container PTS for anything — it's synthetic (exactly
  40 ms/frame, ~3600 s start offset). CSV `timestamp_ns` is the only real
  per-frame clock.
- Re-encoding / re-muxing the recorded `.ts` files to "repair" them — alignment
  is a read-time concern; the recorded files stay untouched.
