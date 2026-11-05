# Habitat RFID: replace the Pi 4 logger with the `rfid` module + DB sync

- **Status:** built on `feat/rfid-db-sync` (2026-11-04), not yet on hardware
- **Created:** 2026-11-04
- **Owner:** ascottg
- **CLAUDE.md ref:** Reliability / UX → "Habitat RFID: replace the Pi 4 logger"

## As built (2026-11-04): where it differs from the design below

- **Credentials live in a file on the module, not the module config.**
  `set_rfid_db_config` writes `db_settings.json` (0600) next to the store.
  `get_config` returns the module config unredacted, so keeping the password
  out of it is safer than the export path's approach, and it needs no
  pruning exemption. It also means the module keeps syncing after a reboot
  while the controller is down. The controller still pushes on every
  `get_config` ack and on save.
- **Server key is `(store_id, seq)`**, not `(module_id, seq)`. `store_id` is
  random per database file, so a re-imaged module can't collide with its
  old sequence (which `INSERT IGNORE` would drop silently). `seq` is
  AUTOINCREMENT so it is never reused after pruning.
- **`kind` is `VARCHAR(8)`**, not an ENUM (`read` / `zero` so far).
- **Zero-ID reads** are stored and kept in the raw session CSV, but not sent
  as `rfid_read` or fed to presence; the heartbeat counts them.
- **Retention:** with no database configured, the store is a rolling 30-day
  log (unsynced rows pruned too); with one configured, unsynced rows are kept.
- **Also done:** the controller now forwards `rfid_read` / `rfid_enter` /
  `rfid_exit` to Socket.IO and the `/api/v1/events` SSE stream (they were
  dropped before), the groundwork for live monitoring and triggers. No
  frontend view listens to them yet.
- Files: `rfid_store.py`, `rfid_sync.py`, `docs/sql/rfid_schema.sql`,
  `RfidDbSection.jsx` (controller config "RFID DB" tab), alert in
  `controller/health.py::_check_rfid_db_sync`.

**Left to do:** set up MariaDB on the habitat server from the schema file;
run on the real bus (capture what the zero-ID frames are, check the 10/s cap
is gone); a live RFID view in the frontend; triggers.

## Why

The habitat's Trovan RFID bus (72 LID650/665 units on one RS485 line, FTDI
USB-RS485 cable) is logged today by a standalone Pi 4 (`cent-rpi-0034`,
`~/rfid-rats`) that is outside SAVIOUR. We want a SAVIOUR module on that bus
so reads (a) keep landing in a long-term MariaDB, (b) reach the controller
live over ZMQ for frontend monitoring and experiment triggers, and (c) are
stamped on the fleet's PTP clock so they line up with cameras and mics.

The `rfid` module (`src/modules/variants/rfid/`) already decodes this
protocol (`rfid_bus.py`) and sends `rfid_read` / `rfid_enter` / `rfid_exit`
status messages. What's missing is an always-on durable store and a sync to
the database. A new MariaDB is being set up, so the schema is ours to choose;
the legacy `activity` table format does not need to be reproduced.

## What the current Pi 4 system does (surveyed 2026-11-04)

Two Python processes, each started by an every-minute cron check into an
`lxterminal` on the desktop (so they need a logged-in desktop session):

- `rfid_processor.monitor_hardware_loop`: listens only (sends nothing to the
  bus), finds `10 02 … 10 03 <xor>` frames, inserts into SQLite
  `local_db.db` table `activity(id, Antenna_number, Transponder_serialno,
  Timestamp, Uploaded)`. `Antenna_number` = sending unit's address,
  `Transponder_serialno` = lowercase hex of the bytes after the type byte,
  `Timestamp` = Pi UTC when the frame is parsed (NTP clock).
- `rfid_processor.upload_retry_loop`: every 10 s, `SELECT … WHERE uploaded = 0`,
  then insert into remote MariaDB `Habitat2` (hardcoded; `config.yaml` says
  `Habitat5` and is never read) one row and two commits at a time.

Numbers from `local_db.db` (2026-02-05 → 2026-11-04): 13.46 M rows, 755 MB,
all uploaded, units 1–72 all present.

Problems found, each one a requirement for the replacement:

