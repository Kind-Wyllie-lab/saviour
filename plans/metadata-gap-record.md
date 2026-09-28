# Per-session metadata gap record

- **Status:** proposed
- **Created:** 2026-09-08
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Correctness / data loss" (A1 liveness — "no
  metadata gap record"); also the Habitat Session item ("metadata gap-JSON on
  pause (events-log markers only); explicit boot gap-marker on restart") and
  `plans/v1.0-roadmap.md` A1 / the boot-pause gap-marker line.

## Why

Several separate pieces of work all assume a machine-readable per-session
record of *when a recording had no data, and why* — and none of them write
one. Today the only trace of a gap is human-readable `FAULT` / `WARNING`
lines in `session_events.log`, plus the transient `session.recording_health_
warning` string. Consequences:

- **A1 (recording liveness):** a camera goes silent for 30 s mid-session →
  `recording_health_warning` fires and clears, but afterwards nothing in the
  session's metadata says "module `camera_x` produced no frames 14:03:12 →
  14:03:44". An analyst re-composing the session can't tell a real gap from a
  frame-index artefact.
- **Habitat Session pause/resume:** a disk-full auto-pause stops every plan's
  modules for hours; the resume is an events-log marker only. `video_compose`
  / an ethogram has no structured "these 3 h are blank on purpose" input.
- **Controller restart with a session ACTIVE:** the recovery path re-issues
  `start_recording`; the window between the crash and recovery is a gap with
  no record and no PTP re-check marker.
- **The "sync stated upfront, quantified" requirement**
  (`plans/multicam-frame-alignment-and-sync-provenance.md` Defect 2): the
  provenance block wants to declare gaps, not silently resample over them.

One small artefact unblocks all of these.

## The artefact

`<session>/<date>/session_gaps.json` on the share (sibling of
`session_metadata.json`), appended atomically by the controller. Schema:

```json
{
  "schema": 1,
  "session_name": "...",
  "generated_by": "recording/1",
  "gaps": [
    {
      "id": "g0001",
      "modules": ["camera_a1b2"],          // or ["*"] for whole-session
      "start_ns": 1788876464727639080,      // PTP wall clock (controller = GM)
      "end_ns": 1788876496110284000,        // null while ongoing
      "cause": "liveness",                  // enum, below
      "severity": "warning",               // warning | error
      "detail": "no frames for 31.4 s (recording_health_warning)",
      "recovered": true,
      "source": "controller"
    }
  ]
}
```

`cause` enum: `liveness` (a `_check_recording_alive` / `recording_health_
warning` episode), `module_offline` (heartbeat-timeout dropout), `pause`
(operator), `pause_disk` (disk-full auto-pause), `plan_window` (a Habitat
plan's window closed — expected, `severity: warning`), `controller_restart`
(session was ACTIVE across a restart; end_ns = when recovery confirmed
recording), `ptp_regressed` (mid-recording PTP breached the degraded
threshold long enough to distrust timestamps).

## Where entries are opened / closed

All controller-side, in `recording.py`, at points that already fire an event
or alert — this rides along, it does not add new detection:

| open a gap | close it |
|---|---|
| `handle_recording_health_status(... "unhealthy")` → `session.recording_health_warning` set | `... "recovered"` / warning cleared |
| `module_offline()` for a module in an ACTIVE session | `module_back_online()` re-registers it |
| `pause_session()` / `_pause(... "disk")` | `resume_session()` / `_resume()` |
| a Habitat plan `_stop_plan()` at a window edge | `_start_plan()` at the next window |
| `_load_sessions()` on boot finds a session still `ACTIVE` (or `feat/habitat-session-plans`'s restart path) — open a `controller_restart` gap with `start_ns` = last known heartbeat/segment time | the recovery `start_recording` ack, or first post-restart data |
| `_check_ptp_sync` mid-recording breach sustained past N polls | offset back under the degraded threshold |

A helper `Recording._record_gap(session, modules, cause, severity, detail,
start_ns=None)` opens (or extends a still-open gap of the same
cause+modules), and `_close_gap(session, cause, modules, end_ns)` closes it.
`session.open_gaps: dict` (not persisted raw — reconstructed from the JSON on
load) tracks what's currently open so a flap doesn't create 40 rows.

Best-effort write (same posture as `_log_session_event`): a share hiccup must
never stall a recording. If the write fails the event log still has the
human line.

## Downstream consumers (out of scope to build here, but the schema serves them)

- `video_compose` / the ethogram provenance caption: list gaps, draw them as
  hatched regions, never resample across an `error`-severity gap silently.
- The unattended daily digest: "N gaps totalling M min" per session.
- A `tools/` reader that prints the gap timeline for a session dir.

## Acceptance

- Kill a camera module's service mid-session → on recovery,
  `session_gaps.json` has one `module_offline` gap for that module with
  plausible `start_ns`/`end_ns`, `recovered: true`.
- Disk-full auto-pause then auto-resume → one `pause_disk` gap spanning the
  outage.
- `kill -9` the controller mid-session, restart → a `controller_restart` gap
  from ~last-heartbeat to recovery.
- A 3-flap `recording_health_warning` inside 20 s → **one** `liveness` gap
  (start..last-recovery), not three.
- `session_gaps.json` is valid JSON after 50 open/close cycles; a forced
  `chmod 000` on the share dir mid-session → recording continues, event log
  still logged, no traceback.
- Deleting the session (`DELETE /api/v1/sessions/<name>?files=true`) removes
  the file with the rest.

## Not doing

- Any new *detection* — this only records gaps the existing liveness /
  offline / pause / restart machinery already notices.
- Per-frame gap accounting inside a segment (that's the `_recording.json`
  `deficit_vs_csv` / framesync layer).
- A frontend timeline widget (a later, separate UX item).
