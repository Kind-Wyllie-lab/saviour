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

## Config (`rfid_config.json`)

| key | meaning |
|---|---|
| `rfid.serial_port` | serial device, `""` = auto-detect |
| `rfid.baud` | bus baud rate (19200 for LID650/665) |
| `rfid.scan_on_start` | broadcast a bus logon 1.5 s after connect |
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