| Problem | Evidence | Replacement must |
|---|---|---|
| Parser capped at 10 frames/s (one frame per 0.1 s loop); backlog gets late, bunched timestamps | 95 k seconds at exactly 10 rows, 73 k at 9 | read continuously (`rfid_bus.py` already does) |
| Uploader at 21% CPU constantly | no index on `Uploaded`, full scan of 755 MB every 10 s | index unsent rows, batch commits |
| Duplicates possible: remote insert then local mark is not atomic, no unique key | code | idempotent insert keyed on a per-module sequence |
| Local DB never pruned | 755 MB and growing | retention on uploaded rows |
| Frame end found with `find(b'\x10\x03')`; a stuffed `10 10 03` ends a frame early | code | proper DLE state machine (`rfid_bus.py` has one) |
| Plaintext DB credentials in 3 source files | code | credentials in a root-only file, never in git |
| Needs a desktop login; crash leaves a dead terminal | cron + lxterminal | systemd service (standard module) |

### Open: the `0000000000` reads

A large share of reads carry tag ID `0000000000`: on the four busiest units
they are roughly half of all reads (unit 68: 986 k of 1.97 M), and on unit 45
they alternate with a real tag 0.3–1.1 s after it. Likely a "tag left field"
frame, possibly a different frame type the old code decoded as a tag (it
discards the type byte). A raw capture (`rfid_capture.py`, type byte kept) is
pending. Decide after it:

- if it's an exit event: store it with `kind='exit'`, and let presence
  tracking use it to close a visit, replacing the gap-timeout guess;
- if it's noise: drop it before ZMQ and presence, still store it (cheap,
  diagnostic) unless the volume argues otherwise.

Either way `rfid_bus._dispatch` must stop handing it to `on_transponder_read`
as an ordinary tag.

## Design

```
RS485 ─► RS485Bus (reader thread, PTP-clock ts)
            │
            ├─► ZMQ status: rfid_read / rfid_enter / rfid_exit  (live, lossy, unchanged)
            ├─► session CSVs while recording                     (unchanged)
            └─► RfidStore (SQLite, always on, WAL)               (new, durable)
                    │
                    └─► RfidSync thread ─► MariaDB               (new)
```

### Local store (`rfid_store.py`, new)

- SQLite in WAL mode under `/var/lib/saviour/rfid/reads.db`, written by one
  thread fed from a queue, so the bus thread never blocks on disk. Commits in
  batches (every ~1 s or N rows).
- Table `reads(seq INTEGER PRIMARY KEY, ts_ns INTEGER, unit INTEGER,
  tag TEXT, type_code INTEGER, kind TEXT, session_id TEXT NULL,
  synced INTEGER DEFAULT 0)` with a partial index `WHERE synced = 0`.
- Written whether or not a session is recording (the habitat logs
  continuously); `session_id` filled while one is.
- Retention: delete `synced = 1` rows older than `rfid.store.retain_days`
  (default 30). Unsynced rows are never deleted; a health warning fires when
  the unsynced backlog passes a threshold.

### Sync (`RfidSync` thread)

**Store-and-forward, not direct writes (decided 2026-11-04).** Every read is
committed locally first and sent to MariaDB within seconds. Writing each
read straight to the remote DB would tie the bus thread to network latency
and lose reads during any server or network outage, unless it kept a local
buffer for the failure case, which is this design anyway. Live consumers
(frontend, triggers) use the ZMQ messages, so the DB only needs to be
near-real-time.

- Every `rfid.sync.interval_s` (default 5 s): select up to N unsynced rows by
  `seq`, `INSERT IGNORE` them in one transaction, then mark them synced
  locally. Crash between the two → re-sent, ignored by the unique key, no
  duplicate.
- Exponential backoff on connection failure, capped (the old loop's 10 s ×
  2^n up to ~10 min is fine).
- Driver: `pymysql` (pure Python, no libmariadb build on the Pi) unless the
  `mariadb` connector is already in the module env.
- Health fields: `sync_backlog`, `last_sync_ok`, `last_sync_error`;
  `@check()` only warns (a DB outage must never block a recording).

### MariaDB schema (proposed)

