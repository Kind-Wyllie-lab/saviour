# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SAVIOUR (Synchronised Audio Video Input Output Recorder) is a modular, PoE-networked multi-sensor recording system for rodent behavioural research. Each Raspberry Pi 5 on the network runs either as the **controller** or as a **module** (camera, microphone, TTL, RFID, etc.).

## Commands

### Python backend

```bash
# Activate the virtual environment first
source env/bin/activate

# Run tests
pytest

# Run a single test file
pytest src/controller/tests/test_facade.py

# Lint
ruff check src/

# Type check
mypy src/
```

### Frontend (React/Vite)

```bash
cd src/controller/frontend

# Dev server (proxies to Flask on port 5000)
npm run dev

# Production build (outputs to dist/)
npm run build

# Lint
npm run lint
```

### Installation & role assignment

```bash
# Full system setup (run once per device)
./setup.sh

# Assign role (controller | module) and type
./switch_role.sh
```

## Architecture

### Two-role system

Every device runs one of two roles, set in `/etc/saviour/config`:

- **Controller** (`src/controller/`) — PTP grandmaster, mDNS service discovery, ZeroMQ command hub, Flask+SocketIO web interface on port 5000, recording session orchestration, file export queue to Samba/NAS.
- **Module** (`src/modules/`) — PTP slave, registers via Zeroconf, connects to controller's ZeroMQ sockets, records to `/var/lib/saviour/recordings`, exports files to the controller's Samba share.

The concrete implementations live under `src/controller/examples/` and `src/modules/examples/`. Each example subclasses the abstract `Controller` or `Module` base class.

### Inter-service communication

ZeroMQ PUB/SUB is used for all controller↔module messaging:

- Controller publishes commands on topics `cmd/<module_id>` or `cmd/all`
- Modules publish status/heartbeats on `status/<module_id>`
- Message envelope (JSON): `proto`, `type`, `timestamp`, `from`, `to`, `msg_id`, `command`, `params`, `status`, `result`, `error`

See `docs/PROTOCOL_V1.md` for the full spec.

### Config layering

JSON config is merged in three layers: `base_config.json` → `active_config.json` → `.env` overrides. Keys prefixed with `_` are internal defaults not meant to be overridden by users. The `Config` class in `config.py` handles this for both controller and module sides.

### Module command system

Module methods decorated with `@command()` are auto-registered as remotely callable RPCs. `@check()` registers status/health reporters. Commands are dispatched by the `Communication` class when a matching `cmd/` ZeroMQ message arrives.

### Frontend↔backend

The React frontend communicates with Flask exclusively via **Socket.IO** (not REST). The Flask server emits module state, health, and recording events; the frontend sends commands back as Socket.IO events. The Vite dev server proxies `/socket.io` to `localhost:5000`.

### Key source files

| File | Purpose |
|------|---------|
| `src/controller/controller.py` | Abstract `Controller` base class |
| `src/controller/facade.py` | `ControllerFacade` — internal API for intra-component calls |
| `src/controller/web.py` | Flask server + all Socket.IO event handlers |
| `src/controller/modules.py` | Tracks discovered module states |
| `src/modules/module.py` | Abstract `Module` base class |
| `src/modules/facade.py` | `ModuleFacade` |
| `src/modules/export.py` | Samba-based file export, config export, traffic shaping |
| `src/modules/config.py` | Config layering: base → active, `set_all`, `save_active` |
| `src/modules/examples/microphone/microphone_module.py` | AudioMoth recording + monitoring stream |
| `src/modules/examples/template/` | Boilerplate for creating a new module type |

### Module types

`camera`, `microphone`, `ttl`, `rfid`, `apa_camera`, `apa_arduino`, `sound` — each under `src/modules/examples/<type>/`.

## Conventions

- **Conventional commits** with `feat/`, `fix/`, `refactor/` branch prefixes
- Branch flow: `develop` → `staging` → `main`; PRs always target `develop`
- Python line length: 88 (ruff), targeting py38 compatibility
- Systemd-aware logging: timestamps are skipped when `INVOCATION_ID` env var is set (systemd sets this)
- PTP log parsing lives in `src/*/ptp.py`; health metrics in `src/*/health.py`

## TODO

Known issues and planned improvements, grouped by priority. Check these off (`- [x]`) as they are completed.

### High priority — silent data loss / correctness

- [x] **`export.py`: Samba mount not retried** — if the mount fails at session start the entire segment is never exported; add a retry loop with backoff.
- [x] **`export_queue.py`: failed exports dropped permanently** — `on_export_failed()` removes the module from `_active` without re-queuing; add retry logic so transient NAS outages don't silently lose data.
- [x] **`export.py`: `PENDING_*` rename not rolled back on copy failure** — if `shutil.copy2()` fails after `os.rename()`, the source file is left in a broken state with no recovery path.
- [x] **`export.py`: `self.exporting` flag and `self.staged_for_export` list lack thread locks** — written from recording, export, and command-handler threads simultaneously; wrap with `threading.Lock`.
- [x] **`config.py`: `_recursive_update()` modifies shared dict without a lock** — other threads can read a half-merged config; guard with a lock in `set_all()`.
- [x] **`modules.py`: config sync status transitions not atomic** — `received_module_config()` compares `target_config` and writes status in two unsynchronised steps; a concurrent `set_target_module_config()` call corrupts state.

