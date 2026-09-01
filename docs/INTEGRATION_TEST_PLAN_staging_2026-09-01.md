# SAVIOUR — Integration Test Plan: `staging` merge, 2026-09-01

**Scope:** validates the nine feature/fix branches merged into `staging` on 2026-09-01
(`origin/staging` @ `8416d493`). Use this **in addition to**
`docs/INTEGRATION_TEST_PLAN.md` (the general new-system checklist) — this one only
covers what changed.

**None of this has run on hardware.** Backend suite is green (942 passed, same one
pre-existing thread-exception warning as `main`); the frontend has **not** been built.

Mark each row ✓ / ✗ / N/A with notes. A row that needs a specific rig
(`habitat_camera`, AudioMoth, Hailo) is N/A on a rig without it.

---

## 0 — Prerequisites (do first, in order)

| # | Step | Expected | ✓ |
|---|------|----------|---|
| 0.1 | Deploy the `staging` ZIP to the controller (`update_saviour` / staging deploy). | Service restarts, version string updates. | |
| 0.2 | **Rebuild the frontend** for the rig's variant: `cd src/controller/frontend && VITE_VARIANT=<variant> npm run build`. Or `sudo saviour-config --regenerate-service` + reprovision. | `dist/` rebuilds with no error. Every frontend change below ships in this build — an un-rebuilt `dist/` shows none of it. | |
| 0.3 | If `dist/` throws `EACCES`/`unlink`: `sudo chown -R $(whoami) src/controller/frontend/dist` then rebuild. | Build completes. | |
| 0.4 | Deploy `staging` to **every module** (`update_saviour` broadcast) and let them re-register. | All modules reconnect, heartbeats resume. | |
| 0.5 | `run_mend` (or `sudo bash mend.sh`) on controller + modules; reboot any that report `reboot_required`. | Picks up `journald.conf` caps + `SyslogIdentifier`. Confirm `journalctl --list-boots` shows >1 boot afterwards on each. | |
| 0.6 | On any `habitat_camera` module that will use occupancy: `env/bin/pip install onnxruntime`. | Installs. Without it the occupancy trigger just stays disabled (see 6.x). | |
| 0.7 | Hard-refresh the browser (Ctrl-Shift-R) after 0.2. | New JS loaded (check a new string, e.g. the Storage "Recording data rate" card). | |

---

## 1 — systemd logging hygiene (`refactor/systemd-logging-hygiene`)

| # | Check | Method | Expected | ✓ |
|---|-------|--------|----------|---|
| 1.1 | Identifier tagging | `journalctl -t saviour -n 20` on controller and a module | Returns SAVIOUR lines (service now sets `SyslogIdentifier=saviour`). | |
| 1.2 | Journald size cap active | `journalctl -u systemd-journald --grep SystemMaxUse` or `cat /etc/systemd/journald.conf` | `SystemMaxUse=500M`, `SystemKeepFree=1G` present. | |
| 1.3 | Rate-limit lifted | `grep LogRateLimitIntervalSec /etc/systemd/system/saviour.service` (or wherever the unit lives) | `LogRateLimitIntervalSec=0` — an incident burst is no longer truncated at 1000 msg/30 s. | |
| 1.4 | TTL edge noise gone | Drive a TTL pulse train into a `ttl` module; `journalctl -u saviour -f` | No per-edge INFO line per pulse (moved to DEBUG). Events still land in the TTL CSV. | |
| 1.5 | Per-frame camera errors are rate-limited | Not easily forced; if a camera logs a frame-timestamp error, watch the journal | First occurrence logs, then a periodic summary with a suppressed count — not one line per frame. | |
| 1.6 | Config dumps redacted | `sudo saviour-config` push export creds, or trigger a `set_config`; `journalctl -u saviour` | No `share_password` / full credential string at INFO (verb only at INFO, full at DEBUG). | |
| 1.7 | New "recording alive" heartbeat — camera | Start a recording on a camera module; `journalctl -u saviour -f` for ~2 min | A periodic `Recording alive: N fps …` line (default every 60 s; `recording.camera_throughput_log_secs`, 0 disables). | |
| 1.8 | New "recording alive" heartbeat — microphone | Start a recording on a mic module; watch its journal | Per-AudioMoth `Recording alive [audiomoth <serial>]: N blocks …` line (`recording.mic_throughput_log_secs`, default 60). | |
| 1.9 | Per-segment rotation line — camera | Let a camera recording roll past one segment boundary | A line with the closed segment's frame count, estimated drops, file size. | |
| 1.10 | Controller status heartbeat | Idle controller, `journalctl -u saviour -f` for ~6 min | One `_log_controller_status()` INFO (~5 min cadence): module status tally + load avg + root-disk %. Replaces the old dead "every 5 cycles / pass" block. | |
| 1.11 | PTP start-gate outcome logged | Start a session; check controller journal | `_check_ptp_sync` logs its own PASS/FAIL with the offsets — "why did/didn't recording start" answerable from the journal alone. | |
| 1.12 | Exceptions carry tracebacks | Force any handled data-path error (e.g. pull a module mid-export) | The `~15` converted sites now log a full stack, not just `: {e}`. | |
| 1.13 | Regression — nothing over-suppressed | Normal record/stop/export cycle | Session lifecycle, export success/fail, PTP degraded warnings all still visible at INFO/WARNING. | |

