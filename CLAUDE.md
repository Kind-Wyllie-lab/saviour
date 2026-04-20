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
| `src/modules/examples/template/` | Boilerplate for creating a new module type |

### Module types

`camera`, `microphone`, `ttl`, `rfid`, `apa_camera`, `apa_arduino`, `sound` — each under `src/modules/examples/<type>/`.

## Conventions

- **Conventional commits** with `feat/`, `fix/`, `refactor/` branch prefixes
- Branch flow: `develop` → `staging` → `main`; PRs always target `develop`
- Python line length: 88 (ruff), targeting py38 compatibility
- Systemd-aware logging: timestamps are skipped when `INVOCATION_ID` env var is set (systemd sets this)
- PTP log parsing lives in `src/*/ptp.py`; health metrics in `src/*/health.py`