### Medium priority — reliability / UX

- [ ] **`export.py` / `module.py`: blocking subprocess calls on network thread** — `_mount_share()` has no timeout and `update_saviour()` blocks ZMQ command processing; move to background threads.
- [ ] **`config.py`: `set()` fires `on_module_config_change()` even when value is unchanged** — guard with an equality check before calling `configure_module()`.
- [ ] **`config.py`: `reset_to_defaults()` doesn't purge stale keys** — keys removed from the module config file persist in `active_config.json` after a reset; rebuild from scratch rather than merging.
- [ ] **`web.py`: `_`-prefixed (internal) config keys not filtered on inbound socket events** — the frontend can overwrite `_communication.*`, `_codec`, etc.; apply `filterPrivateKeys` equivalent server-side before merging.
- [ ] **`modules.py`: online/offline status can oscillate without hysteresis** — a single delayed heartbeat immediately brings a module back online; add a short debounce (e.g. require 2 consecutive heartbeats before marking online again).
- [ ] **`controller/network.py`: infinite loop waiting for `nmcli`** — if NetworkManager is not running the controller hangs at startup; add a timeout and a clear error message.
- [ ] **Session metadata not retried if NAS unavailable at session start** — `_write_session_metadata()` in `web.py` runs once; add retry on NAS recovery.
- [ ] **`facade.py`: `apply_section_to_type` has no ack timeout** — bulk config pushes that are never acknowledged leave the frontend in a permanent "pending" state.

### Low priority — observability / maintenance

- [ ] **No correlation IDs on ZMQ commands** — matching a `cmd_ack` to its originating command is impossible under concurrent load; add a `msg_id` round-trip in the command envelope.
- [ ] **PTP offset stored as raw nanoseconds with no unit annotation** — annotate the field name (`ptp4l_offset_ns`) or normalise to µs so the frontend doesn't have to guess units.
- [ ] **Hardcoded IP ranges in three files** — `192.168.1.` and `10.0.0.` appear in `src/modules/network.py`, `src/controller/network.py`, and `src/modules/export.py`; centralise in `base_config.json`.
- [ ] **`switch_role.sh`: `ROLE=` / `TYPE=` values written without sanitisation** — a typo or injection can embed shell syntax in `/etc/saviour/config`; validate against an allowlist.
- [ ] **`setup.sh`: package install exit codes not checked** — a failed `apt-get install` mid-script lets execution continue with misleading downstream errors; add `set -e` or per-step checks.
- [ ] **Module version stays stale after restart** — zeroconf properties are not re-read on rediscovery; force a property refresh on `module_discovery()`.

### Tests

- [ ] **Config merge has no unit tests** — `_merge_defaults`, `_merge_dicts`, `_merge_internal_defaults`, and `reset_to_defaults` are all untested; add `pytest` cases covering each merge path and edge cases (stale keys, `_`-prefix re-application).
- [ ] **Export pipeline has no unit tests** — mock the Samba mount and verify PENDING rename, copy, cleanup, and failure rollback paths.
- [ ] **No integration test for multi-module recording** — add a test that simulates controller + 2 modules, a full record/stop/export cycle, and a mid-session module dropout.
- [ ] **No config schema regression test** — a renamed or removed config key silently breaks modules loading old `active_config.json`; add a test that loads each `*_config.json` against the current base and asserts all required keys are present.

## Hardware gotchas

### AudioMoth USB microphone

- **Device name encodes sample rate.** The AudioMoth firmware names its USB audio device after its current sample rate (e.g. `250kHz AudioMoth USB Microphone`). Calling `configure_audiomoth()` to change the rate causes PulseAudio/PipeWire to drop the old device ID and register a new one. Any code that stores a PulseAudio device ID (e.g. `self.audiomoths`) must re-discover after reconfiguration — otherwise `soundcard.get_microphone(id)` raises `IndexError` intermittently while the monitoring stream (opened at startup) keeps working on the stale stream.
- **Effective bandwidth is much lower than Nyquist at low sample rates.** The EFM32's PDM decimation filter provides only a fraction of the theoretical bandwidth: ~5 kHz usable at 48 kHz, ~20 kHz at 96 kHz, ~70 kHz+ at 192 kHz. 192 kHz is the only rate suitable for ultrasonic rodent vocalisation work. Do not assume Nyquist = usable bandwidth when validating or warning about sample rate choices.
- **Monitoring and recording use separate soundcard recorders** on the same physical device. PipeWire supports multiple simultaneous readers, so this is intentional and works correctly.

### Controller clock (PTP grandmaster)

- The controller runs `phc2sys` to discipline the system clock from its PTP hardware clock. This means `systemd-timesyncd` / NTP is active and `timedatectl set-time` will fail with *"Automatic time synchronization is enabled"*. Any code that sets the system time must disable NTP first (`timedatectl set-ntp false`), set the time, then re-enable it (`timedatectl set-ntp true`) — ideally in a try/finally.

### Module offline detection

- Modules do **not** send a graceful mDNS goodbye on ungraceful disconnection (power loss, switch unplug). The heartbeat timeout (90 s, `HEARTBEAT_TIMEOUT_SECS` in `modules.py`) is the only mechanism for detecting these. The `last_heartbeat_time` field on `Module` must be non-zero before the timeout logic fires, so newly registered modules with no heartbeat yet are not immediately evicted.