```sql
CREATE TABLE rfid_read (
  id         BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  module_id  VARCHAR(64)  NOT NULL,
  seq        BIGINT       NOT NULL,      -- module-local sequence
  ts         DATETIME(6)  NOT NULL,      -- UTC, PTP clock
  unit       SMALLINT UNSIGNED NOT NULL, -- reader unit address (antenna)
  tag        CHAR(10)     NOT NULL,      -- uppercase hex
  type_code  TINYINT UNSIGNED NULL,
  kind       ENUM('read','zero','exit','other') NOT NULL DEFAULT 'read',
  session_id VARCHAR(128) NULL,
  UNIQUE KEY uq_module_seq (module_id, seq),
  KEY ix_tag_ts (tag, ts),
  KEY ix_unit_ts (unit, ts)
);
```

Possible later tables, not in scope: `rfid_unit` (unit → box/location map,
pairs with the habitat box map in `plans/habitat-post-processing.md`),
`animal` (tag → animal ID).

### Where the DB runs, and credentials (decided 2026-11-04)

The new MariaDB runs on the habitat server, the machine that already hosts
the habitat Samba share (`export.share_ip` in
`variants/habitat/habitat_controller_config.json`). Modules already reach it
for export, so the module syncs **directly** to it; no controller relay.

Credentials are entered on the controller config page and pushed to RFID
modules, mirroring the Samba export credentials end to end:

| Samba export (existing) | RFID DB (new) |
|---|---|
| controller config `export.share_*` | controller config `rfid_db.{host,port,database,username,password}` |
| `set_export_config` `@command()` on the module | `set_rfid_db_config` on `RFIDModule` |
| pushed on module discovery/reconnect (`controller.py:309`) and on save (`sync_export_to_all`) | same, to modules of type `rfid` only |
| `export` in `_NEVER_PRUNE_SECTIONS` and the startup prune exemption (`modules/config.py`) | add `rfid_db` to both, or the password is pruned on every restart (the bug the export path already hit) |
| redacted in logs (`communication.py:113`, `_sanitise_config`) | key is named `password`, so `_sanitise_config` catches it; add the command to the controller's redacted-command list |

Plus a **Test connection** button: the controller sends `rfid_db_test` to
the module, which connects, checks the table exists and it can insert, and
returns the error text if not.

Same exposure as the Samba password (plaintext in the controller's and the
module's `active_config.json`), which the threat model accepts. Don't reuse
the current `habwrite` password; it has been in plaintext in the old code.

### Server setup (on the habitat server, by hand, documented in the PR)

- `bind-address` reachable from the rig network (not `127.0.0.1`); 3306
  open to the rig subnet only.
- Schema created by an admin from a checked-in `.sql` file; the module never
  runs DDL.
- A writer account restricted to the rig subnet with `INSERT` on
  `rfid_read` only (`INSERT IGNORE` needs nothing more), plus `SELECT` for
  the connection test. Analysis uses a separate read-only account.

### Changes to the existing module

- `rfid_bus.py`: keep the type byte and pass it through; classify zero-ID
  frames (see Open above).
- `rfid_module.py`: per-ping `logger.info` → `debug` (at 10+ reads/s that is
  a journald flood); feed every read to `RfidStore`.
- Live ZMQ messages unchanged; triggers build on `rfid_read` / `rfid_enter`
  on the controller side (separate plan when a trigger is specified).

## Open questions

1. ~~Where does the DB run?~~ Decided: the habitat server (see above).
2. ~~Migrate legacy rows?~~ Decided: no, they stay in `Habitat2`.
3. `0000000000` frames: meaning unknown. Until a raw capture settles it,
   store them with their type byte and `kind='zero'`, and keep them out of
   presence tracking and the live tag stream (counted in health instead).
   The capture script is `rfid_capture.py`; the SAVIOUR module's own debug
   frame hook gives the same data once it's on the bus.
4. ~~Retention and thresholds~~ Decided: 30 days local retention of synced
   rows; sync is store-and-forward every 5 s (below), so backlog warns at
   5 min oldest-unsynced and escalates at 1 h.

## Cutover

1. Build and test against a recorded byte stream (`/tmp/rfid_raw.bin` from
   the capture) and an emulated bus; unit tests for store + idempotent sync.
2. Run the SAVIOUR module beside the Pi 4 is not possible (one port); instead
   swap the cable at a quiet time, run for 24 h, compare per-unit read counts
   against the same weekday on the old system.
3. Retire the Pi 4's cron jobs; keep its SD card until the new DB has a week
   of clean data.
