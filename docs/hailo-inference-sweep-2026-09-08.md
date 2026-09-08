# Hailo preview-inference load vs sync-client frame drops — sweep, 2026-09-08

Automated sweep with `tools/framesync_sweep.py` to characterise how the
`hailo_camera` preview-inference thread affects the recorded video on the
libcamera sync **client** camera. Motivated by the "ai camera lags the
frameserver by a few frames" observation in Post-Process compose and on the
livestream (see `plans/multicam-frame-alignment-and-sync-provenance.md` and
`plans/hailo-inference-threading.md`).

Raw data: `docs/hailo-inference-sweep-2026-09-08.csv` (one row per run).

## Setup

| | |
|---|---|
| Controller | `10.0.0.1`, `v0.9-328`, staging |
| Sync **server** (frameserver) | `camera_d074` — Pi Camera Module 3, `sync_mode: server` |
| Sync **client** | `hailo_camera_3606` ("ai camera") — `sync_mode: client`, model `yolov8s`, `threshold 0.2`, `max_labels 10` |
| Both cameras | 1920×1080, 30 fps, sensor mode 2, `framesync_enabled: true` |
| Also recording | `microphone_4703`, `ttl_0fdf` (`--target all`; constant load across runs) |
| Per run | 60 s recording; config PATCHed via `GET`/`PATCH /api/v1/modules/<id>/config`; PTP gated (`< 50 µs`) before start |
| Conditions | `off` = `hailo.infer_enabled: false`; `n1`/`n2`/`n8` = `infer_enabled: true`, `infer_every_n` = 1 / 2 / 8 |
| Repeats | 5 planned per condition, inference conditions **shuffled** within each repeat (guards against thermal drift) |
| Metrics | from each session's controller-generated `framesync_report.json` + `<stem>_recording.json` + `ffprobe -count_packets` on the `.ts` |

`client deficit` = CSV rows written − `.ts` frames encoded (frames the module
handed the encoder that never reached the container — this is the compose
lag). `dropped_before` = frames the pipeline dropped before the CSV
(estimated from inter-frame gaps). `rate_cv` = coefficient of variation of
the inter-frame interval (capture-cadence jitter).

**Caveat:** two laptop-side network outages during the ~2 h run lost 5 of 20
runs (`off_r1`, `n1_r1`, `n8_r1`, `off_r4`, `n8_r4`, `n1_r4`) and the
harness's end-of-run config restore failed — restored manually from
`config_snapshot.json`. The controller/rig were unaffected (`saviour`
uptime unbroken). Final n = 3–5 per condition.

## Results

| condition | n | client deficit | client `dropped_before` | `rate_cv` | dropped-frame % | detrended p95 |
|---|---:|---|---|---|---|---|
| inference **off** | 3 | `1, 0, 0` (mean 0.3) | `0, 0, 0` | ~0.00014 | 0 | 31–36 µs |
| infer **every 8th** | 3 | `0, 0, 0` | `0, 0, 0` | ~0.00020 | 0 | 35–38 µs |
| infer **every 2nd** | 5 | `1, 0, 0, 0, 0` (mean 0.2) | `0, 0, 0, 0, 0` | ~0.00027 | 0 | 35–41 µs |
| infer **every frame** (`n1`) | 3 | `1, 6, 2` (mean 3.0 ± 2.2) | `0, 8, 11` (mean 6.3 ± 4.6) | `0.0003, 0.065, 0.082` | `0, 0.42%, 0.58%` | 36–38 µs |

Sync **server** (`camera_d074`): `deficit = 0`, `dropped_before = 0`, `rate_cv
≈ 0.0002` in **every single run**. The effect is entirely on the client.

`framesync_report.json` verdict for the two degraded `n1` runs:
`"ai camera: 0.42% dropped frames | ai camera: unstable capture rate (gap CV
0.064)"` (and the 0.58% / 0.082 run).

## Conclusions

1. **`infer_every_n = 1` degrades the sync client intermittently.** 2 of 3
   completed runs here (1 of 1 in the earlier smoke) — call it ~2/3–3/4. When
   it fires: **8–11 capture frames dropped + 2–6 encoder frames missing +
   capture-cadence CV jumps ~250–300×**. This is the few-frame compose lag.
   When it doesn't fire, `n1` looks identical to `off`.

2. **`infer_every_n ≥ 2` is clean and indistinguishable from inference-off.**
   `n2`, `n8`, and `off` all show 0 dropped frames and `rate_cv` at the noise
   floor across every run. The stray `deficit = 1` (once each in off / n2 /
   n1) is a ~20 %-of-runs trailing-frame artefact, unrelated to inference.

3. **It's a throughput problem, not a timing one.** `detrended p95` sits at
   34–41 µs across *every* condition — PTP sync quality is unaffected. The
   client's frames are captured at the right instants; some just never make
   it into the recorded stream.

4. **Mechanism** (from `plans/hailo-inference-threading.md`): `hailo_camera`
   runs `detector.detect()` **synchronously on the libcamera capture-callback
   thread**, which also feeds the H264 encoder. At `infer_every_n = 1` the
   mean inference time sits right at the frame budget, so the tail of the
   distribution (a slow inference coinciding with a GC pause / thermal blip /
   export I/O from the prior run) tips individual frames over into a drop
   burst. At `n2` there is enough slack to absorb those.

## Actions

- **Immediate:** the deployed ai camera's `infer_every_n` was found set to
  `1`. The file default is already `2`. Set deployed rigs to `2` — zero
  measured cost, removes the intermittent drop risk. (Sweep left the rig
  restored to its original `1`.)
- **Structural fix:** move preview inference to a worker thread so the
  capture/encode thread's per-frame cost is constant regardless of inference
  time/variance — `plans/hailo-inference-threading.md`. This sweep is the
  acceptance test (re-run and confirm `n1` matches `off`).
- **Not done:** a 60 fps block (would test whether `n2`'s headroom shrinks);
  more `n1` repeats to pin the failure rate; `sync_mode: none` arm.
- **Harness:** needs resume-from-`results.csv` and a retrying / snapshot-based
  restore for runs this long (two network drops today).
