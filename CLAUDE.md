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

## Hardware gotchas

### AudioMoth USB microphone

- **Device name encodes sample rate.** The AudioMoth firmware names its USB audio device after its current sample rate (e.g. `250kHz AudioMoth USB Microphone`). Calling `configure_audiomoth()` to change the rate causes PulseAudio/PipeWire to drop the old device ID and register a new one. Any code that stores a PulseAudio device ID (e.g. `self.audiomoths`) must re-discover after reconfiguration — otherwise `soundcard.get_microphone(id)` raises `IndexError` intermittently while the monitoring stream (opened at startup) keeps working on the stale stream.
- **Effective bandwidth is much lower than Nyquist at low sample rates.** The EFM32's PDM decimation filter provides only a fraction of the theoretical bandwidth: ~5 kHz usable at 48 kHz, ~20 kHz at 96 kHz, ~70 kHz+ at 192 kHz. 192 kHz is the only rate suitable for ultrasonic rodent vocalisation work. Do not assume Nyquist = usable bandwidth when validating or warning about sample rate choices.
- **Monitoring and recording use separate soundcard recorders** on the same physical device. PipeWire supports multiple simultaneous readers, so this is intentional and works correctly.

### Controller clock (PTP grandmaster)

- The controller runs `phc2sys` to discipline the system clock from its PTP hardware clock. This means `systemd-timesyncd` / NTP is active and `timedatectl set-time` will fail with *"Automatic time synchronization is enabled"*. Any code that sets the system time must disable NTP first (`timedatectl set-ntp false`), set the time, then re-enable it (`timedatectl set-ntp true`) — ideally in a try/finally.

### Module offline detection

- Modules do **not** send a graceful mDNS goodbye on ungraceful disconnection (power loss, switch unplug). The heartbeat timeout (90 s, `HEARTBEAT_TIMEOUT_SECS` in `modules.py`) is the only mechanism for detecting these. The `last_heartbeat_time` field on `Module` must be non-zero before the timeout logic fires, so newly registered modules with no heartbeat yet are not immediately evicted.