---

## 2 — Pi throttle / under-voltage reporting (`feat/throttle-health-check`)

| # | Check | Method | Expected | ✓ |
|---|-------|--------|----------|---|
| 2.1 | Field present end to end | Open **System** page | Every module row + the controller row render (no crash from the new `throttled` field). | |
| 2.2 | Healthy state | System page, hover the Temp cell of a well-powered module | No ⚡ marker (or a neutral one); tooltip shows no active flags. | |
| 2.3 | Under-voltage surfaces | Briefly power a module from a marginal supply / long thin cable, or a PoE HAT with `PSU_MAX_CURRENT` unset under load | Temp cell shows a **red ⚡**; tooltip names the flag (`under_voltage` / `throttled` / `freq_capped` / `soft_temp_limit`). | |
| 2.4 | Since-boot vs now | After 2.3, restore good power | ⚡ goes **amber** (since-boot only), doesn't stay red. | |
| 2.5 | Transition logged once | `journalctl -u saviour` on that module across 2.3→2.4 | One WARNING when flags go active, one INFO `throttle flags cleared` — **not** every heartbeat. | |
| 2.6 | Controller's own throttle | System page controller row | Same ⚡ behaviour, fed by `get_controller_health`. | |
| 2.7 | Disappear-on-brownout breadcrumb | Let a module drop offline right after an under-voltage event | Controller journal `_enter_suspicion` line records the last-seen throttle flags for that module. | |
| 2.8 | Old module compatibility | If any module still runs pre-`staging` | It reports `throttled=None`; System page shows no ⚡ for it, no error. | |

---

## 3 — Recording data-rate estimate (`feat/module-data-rate-estimate`)

