# UTC timestamps everywhere (explicit `Z`)

- **Status:** proposed
- **Created:** 2026-11-20
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (one-liner)

## Why

The fleet runs on Europe/London (controller and every module, `timedatectl`),
and the controller writes its session times with `datetime.now()` and no zone:
`start_time` / `end_time` / `error_time` (`20261120-143000`), the session log,
pause / marker times. In winter that equals UTC; from late March to late
October it is an hour ahead, and a session across a clock change has an
ambiguous or repeated hour with nothing in the stamp to say which. Meanwhile
the data itself is already UTC: per-frame `timestamp_ns`, audio block epochs,
TTL edges, module recording file names (`time.gmtime()`), `created_at` in
`session_metadata.json`. So the record disagrees with the data half the year.

Decision (2026-11-20): **every timestamp SAVIOUR writes or shows is UTC,
marked with an explicit `Z`**, except things that are about local time by
nature (below).

## Rules

1. **Written**: stamps get a `Z`. Compact form `20261120-143000Z` where the
   compact form is used today (session times, logs); ISO `2026-11-20T14:30:00Z`
   in JSON written fresh (metadata, manifests, REST).
2. **Shown**: the UI shows UTC and says so (e.g. `14:30 UTC`, or a `UTC`
   column header), never the browser's local time. One shared formatter
   (`src/utils/time.js`: `parseStamp`, `formatUtc`, `formatUtcTime`,
   `formatUtcDate`) replaces the 21 files' ad hoc `toLocale*String` /
   `getHours()` calls.
3. **Read**: one parser on each side accepting both forms. A stamp *without*
   `Z` is legacy controller-local (Europe/London) and is converted with that
   zone (Python `zoneinfo`, JS: interpreted as local as today), so old
   sessions keep their meaning.
4. **Local time on purpose (unchanged, labelled "local")**: habitat / scheduled
   session windows (`scheduled_start_time` HH:MM, `recording_plans.py`), the
   DMX lighting day/night schedule, and anything else that is a wall-clock
   intention ("record 09:00-17:00", "sunrise 07:00"). Those stay local and the
   UI labels them as local time.

## Inventory (from a sweep, 2026-11-20)

Controller (`datetime.now()` → `datetime.now(UTC)` + `Z`):
- `recording.py` (21 sites): `start_time`, `end_time`, `error_time`, pause /
  marker / plan stamps, session log line prefix (`%H%M%S`), habitat plan
  evaluation (`now or datetime.now()`: stays local, rule 4).
- `web.py`: session name suffix for the legacy `send_command start_recording`
  path, `stamp` at 2122, `uploaded_at` (isoformat, no zone), file browser
  `created` (st_ctime, local), the `age` strptime at 4345.
- `health.py` (3), `system_update.py` (1).

Modules:
- `module.py`: `start_time`, stop log lines, the `%Y%m%d_%H%M%S` stamp at 1107.
- `export.py`: manifest timestamp, file `mod_time` (local).
- Already UTC: `recording.py` file names, `camera_base.py`, basler, loom
  `created`, dmx_lighting event times. DMX schedule: local by design.

Frontend:
- Session-name timestamp built from the browser's local clock
  (`NewSessionForm.jsx:40`, `LoomRecording.jsx formatTs`) → UTC. **New session
  folder names change zone from then on**; existing folders untouched.
- Parsers anchored on `^YYYYMMDD-HHMMSS$` (`RecordingStatusWidget`,
  `LoomRecordingTimer`, `FaultAlertModal` via `parseTimestamp`;
  `sessionFormat.js formatFaultTime`, `sessionDateKey`;
  `SessionDetailPage` fault line) → the shared `parseStamp` (accepts `Z`).
- 29 `toLocale*` calls in 21 files → `formatUtc*` (except schedule pickers and
  the DMX schedule editor, rule 4). Session list date grouping
  (`localDateKey`) groups by UTC date.
- `ClockModal` (sets the controller clock): shows / takes UTC.

Docs: `REST_API.md` (session times now carry `Z`), `NWB_EXPORT_DESIGN.md`
(gap closed), `HARDWARE_NOTES.md` if the fleet timezone is mentioned.

## Phasing

1. Shared parsers / formatters (Python `src/shared/timestamps.py`, JS
   `src/utils/time.js`) with tests for both forms and a BST-era legacy stamp.
2. Controller + module writers emit `Z`; readers go through the parser.
   Session-name timestamps in UTC.
3. Frontend display sweep to UTC with a `UTC` label; local-time features
   labelled "local".
4. REST docs, CHANGELOG; check `tools/` scripts that parse session times
   (`analyse_framesync.py`, `make_aligned_video.py`, `apa_track.py`).

## Open questions

- Show a small "All times UTC" note once (top bar clock?) rather than a
  `UTC` suffix on every time? Suggest: the top-bar clock reads `14:30 UTC` and
  tables say UTC in the header; inline times carry no suffix.
- Leave the fleet's system timezone as Europe/London (journald, cron and the
  DMX schedule rely on it) — this plan only changes what SAVIOUR writes and
  shows. Recommended: leave it.
