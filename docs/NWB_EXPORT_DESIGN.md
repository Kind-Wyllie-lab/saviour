# NWB Export — Design Scoping

**Status: proposal, not implemented.** Nothing described here exists yet. `src/controller/database.py`
has a dead `import pynwb` (optional, `NWB_AVAILABLE` flag, never actually used anywhere — see
CLAUDE.md's dead-code TODO) that predates this document and should be treated as unrelated leftover,
not partial progress. This doc exists so that when someone picks this up, the mapping work below
doesn't have to be re-derived from scratch.

## Why NWB fits SAVIOUR

NWB (Neurodata Without Borders) is the standard format for exactly SAVIOUR's problem shape:
multi-modal experimental data (video, audio, discrete events, behavioral tracking) sharing one
precise common clock, plus subject/session metadata, in a single container that tools like DANDI,
NWBWidgets, and most ephys/behavior analysis pipelines already understand. SAVIOUR's PTP-disciplined
timing is the hard part most labs bolting NWB onto their own recording pipeline don't have — every
stream below already carries a wall-clock-referenced timestamp precise enough to be meaningful in an
NWB file's shared timeline. The conversion work is mostly *packaging*, not *deriving new timing*.

## Recommended shape: standalone post-hoc export tool, not a pipeline feature

Build this as `tools/export_nwb.py`, alongside `tools/analyse_framesync.py` and
`tools/make_aligned_video.py` — a script a researcher runs against an already-exported session
directory on the NAS share, producing one `.nwb` file. **Do not wire this into the live
recording/export pipeline** (`src/modules/recording.py`, `src/controller/recording.py`,
`src/modules/export.py`): it only needs read access to files that already exist after a session
completes and exports, has no reason to run on a Pi, and keeping it fully offline means a bug in NWB
conversion can never affect a live recording. This mirrors the existing `env2` convention — `pynwb`
pulls in `h5py`/`hdmf`/`pandas`, which is exactly the kind of dependency weight CLAUDE.md already
keeps off module/controller installs. Add it as an extra (`pip install -e ".[nwb]"` or a dedicated
`env3`), never to the core `pyproject.toml` dependencies modules/controllers install.

**One NWBFile per session**, not per module. NWB's whole value proposition is combining multi-modal
streams from one experimental session against one shared clock — that maps directly onto a SAVIOUR
session (one `session_metadata.json`, N module subdirectories, one shared PTP timeline), not onto any
single module's output.

## What SAVIOUR actually produces (input inventory)

All paths relative to `{share_root}/{session_name}/`. Confirmed against the current code as of this
writing — see `src/modules/camera_base.py`, `src/modules/examples/microphone/microphone_module.py`,
`src/modules/examples/ttl/ttl_module.py`, `src/modules/examples/apa_arduino/apa_arduino_module.py`,
`src/shared/health.py`, `src/controller/web.py`.

| Stream | Path pattern | Format | Timestamp basis |
|---|---|---|---|
| Video | `{date}/{camera_module}/*.ts` | MPEG-TS, raw H.264 elementary stream (`recording.recording_filetype`, default `ts`) | Per-frame CSV sidecar, see below |
| Video timestamps | `{date}/{camera_module}/*_timestamps.csv` | CSV: `frame_id, timestamp_ns, timestamp_utc, wall_mono_offset_s, delta_ms, dropped_before, sync_lag_us, exposure_time_us, analogue_gain, colour_gain_r, colour_gain_b` (+ `det_cx,det_cy,in_zone` for apa_camera; `cx,cy,zone_state,event` for loom_camera) | `timestamp_ns` = PTP-hardware-derived (`SensorTimestamp`), epoch ns UTC |
| Audio | `{date}/{mic_module}/*.flac` | FLAC, 16-bit PCM mono, one file per AudioMoth per segment (`recording.recording_filetype`, default `flac`) | Per-block CSV sidecar, see below |
| Audio timestamps | `{date}/{mic_module}/*_timestamps.txt` | Plain text: optional `START_AT <epoch>`, `STARTED <epoch>`, optional `STARTUP_LATENCY_MS <ms>`, then one `time.time()` epoch-seconds float per recorded block (~683ms blocks at default settings) | `time.time()` epoch seconds UTC, phc2sys-disciplined system clock (not hardware-latched like camera) |
| TTL events | `{date}/{ttl_module}/*.csv` | CSV: `Timestamp_nanoseconds,pin_number,pin_mode,pin_state,pin_description` — one row per edge transition | `time.time_ns()` epoch ns UTC, phc2sys-disciplined |
| APA shock events | `{date}/{apa_arduino_module}/*_shock_events.csv` | CSV: `Timestamp_nanoseconds, event, rotation speed (rpm)` — `event` ∈ `SENDING_SHOCK/STOPPING_SHOCK/SHOCK_DELIVERY/SHOCK_STOP_DELIVERY` | epoch ns UTC |
| Health/diagnostics | `{date}/{module}/*_health_metadata_*.csv` | CSV, `ModuleHealthSnapshot` fields: `timestamp, cpu_temp, cpu_usage, memory_usage, memory_total_gb, uptime, disk_space, disk_used_gb, disk_total_gb, ptp4l_offset_ns, ptp4l_freq, phc2sys_offset_ns, phc2sys_freq, recording, version` (+`wall_mono_offset_s` for cameras) | `time.time()` epoch seconds |
| Session metadata | `session_metadata.json` (session root) | JSON: `session_name, created_at, target, experimenter, experiment, rat_id, strain, batch, stage, trial` | `created_at` ISO8601 UTC |
| Session audit log | `session_events.log` (session root) | Plain text, one line per lifecycle event (start/stop/fault/PTP warning/recovery) | Free text, not machine-structured |
| Config snapshot | `{date}/{module}/config.json` | JSON, module's active config at export time | — |

Note `RecordingSession.start_time`/`end_time` (the controller's in-memory/`sessions.json` record, not
exported to the NAS) are **local controller time, not UTC** — a gap flagged below.

## Proposed NWB mapping

| SAVIOUR stream | NWB container | Notes |
|---|---|---|
| Session metadata | `NWBFile(session_description=experiment, identifier=session_name, session_start_time=..., session_id=session_name, experimenter=[experimenter], notes=...)` | `identifier` must be globally unique — `session_name` is a reasonable default if session names are unique per lab, otherwise generate a UUID and keep `session_name` as `session_id`. |
| `rat_id`/`strain` | `pynwb.file.Subject(subject_id=rat_id, strain=strain, description=...)` | **Gap**: no `species` field exists anywhere in SAVIOUR today, and `Subject.species` is effectively required for DANDI validation (NCBI taxonomy name, e.g. `"Rattus norvegicus"`). Needs a new field added to `experiment_metadata` before this is DANDI-clean — not a blocker for a working prototype, just leave `species` hardcoded/blank initially. |
| Video (per camera) | `pynwb.image.ImageSeries(external_file=[path], format="external", timestamps=<per-frame ns → seconds-since-session_start_time>)` | External reference, not embedded — video is the one stream genuinely too large to duplicate into HDF5. Use the *actual* per-frame `timestamps` array (irregular), not a declared constant `rate`: SAVIOUR already computes real per-frame timing including `dropped_before` gaps, and using `timestamps` preserves that instead of pretending frames are evenly spaced. |
| Audio (per AudioMoth) | `pynwb.base.TimeSeries(data=<raw PCM>, rate=sample_rate, starting_time=<block-0 offset>, unit="volts"/"n.a.")` | Audio is small enough per-session (~384 KB/s at 192kHz/16-bit mono) to embed directly as a chunked+gzip HDF5 dataset — unlike video, this is the "textbook" NWB approach rather than external reference. Requires decoding FLAC → raw PCM at export time (cheap, `soundfile`/`pysoundfile` already a dependency on the module side). Per-block start times in the sidecar are available if per-block (rather than constant-rate) timing precision is wanted, but a constant `rate` is likely accurate enough given AudioMoth's own sample clock is stable within a block. |
| TTL edges | `pynwb.misc.IntervalSeries(timestamps=<edge times>, data=[+1/-1 per HIGH/LOW])`, one per pin | `IntervalSeries` is built for exactly this (interval start/stop semantics). Group under `processing/behavior` or `acquisition` depending on whether the pin represents a stimulus/sync signal (→ `stimulus`) or a measured behavioral event (→ `behavior`) — likely needs per-pin `pin_description` to decide, not a blanket rule. |
| APA shock events | `pynwb.misc.IntervalSeries` (SENDING/STOPPING pairs → start/stop) or `pynwb.misc.AnnotationSeries` (four discrete labelled instants) | `IntervalSeries` fits the SENDING→STOPPING and DELIVERY→STOP_DELIVERY pairs better than four independent annotations. |
| Camera behavioral tracking (`det_cx/det_cy/in_zone`, `cx/cy/zone_state/event`) | `pynwb.behavior.SpatialSeries` (x,y in pixel space) + `pynwb.misc.IntervalSeries` (in_zone) under `processing/behavior` | Pixel-space, not real-world units — note that explicitly in the `SpatialSeries.reference_frame` field rather than implying calibrated coordinates. |
| Health/PTP diagnostics | **Excluded from the NWB file** — left as the existing sidecar CSVs | This is device telemetry, not scientific data; embedding 15+ engineering fields per module per session bloats the file for no scientific value. If timing-quality documentation is wanted inside the NWB file, consider a short QC summary (e.g. max/mean PTP offset during the session) in `NWBFile.notes` rather than the raw time series. |
| `session_events.log`, `config.json` | Not mapped — left as companion files alongside the `.nwb` output, or attached as `NWBFile.notes`/scratch data only if a concrete need shows up | Free text / non-scientific, low value inside the structured format. |
| Module identity (camera model, AudioMoth serial, TTL pin map) | `NWBFile.devices` — one `pynwb.device.Device` per module, `name=module_id` | Straightforward; source data (sensor model, serial) is already read at module init (`camera_base.py` sensor_model, mic module's discovered AudioMoth serials). |

## Metadata gaps this surfaces (pre-existing, not created by this doc)

- **No `species` field anywhere.** Needed for `Subject.species`; DANDI validation expects NCBI
  taxonomy format. Smallest fix: add `species` to `experiment_metadata` (`src/controller/web.py:107-115`)
  and the `update_experiment_metadata` frontend form, defaulting empty.
- **`RecordingSession.start_time`/`end_time` are naive local time, not UTC or timezone-aware**
  (`src/controller/recording.py`, `datetime.now().strftime("%Y%m%d-%H%M%S")`). `NWBFile.session_start_time`
  *requires* a timezone-aware `datetime`. This needs either (a) a real fix upstream — storing UTC on
  `RecordingSession` the way `session_metadata.json`'s `created_at` already correctly does — or (b) the
  export tool assuming the controller's configured system timezone and converting at export time. (a)
  is the better fix and independently useful (this is exactly the kind of naive-datetime bug CLAUDE.md's
  TODO list already tracks elsewhere), but out of scope for the export tool itself to silently paper over.
- **`rat_id`/`strain`/`experimenter` are unvalidated free text.** Fine for `Subject.subject_id`/`strain`
  and `NWBFile.experimenter` as-is — NWB doesn't require controlled vocabulary here, just flagging that
  garbage-in-garbage-out applies (a typo'd `rat_id` between sessions for the same animal won't be caught).

## Open design questions (not resolved here)

1. **Video container**: ship `.ts` as the `ImageSeries.external_file` reference as-is, or remux
   (stream-copy, no re-encode) to `.mp4` for wider downstream-tool compatibility? The camera module
   already runs an `ffmpeg -c copy` pass at segment-stop time for PTS/DTS correction
   (`camera_base.py:631-642`) — an additional stream-copy remux would be cheap to add to the *export
   tool*, but should not touch the recording pipeline itself.
2. **Audio embed vs. reference**: the recommendation above (embed) assumes typical session lengths;
   revisit if very long (multi-hour, many-mic) sessions make the embedded-PCM math less favorable than
   it looks for a single mic/segment.
3. **One NWBFile per session vs. per session-per-subject-per-day with multiple sessions merged**: if a
   single animal has multiple sessions in one day (per `RecordingSession.scheduled_days`/multi-run
   habitat use), does DANDI/lab convention want those as separate NWB files or one? Needs a real answer
   from whoever's driving DANDI upload, not guessed here.
4. **TTL pin → `stimulus` vs `acquisition` vs `behavior`** classification isn't mechanical — needs
   either a config-driven per-pin classification (extend `ttl_config.json`'s per-pin schema) or a
   manual mapping step in the export tool.
5. Multi-camera sync-mode pairing (`camera.sync_mode`) and the fixed per-session phase offset it implies
   (see CLAUDE.md's Camera framesync hardware-gotchas section) — should the export tool apply that
   calibration before writing `timestamps`, or leave raw per-camera timestamps and let downstream
   analysis handle it? Leaning toward raw + documented in `NWBFile.notes`, consistent with not silently
   transforming data the researcher didn't ask for, but worth confirming.

## Phased plan

- **Tier 0 (prototype, single module type)**: one NWBFile per session for camera-only or mic-only
  sessions, session/subject metadata from `session_metadata.json`, no DANDI-compliance work. Proves the
  mapping and timestamp-unit conversions end-to-end against a real exported session directory.
- **Tier 1 (multi-modal)**: combine camera + mic + TTL in one file per session; add APA shock events and
  behavioral tracking (`processing/behavior`) for rigs that have them; add `NWBFile.devices`.
- **Tier 2 (DANDI-ready)**: close the metadata gaps above (species, UTC session times), add a CLI/config
  for lab/institution defaults, validate output with `pynwb`'s built-in validator and/or
  `dandi validate`.

## Non-goals for v1

- No changes to the live recording/export pipeline.
- No changes to what modules record or how — this only repackages already-exported files.
- No DANDI upload automation.
- No attempt to reconcile/calibrate multi-camera phase offsets automatically (see open question 5).