| # | Check | Method | Expected | ✓ |
|---|-------|--------|----------|---|
| 3.1 | Storage page card renders | Open **Storage** | New "Recording data rate" card: MB/min · GB/hour · "share holds ~N …" + a trend chart area. | |
| 3.2 | Local-disks table columns | Storage page, per-module disk table | New **Rate** and **Buffer** columns + a recording dot; non-recording modules show a dash / nominal. | |
| 3.3 | Estimate before a session | **New session** form (and loom's `LoomRecording` page): pick a target + duration | Line like `~X MB/min (Y GB/hour). Share holds this session ~A … Modules buffer ~B locally if export stalls (shortest: <module>).` | |
| 3.4 | Two metrics, shown separately | Same line | Share-runway and local-buffer are **distinct** numbers (not `min()` of the two). | |
| 3.5 | Over-duration warning | Set a timed run longer than the share runway | Line flips to warning style — **but session creation is NOT blocked** (soft warning by design). | |
| 3.6 | Measured cross-check appears | Start a real recording; wait ≥ 2 health ticks; Storage page | Rate cell shows `14.8 MB/min (est 15.2)` form — measured value present, estimate in parens. | |
| 3.7 | Measured is monotonic across export | Keep recording through a segment export/delete | Measured cumulative total does not drop when export moves/deletes a file (high-water-mark per file). | |
| 3.8 | Health field flows | System/health inspection while recording | `rec_bytes_per_s` populated only while recording, `None` otherwise. | |
| 3.9 | History + CSV | Let a recording run ≥ 10 min; Storage card range buttons; "Download CSV" | Trend chart fills; `/api/data_rate_history.csv` downloads fleet MB/min + recording-module count + per-module JSON. | |
| 3.10 | habitat_camera caveat | Estimate for a `habitat_camera` target | `est_note` flags that motion-gating isn't modelled (estimate is a ceiling). | |
| 3.11 | Mic channel count | Estimate for a microphone with N AudioMoths configured | Rate scales with N (`len(audiomoth_labels)`); falls back to 1 with a note if unset. | |

---

## 4 — Audio clipping indicator (`feat/audio-clip-indicator`)

| # | Check | Method | Expected | ✓ |
|---|-------|--------|----------|---|
| 4.1 | Config field | **Microphone** config card | "Clipping warn threshold (%)" field, default `0.5`. | |
| 4.2 | Monitor badge — clean | Open the mic monitoring stream at a sane `audiomoth.gain` | No `CLIP` badge on the spectrogram cell header. | |
| 4.3 | Monitor badge — clipping | Raise `audiomoth.gain` high / present a loud source; watch the monitor | Red `CLIP N%` badge appears in the cell header once the rolling ~3 s clipped fraction exceeds the threshold. | |
| 4.4 | Not tripped by a transient | One brief loud tap | A single-block spike does **not** latch the badge (rolling window). | |
| 4.5 | System page badge | System page, mic row, while 4.3 holds | `CLIP N%` badge next to the mic name (≥ 0.5 %). | |
| 4.6 | Health field | health inspection | `audio_clip_pct` populated for the mic, `None` for non-mic modules. | |
| 4.7 | Per-segment sidecar | Record a clipping segment; open its `*_timestamps.txt` on the share | Contains `SEGMENT_CLIPPED_SAMPLES`, `SEGMENT_TOTAL_SAMPLES`, `SEGMENT_CLIPPED_PCT`, `SEGMENT_PEAK_DBFS`. | |
| 4.8 | Per-segment + session log | Journal during/after that recording | Per-segment WARNING when a segment exceeds the threshold; a session-total line at stop. | |
| 4.9 | Regression — spectrogram | Normal monitor use | Spectrogram render, colormap, peak-hold all unaffected. | |

---

## 5 — Camera exposure-clipping indicator (`feat/camera-saturation-indicator`)

| # | Check | Method | Expected | ✓ |
|---|-------|--------|----------|---|
| 5.1 | Config fields | **Camera** config card (any camera variant) | `exposure_warn_pct` (5.0), `exposure_overlay` (on), and the raw `exposure_clip_high/low` + `exposure_check_interval_s` are in the six camera config JSONs. | |
| 5.2 | Overlay — normal exposure | Livestream a normally-lit scene | No `OVEREXPOSED` / `UNDEREXPOSED` badge (top-left). | |
| 5.3 | Overlay — blown white | Point the camera at a bright light / white card filling frame | Red `OVEREXPOSED N%` top-left once the strided-subsample clipped fraction stays over `exposure_warn_pct` for ~3 samples. | |
| 5.4 | Overlay — crushed black | Cap the lens / dark scene | Amber `UNDEREXPOSED N%`. | |
| 5.5 | Overlay position | While 5.3/5.4 show | Top-left only — does not collide with the timestamp (top-centre), fps (top-right), or habitat motion overlay (bottom-left). | |
| 5.6 | Transition logging | Journal across 5.2→5.3→5.2 | WARNING on entering sustained-bad, INFO on recovery — not per frame. | |
| 5.7 | Overlay toggle | Set `exposure_overlay` false, save | Badge stops rendering; health field still updates. | |
| 5.8 | System page badge | System page, camera row, while 5.3 holds | `EXPOSURE N%` badge by the camera name (≥ 5 %). | |
| 5.9 | Health field | health inspection | `frame_clip_pct` = worse of over/under for cameras, `None` for non-cameras. | |
| 5.10 | Cost | Watch preview fps / CPU on a busy camera | Sampling is ~1/64 strided and interval-throttled — no visible fps drop. | |

---

## 6 — Habitat occupancy trigger (`feat/habitat-occupancy-trigger`) — `habitat_camera` only

| # | Check | Method | Expected | ✓ |
|---|-------|--------|----------|---|
| 6.1 | Default off | Fresh `habitat_camera` on `staging`, no config change | `occupancy.enabled=false`; module behaves exactly as motion-only. Journal notes occupancy disabled. | |
| 6.2 | Graceful fallback — no onnxruntime | Set `occupancy.enabled=true` **without** step 0.6 | Module logs "no usable model — occupancy trigger disabled (motion-only)" and keeps running. **No crash.** | |
| 6.3 | Graceful fallback — missing model file | `enabled=true`, `model_path` pointing nowhere | Same as 6.2. | |
| 6.4 | Model loads | 0.6 done, `enabled=true`, default `model_path` (`models/rats_yolov8n_416.onnx`) | Journal: model loaded, input size/output shape logged as "detector". | |
| 6.5 | Config tab | Camera config card → **Motion** tab (habitat only) | "Occupancy trigger" subsection: enable, threshold (0.35), interval_s (2.0), confirm_samples (2), clear_secs (30), model_path. | |
| 6.6 | Livestream verdict readout | Open the habitat_camera livestream | Overlay shows `[subject NN]` / `RAT NN` (present) or `empty NN` (score) — updates every `interval_s`. | |
| 6.7 | Occupancy opens a clip | Place a subject (or a convincing stand-in) in frame, still | After `confirm_samples` consecutive positive scores, recording starts even with **no motion** — overlay attribution shows `(rat)` or `(motion+rat)`. | |
| 6.8 | OR-fusion with motion | Subject moves, then goes still in frame | Recording started by motion **stays open** via occupancy while the subject is still (doesn't close at `activity_min_duration_s`). | |
| 6.9 | Clear hangover | Subject leaves frame | Occupancy clears after `clear_secs` of sub-threshold; then the motion path's `inactivity_min_duration_s` (~300 s) applies. Effective tail ≈ `clear_secs` + `inactivity_min_duration_s`. | |
| 6.10 | CSV columns | Pull a per-frame CSV + the motion-diagnostic CSV for a triggered clip | Both carry `occupancy_score`, `occupancy_present` (plus existing `motion_score`, `motion_state`). | |
| 6.11 | `reset_motion_trigger` | Send the command mid-clip | Resets the occupancy detector too (not just motion). | |
| 6.12 | CPU headroom | `top` on the Pi 5 while recording + scoring at 2 s cadence | Occupancy thread is a few % of one core (est. ~150–250 ms/frame). No frame drops on the capture path. | |
| 6.13 | Clean stop | `stop()` / module shutdown while enabled | Occupancy thread joins; no hang, no stray thread warning. | |
| 6.14 | Threshold tuning note | — | `0.35` is **unvalidated** against real habitat footage. Record observed `[subject NN]` values for empty vs occupied frames so the threshold can be set per-camera later. | |

---

## 7 — Loom dashboard timer during a fault (`fix/loom-timer-visible-during-fault`) — loom rig only

| # | Check | Method | Expected | ✓ |
|---|-------|--------|----------|---|
| 7.1 | Timer visible normally | Loom dashboard during a healthy recording | Countdown/elapsed timer shows as before. | |
| 7.2 | Timer stays during fault | Pull a module (or induce a fault) mid-recording | Timer **stays on screen** (previous bug: it vanished when a still-recording session went to `error`). | |
| 7.3 | Fault styling | While 7.2 | Timer shows the fault style + the session's `error_message` line. | |
| 7.4 | Near-end styling | Let a timed run approach its end | `--near-end` style still applies (not clobbered by the fault-state change). | |
| 7.5 | Recovery | Restore the module | Timer returns to normal style once the session leaves `error`. | |

---

## 8 — First-run controller setup modal (`feat/first-run-setup`)

| # | Check | Method | Expected | ✓ |
|---|-------|--------|----------|---|
| 8.1 | Sentinel written on (re)config | `sudo saviour-config` → set this device to controller (or change its controller type) → finish | `/var/lib/saviour/first_run` exists afterwards (`reason=`, `type=`, `created=`). | |
| 8.2 | Modal appears | Load the web UI after 8.1 | "First-time controller setup" modal opens on connect (no navigation needed). | |
| 8.3 | Pre-fill | If `controller.name`/`location` were already set | Fields pre-filled with the current values. | |
| 8.4 | Variant match shown | Modal body | "Experiment interface" (built `VITE_VARIANT`) shown; "Provisioned type" (from `/etc/saviour/config`) shown. | |
| 8.5 | Variant **mismatch** warning | Build the frontend for the wrong variant vs the provisioned type (or inspect on a genuine mismatch) | Red warning telling the operator to re-run `saviour-config`; modal can't change it. | |
| 8.6 | Login gate | Not logged in | Fields visible; **Save disabled**; "Log in … to complete first-time setup." note. | |
| 8.7 | Confirmation checkbox required | Logged in, name filled, checkbox unticked | Save stays disabled until "This controller is set up for the `<variant>` experiment" is ticked. | |
| 8.8 | Empty name rejected | Logged in, clear the name, tick box, Save | `first_run_error` — "A controller name is required"; sentinel not removed. | |
| 8.9 | Save path | Fill name + location, tick box, Save | `controller.name`/`location` persisted (check Settings → Controller → Basic); `/var/lib/saviour/first_run` **removed**; modal closes. | |
| 8.10 | Multi-client dismiss | Two browsers open on 8.2; complete in one | The other browser's modal dismisses (broadcast `first_run_state`). | |
| 8.11 | "Later" snooze | Click **Later** | Modal closes for this browser session (`sessionStorage`); reappears in a new tab / after a hard refresh; sentinel stays until real completion. | |
| 8.12 | No false positive on upgrade | A controller that was already provisioned before `staging`, just ZIP-updated | **No** modal (no sentinel written by a plain `update_saviour`). | |
| 8.13 | Steady-state cost | After 8.9, normal use | Every websocket connect: `_first_run_state()` returns `{"needed": false}` with no config fetch. | |
| 8.14 | Variant config change | — | `habitat` / `acoustic_startle` controller configs no longer force `controller.name` to the slug; a fresh one is `""`. `get_controller_info` / Teams alerts fall back to hostname. Confirm Teams test alert still names the controller sensibly. | |

---

## 9 — Telemetry design doc (`docs/telemetry-design`)

Documentation only — `docs/TELEMETRY_DESIGN.md`. **No runtime change, nothing to test.**
Confirm the file is present on `staging` and renders. Implementation is a separate future piece.

---

## 10 — Cross-cutting regression (the combined merge)

| # | Check | Method | Expected | ✓ |
|---|-------|--------|----------|---|
| 10.1 | Health snapshot end to end | Record on a camera **and** a mic simultaneously; inspect a module health payload | All four new fields present and correctly typed: `throttled` (int/None), `rec_bytes_per_s`, `audio_clip_pct`, `frame_clip_pct`. Old fields unchanged. | |
| 10.2 | Health metadata CSV | Pull a `*_health_metadata_*.csv` from the share | Header includes the new columns; rows populate; no malformed rows. | |
| 10.3 | System page under load | 4+ modules, mixed types, one recording, one under-voltage, one clipping | Table renders every row; ⚡ / `CLIP` / `EXPOSURE` badges coexist without layout break. | |
| 10.4 | CameraConfigCard tabs | Open a camera config card (plain `camera`, then `habitat_camera`) | Image tab has exposure fields; Motion tab (habitat) has the occupancy subsection; existing tabs unaffected; Save writes all sections. | |
| 10.5 | Full record → stop → export | One clean cycle per module type on the rig | Files land on the share; session goes ENDED; no new WARNING/ERROR from the merged logging changes; export counts correct. | |
| 10.6 | Scheduled + FrameSync start | Start a scheduled multi-camera session | PTP start-gate + FrameSync unaffected; new `_check_ptp_sync` PASS/FAIL line present. | |
| 10.7 | Frontend build clean | `npm run build` output from 0.2 | No errors, no unresolved imports (new `FirstRunModal`, Storage card, occupancy fields all resolve). | |
| 10.8 | Backend unit suite on the deployed tree | `source env/bin/activate && pytest` on the controller | Green (942 on the dev box; expect the same minus any env-specific skips). | |
| 10.9 | Old-module mixed fleet | If any module can't be updated to `staging` | It still registers, heartbeats, records; its missing new health fields read as `None` everywhere; no controller-side error. | |

---

## Sign-off

| Area | Result | Tester | Date |
|------|--------|--------|------|
| 1 logging | | | |
| 2 throttle | | | |
| 3 data rate | | | |
| 4 audio clip | | | |
| 5 camera exposure | | | |
| 6 occupancy | | | |
| 7 loom timer | | | |
| 8 first-run | | | |
| 10 regression | | | |

**Ready to promote `staging` → `main` when sections 1–8 + 10 are ✓ (6/7 N/A where the rig lacks the hardware) and CI is green.**
