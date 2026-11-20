# NWB export from the Post-processing page

- **Status:** proposed
- **Created:** 2026-11-20 (mapping scoped 2026-08-13 in `docs/NWB_EXPORT_DESIGN.md`)
- **Owner:** ascottg
- **CLAUDE.md ref:** "Maintenance / architecture → After v1.0" (moves to Open work)

## What

An **Export NWB** action on the Post-processing page that turns a finished
session (as exported to the share) into one `.nwb` file, with the subject
details asked for at that point. The NWB libraries aren't part of a stock
install: the page offers to install them first.

The stream → NWB mapping is already worked out in
`docs/NWB_EXPORT_DESIGN.md` (video as external `ImageSeries` with the real
per-frame timestamps, audio embedded as `TimeSeries`, TTL edges and APA shocks
as `IntervalSeries`, tracking as `SpatialSeries`, one `Device` per module,
health / logs left as companion files). This plan covers how it is offered.

## Design

### 1. "Install NWB export package"

- Post-processing → NWB panel shows **Install NWB export package** when
  `pynwb` isn't importable. Admin only.
- Installs into a **separate venv**, `/usr/local/src/saviour/env-nwb`
  (`python -m venv --system-site-packages` off the main env so numpy /
  soundfile are shared), `pip install pynwb` (pulls `h5py`, `hdmf`,
  `pandas`). Kept out of `env`, so module/controller installs stay light and a
  broken NWB stack can't break the controller (CLAUDE.md: pandas is
  deliberately absent from the main env).
- Runs as a background job (`nwb_install` socket events: started / log line /
  done / error), so the page shows progress; the controller needs internet
  (this rig has it; a closed-LAN rig gets a clear error and the offline
  alternative: run `tools/export_nwb.py` on a workstation).
- `pyproject.toml` gains an `nwb` extra (`pip install -e ".[nwb]"`) for
  workstation installs.
- Status: `get_nwb_status` → `{installed, version, env}`.

### 2. Export form (per session)

- **Species**: dropdown, default **Rattus norvegicus**; also Mus musculus and
  "Other…" (free text, NCBI taxonomy name). Remembered per browser.
- **Subject ID**: prefilled from the session's rat ID. **Strain**: prefilled
  from metadata. **Sex** (M / F / U), **Age** (ISO 8601, e.g. `P90D`),
  **Weight**, **Genotype**, **Description**: optional.
- **Session description**: prefilled from the experiment name.
- What to include: video (external links), audio (embedded; size estimate
  shown), TTL, APA shocks / tracking where present.
- **Export** → background job (`nwb_export` events, progress per stream) →
  writes `<session>/<session>.nwb` next to the data on the share, with a
  download link and the export log.

### 3. Converter

- `src/controller/nwb_export.py`, run as a subprocess with `env-nwb/bin/python`
  (never imported by the controller process): reads the exported session dir,
  writes the file, prints JSON progress lines.
- `session_start_time` from the session's start via
  `src/shared/timestamps.parse()` (UTC `Z` stamps; older zone-less ones are
  converted from Europe/London).
- `tools/export_nwb.py` = the same converter with a CLI, for workstations.
- Validate with `pynwb.validate` (and `nwbinspector` if installed); report
  warnings in the export log.

## Phasing

1. Install button + status + venv; `nwb` extra.
2. Converter: metadata + subject, video (external), TTL; export form and job.
3. Audio, APA shocks, tracking; inspector warnings.
4. Docs: `docs/NWB_EXPORT_DESIGN.md` → describes what was built; guide page.

## Depends on

- A session's data being on the share (export complete).

## Open questions

- Where the `.nwb` lands: next to the data in the session folder (default
  here), or a separate `nwb/` folder on the share?
- Embed audio by default, or off by default for long habitat sessions (a
  24 h, 16-mic session is ~530 GB of PCM)? Suggest: on for standard
  sessions, off with a size warning above a threshold.
