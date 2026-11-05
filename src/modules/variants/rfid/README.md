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

## Local store and long-term database

Every read is also written, recording or not, to a local SQLite store
(`rfid_store.py`, `rfid.store.path`, default `/var/lib/saviour/rfid/reads.db`).
A sync thread (`rfid_sync.py`) sends unsynced rows to a MariaDB every
`rfid.sync.interval_s` (5 s) in one `INSERT IGNORE` batch, then marks them
synced. The server's unique key on `(store_id, seq)` makes a re-sent batch a
no-op, so a crash between "sent" and "marked" never duplicates. A server or
network outage only grows the local backlog.

- **Connection settings** come from the controller config page (RFID DB tab,
  the `rfid_db` section), pushed with `set_rfid_db_config` on every module
  connect and on save. The module keeps them in `db_settings.json` (mode 0600)
  next to the store, not in the module config, so `get_config` never echoes
  the password, and it keeps syncing after a reboot while the controller is
  down. An empty host turns the sync off.
- **Schema**: `docs/sql/rfid_schema.sql` (an admin runs it once; the module
  never runs DDL).
- **Retention**: synced rows older than `rfid.store.retain_days` (30) are
  deleted hourly. Unsynced rows are kept indefinitely while a database is
  configured; with none configured the store is a rolling 30-day log.
- **Health**: each heartbeat carries `rfid_db` (state, backlog, age of the
  oldest unsynced read, last error). The controller alerts at 5 min and again
  at 1 h of backlog; it never blocks recording.
- **All-zero transponder IDs** (`0000000000`, about half of all reads on the
  busiest habitat units under the old Pi 4 logger; meaning not yet known)
  are stored with `kind = 'zero'` and kept in the raw session CSV, but are
  not sent as `rfid_read` or fed to presence tracking. The heartbeat counts
  them (`zero_reads`).
- Dependency: `python3-pymysql` (apt, `variant.conf`).

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

## Mock mode (no reader attached)

`rfid.mock.enabled` swaps the serial port for a simulated bus
(`rfid_mock.MockBusSerial`), for working on the frontend or the module without
hardware. The simulator speaks the real frame protocol, so everything from the
frame decoder up runs as normal: the logon finds `rfid.mock.units` units
(addresses 1..N, firmware `800`, made-up serial numbers), and `rfid.mock.tags`
animals wander between neighbouring units, pinging at `ping_hz` for a few
seconds at a time. That gives presence enter/exit visits, `rfid_read` messages,
the MJPEG view, session CSVs and heartbeat counts. `zero_fraction` of reads carry
an all-zero ID. Tag IDs come from `seed`, so they're the same across restarts.

- The bus shows as `simulated` (health check, stream header) and the module logs a
  warning on connect, so it can't be mistaken for a real reader.
- Simulated reads go to a separate store (`reads_mock.db` next to `reads.db`)
  and are **never synced** to MariaDB. The DB tab shows `rfid_db.mock: true`
  and the sync as not configured. "Test connection" still uses the saved
  settings.
- Toggling it, or changing any `rfid.mock.*` key, reconnects the bus and
  reopens the store. Turn it off before recording real data.

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
| `rfid.store.path` | local SQLite store (restart to change) |
| `rfid.store.retain_days` | delete synced reads older than this (default 30) |
| `rfid.sync.interval_s` | seconds between database syncs (default 5) |
| `rfid.sync.batch_rows` | rows per sync transaction (default 500) |
| `rfid.mock.enabled` | simulated bus instead of the serial port (default off) |
| `rfid.mock.units` / `tags` | simulated reader units / animals (6 / 4) |
| `rfid.mock.ping_hz` | reads per second while a tag is at a unit (5) |
| `rfid.mock.zero_fraction` | share of reads with an all-zero ID (0.05) |
| `rfid.mock.seed` | fixes the simulated tag IDs and serial numbers (1) |
| `monitoring._port` | MJPEG stream port |
| `monitoring.history_secs` | timeline window shown in the stream |
| `monitoring.ping_flash_secs` | how long a fresh read stays highlighted |

## Planned / not built (2026-08-31, from a hardware-team note)

Larger than the current demo scope, recorded so they aren't lost:

- **Active bus polling when a block of expected tags goes quiet.** The
  LID650/665 supports addressed *polled* reads, not just spontaneous ones.
  Given a set of expected transponders, if none ping for a configured
  period, poll each unit to tell "animals left / out of range" from
  "reader or antenna fault". Pairs with the antenna self-test below.
- **Antenna self-test.** The reader exposes antenna current / tuning
  diagnostics; surface "antenna connected / tuned / detuned" as a
  `@check()` and/or a periodic health field, so an unattended rig flags a
  disconnected or detuned antenna instead of just going silent.

## Files

- `rfid_module.py` - the `RFIDModule` class (this is what the service runs)
- `rfid_bus.py` - standalone RS485 / DLE-framed protocol driver
- `rfid_store.py` - always-on local SQLite read store
- `rfid_sync.py` - store-and-forward sync to MariaDB
- `rfid_mock.py` - simulated bus for `rfid.mock.enabled`
- `rfid_config.json`, `variant.conf` - SAVIOUR variant metadata
- `docs/LID665_commands.pdf` - vendor command reference (not tracked)

The earlier standalone Flask monitor (`rfid_server.py`, `rfid_db.py`) is
superseded by this module and is not part of it.
