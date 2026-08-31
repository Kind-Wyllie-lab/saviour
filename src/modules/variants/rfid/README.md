# RFID module

A thin SAVIOUR `Module` around the Trovan **LID650 / LID665** RS485 bus.

## What it does

- Opens the RS485 bus (`rfid_bus.RS485Bus`), auto-detecting the USB-serial
  adapter, and broadcasts a logon so every reader unit replies with its address.
- On every transponder read ("ping"):
  - buffers it for the live view,
  - sends an `rfid_read` status message to the controller,
  - appends a row to the current segment CSV **if a recording session is
    active** (`timestamp_ns,timestamp_utc,unit_address,transponder_id,transponder_type`).
- Serves an **MJPEG "pings" stream** on `monitoring._port` (default **8083**):
  a scrolling per-tag timeline with a flash on each fresh read, a running
  rate, and a recent-reads list. Visible in the frontend on the module's
  config card (Settings -> the RFID module).

## Presence tracking (enter / exit smoothing)

A tag sitting in a reader's field pings several times a second, so the raw
stream and the per-ping CSV are noisy. With `rfid.presence.enabled` on, the
module also tracks **visits**: a run of pings for one `(unit, tag)` with no
gap longer than `gap_timeout_s`. A visit is only counted once it clears
`min_pings` **and** `min_dwell_s` (rejects a single edge-of-field blip).

- On a counted visit it emits `rfid_enter`, and on close `rfid_exit`
  (`enter_ts`, `exit_ts`, `duration_s`, `ping_count`, `closed_reason` -
  one of `gap` / `segment_boundary` / `recording_stopped`).
- While recording it writes a **visits CSV** alongside (or instead of) the
  raw one - one row per completed visit:
  `enter_ts_ns,exit_ts_ns,enter_utc,exit_utc,unit_address,transponder_id,transponder_type,ping_count,duration_s,closed_reason`.
- `rfid.presence.record` picks what lands on disk while recording:
  `raw` (per-ping only), `visits` (visits only), `both` (default).
- Changing `rfid.presence.*` re-arms the tracker live and does **not**
  reconnect the bus. Enabling it mid-recording opens the visits CSV for the
  rest of the current segment; switching `record` away from a file type
  takes effect at the next segment.

## Config (`rfid_config.json`)

| key | meaning |
|---|---|
| `rfid.serial_port` | serial device, `""` = auto-detect |
| `rfid.baud` | bus baud rate (19200 for LID650/665) |
| `rfid.scan_on_start` | broadcast a bus logon 1.5 s after connect |
| `rfid.presence.enabled` | track enter/exit visits, not just raw pings |
| `rfid.presence.gap_timeout_s` | silence this long ends a visit (default 2.0) |
| `rfid.presence.min_pings` | pings before a visit counts (default 2) |
| `rfid.presence.min_dwell_s` | seconds present before a visit counts (default 0) |
| `rfid.presence.record` | `raw` \| `visits` \| `both` (default `both`) |
| `monitoring._port` | MJPEG stream port |
| `monitoring.history_secs` | timeline window shown in the stream |
| `monitoring.ping_flash_secs` | how long a fresh read stays highlighted |

## Files

- `rfid_module.py` - the `RFIDModule` class (this is what the service runs)
- `rfid_bus.py` - standalone RS485 / DLE-framed protocol driver
- `rfid_config.json`, `variant.conf` - SAVIOUR variant metadata
- `docs/LID665_commands.pdf` - vendor command reference (not tracked)

The earlier standalone Flask monitor (`rfid_server.py`, `rfid_db.py`) is
superseded by this module and is not part of it.
