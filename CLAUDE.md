# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SAVIOUR (Synchronised Audio Video Input Output Recorder) is a modular, PoE-networked multi-sensor recording system for rodent behavioural research. Each Raspberry Pi 5 on the network runs either as the **controller** or as a **module** (camera, microphone, TTL, RFID, etc.).

## Commands

### Python backend

```bash
# Activate the virtual environment first
source env/bin/activate

# Run tests — testpaths already covers src/controller/tests, src/modules/tests,
# and src/tests, so plain `pytest` runs everything:
pytest

# Run a single test file
pytest src/controller/tests/test_facade.py

# Lint — ruff is a dev dependency (pip install -e ".[dev]") and is what CI runs
ruff check src/

# Type check (aspirational — strict mypy config has never passed)
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

### Analysis tools

```bash
# env2 is a second venv for analysis scripts that need pandas
# (numpy IS in the main env — it's a core dependency in pyproject.toml;
# only pandas is omitted to keep module installs lightweight)
source env2/bin/activate

# Framesync analysis — compare per-frame timestamp CSVs from a session directory
python3 tools/analyse_framesync.py /path/to/session/date_dir
# e.g.:
python3 tools/analyse_framesync.py /home/pi/controller_share/my-session/20260703

# Frame-aligned video — produce a side-by-side aligned output from a session directory
# Checks PTP quality, strips pre-stage CSV frames, computes per-camera skip, calls ffmpeg
python3 tools/make_aligned_video.py /path/to/session [--output out.mp4] [--layout side|stack|grid]
# e.g.:
python3 tools/make_aligned_video.py /home/pi/controller_share/my-session
```

### Installation & role assignment

```bash
# Full system setup (run once per device)
./setup.sh

# Assign role (controller | module) and type — interactive TUI
sudo saviour-config
```

## Architecture

### Two-role system

Every device runs one of two roles, set in `/etc/saviour/config`:

- **Controller** (`src/controller/`) — PTP grandmaster, mDNS service discovery, ZeroMQ command hub, Flask+SocketIO web interface on port 5000, recording session orchestration, file export queue to Samba/NAS.
- **Module** (`src/modules/`) — PTP slave, registers via Zeroconf, connects to controller's ZeroMQ sockets, records to `/var/lib/saviour/recordings`, exports files to the controller's Samba share.

The concrete implementations live under `src/controller/examples/` and `src/modules/examples/`. Each example subclasses the abstract `Controller` or `Module` base class.

### Inter-service communication

ZeroMQ **ROUTER/DEALER** is used for controller→module commands; modules publish status/heartbeats on a PUB/SUB socket:

- Controller binds a ROUTER socket (port 5555); each module connects a DEALER socket with its `module_id` as the ZMQ identity and sends a `"hello"` frame on connect to register.
- Controller tracks connected dealers in `_connected_dealers`; heartbeat timeout evicts a module and calls `remove_dealer()`.
- Modules publish status/heartbeats on `status/<module_id>` (PUB, port 5556); controller SUBs to all topics.
- **Actual wire format**: commands are plain strings `"{command} {json_params}"` routed to the DEALER by identity; status messages are JSON dicts with `type`, `timestamp`, `module_id`, `module_name` plus type-specific fields. There is no envelope, no `msg_id`, and no ack correlation.
- ⚠ `docs/PROTOCOL_V1.md` is **aspirational, not descriptive** — it documents PUB/SUB `cmd/` topics and a JSON envelope with `msg_id`/ack/retry semantics that were never implemented. Do not use it as a reference for the current protocol.

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
- Branch flow: PRs → `staging` → `main`
- Python line length: 88 (ruff). ⚠ pyproject claims py38 compatibility but the code requires **3.11+** (`match` statements, `StrEnum`, numpy ≥ 2.2); all deployed Pis run Bookworm/3.11+.
- Systemd-aware logging: timestamps are skipped when `INVOCATION_ID` env var is set (systemd sets this)
- PTP log parsing lives in `src/*/ptp.py`; health metrics in `src/*/health.py`
- `src/__version__.py` is written by a **pre-commit hook** (`git describe`) so ZIP deploys carry the version. It is inherently one commit behind — never "fix" this by committing a manual bump.
- The frontend variant (basic / loom / apa / habitat / acoustic_startle) is selected by a **hardcoded import in `src/controller/frontend/src/main.jsx`** — switching rigs requires editing that import and rebuilding.

## Roadmap (2026-07-09 review; security items added 2026-07-27)

Phased plan derived from a full codebase review. Detailed items live in the TODO lists below.

1. **Stabilise (days)** — fix the web.py handler-registration indentation bug, the overnight-schedule bug, and the offline-install build-isolation failure; make CI actually run (PR triggers, module tests in testpaths, ruff installed and converged).
2. **Consolidate (weeks)** — extract a shared `CameraBase` from the three camera forks; split web.py into blueprints; dead-code sweep (database.py, broken NAS listing, fake login); rewrite PROTOCOL_V1.md to match reality.
3. **Harden (when touching transport)** — add `msg_id` correlation to commands (the one remaining transport gap now DEALER/ROUTER is in); supervised long-lived threads; the multi-module integration test.
4. **Strategic (only if the system outgrows one lab)** — replace Samba export with rsync/HTTP; module base-class composition refactor.

**Not scale-dependent, do sooner than phase 4**: the 2026-07-27 security review (see the Security section in TODO) found items that are risks today regardless of lab size — two hardcoded fleet-wide Samba passwords already committed to the repo, and an unauthenticated-ZMQ path to full RCE-as-root via `update_saviour` that doesn't require ever touching the web UI's login. The web UI *does* already have a real (if limited) auth system, contrary to this roadmap's original "auth on... web UI" framing — what it actually needs is TLS, rate-limiting, and closing the unauthenticated endpoints, not auth from scratch.

## TODO

Known issues and planned improvements, grouped by priority. Check these off (`- [x]`) as they are completed.

### High priority — silent data loss / correctness

- [x] **`web.py`: four Socket.IO handlers registered inside the wrong method** (found 2026-07-09 review) — everything from the `""" Recording """` comment at ~line 1521 (`get_recording_sessions`, `get_debug_data`, `login`, `remove_module`) is indented inside `broadcast_module_health()` instead of `_register_socketio_events()`. These handlers only register when the first health broadcast fires, and re-register on every subsequent call. Fix the indentation and delete the dead `login` handler while there.
- [ ] **`recording.py`: scheduled sessions cannot span midnight** (found 2026-07-09 review) — start/stop use lexicographic `"HH:MM"` comparison; a window like 22:00–06:00 stops immediately after starting (`"22:00" >= "06:00"`). Dark-cycle overnight recording is a core rodent-lab use case. Fix: detect `end < start` and treat the window as crossing midnight.
- [ ] **`pyproject.toml`: `hatchling` in build requires breaks offline module installs** (found 2026-07-09 review) — build backend is `setuptools.build_meta`, so hatchling is never used, but pip's build isolation tries to download it (and setuptools/setuptools_scm) from PyPI on every `pip install -e .`. This is the exact `ERROR: No matching distribution found for hatchling` seen in module journals. Fix: remove hatchling from requires AND use `--no-build-isolation` in mend.sh/update paths; also delete the dead `[tool.hatch.*]` sections.
- [x] **`export.py`: Samba mount not retried** — if the mount fails at session start the entire segment is never exported; add a retry loop with backoff.
- [x] **`export_queue.py`: failed exports dropped permanently** — `on_export_failed()` removes the module from `_active` without re-queuing; add retry logic so transient NAS outages don't silently lose data.
- [x] **`export.py`: `PENDING_*` rename not rolled back on copy failure** — if `shutil.copy2()` fails after `os.rename()`, the source file is left in a broken state with no recovery path.
- [x] **`export.py`: `self.exporting` flag and `self.staged_for_export` list lack thread locks** — written from recording, export, and command-handler threads simultaneously; wrap with `threading.Lock`.
- [x] **`config.py`: `_recursive_update()` modifies shared dict without a lock** — other threads can read a half-merged config; guard with a lock in `set_all()`.
- [x] **`modules.py`: config sync status transitions not atomic** — `received_module_config()` compares `target_config` and writes status in two unsynchronised steps; a concurrent `set_target_module_config()` call corrupts state.

### Security (found 2026-07-27 review)

Threat model: a local network of Raspberry Pi 5s (one controller, several modules), typically a closed lab LAN — but the controller (or a separate router) sometimes acts as the internet gateway, and devices are provisioned either by a user running `install.sh` themselves or by flashing pre-baked cloned SD card images (`scripts/multiclone.sh`) across a fleet. Several items below are only "acceptable" as long as the LAN stays closed and stop being acceptable the moment it's bridged — but the credential and RCE items are real regardless of network exposure.

- [x] **`saviour-config`: two hardcoded, fleet-wide Samba passwords committed to the repo** — `researcher` and `sidbit` (the Samba **admin user**, full read/write) had literal hardcoded passwords, identical on every deployment and permanently in git history regardless of this fix. Unlike `saviour_module` (a machine account modules authenticate with, whose password can safely regenerate every `configure_samba_share()` run), these two are human-typed logins for a researcher connecting from their own machine — so they now get a random *initial* password only the first time each account is created (written to `/etc/saviour/samba_credentials`, mode 600, retrievable via `sudo cat`), and a new "Reset Samba share password" menu item in `saviour-config` (mirroring the existing admin-password reset flow) lets an admin set a memorable one or rotate it later without silently invalidating whatever a lab has already shared with its members. Note the old strings (`getmyfiles`/`espressocreme`) are burned — anyone who already has them from git history could still try them against an unpatched (not-yet-reconfigured) existing deployment.
- [ ] **`update_saviour` is a full unauthenticated RCE-as-root primitive** — it's a `@command()`-registered ZMQ RPC (`module.py`) that takes a **caller-supplied** `controller_url`, downloads `{controller_url}/update/package` over plain HTTP with no signature/checksum check (only `zipfile.is_zipfile()`), `rsync`s it over the install directory, and restarts the service as root. Since the ZMQ command bus has no auth (below), any device that can reach a module's DEALER connection can point this at an attacker-controlled server and get arbitrary code execution as root — without ever touching the web UI's login. The web-UI-driven deploy path (`deploy_update`/`stage_current_version` in `web.py`) is properly gated behind the admin password but has the same total absence of package integrity/authenticity verification.
- [ ] **ZMQ command bus: no auth, no encryption, and an identity-hijack path** — ROUTER (5555) and PUB/SUB (5556) bind `tcp://*` with no CURVE security. `ROUTER_HANDOVER=1` means any device that opens a DEALER socket, sets its identity to a real module's `module_id`, and sends `"hello"` immediately displaces that module's routing entry — the controller's commands (including Samba credentials via `set_export_config`, `shutdown`, `update_saviour`) then go to the attacker instead of the real module. Remotely-callable commands with no auth at all include `shutdown`, `reboot`, `update_saviour`, `reset_config`, `set_export_config` (redirects a module's export to an attacker-controlled share), and rig-specific physical actuators (e.g. APA's shock/motor control).
- [ ] **Web UI: single shared password, sent and stored in plaintext, no lockout** — Flask-SocketIO is served over plain HTTP (`host='0.0.0.0'`, no TLS anywhere), so the admin password is sniffable on every login and reconnect. The frontend (`authStorage.js`) caches it in `localStorage` indefinitely and resends it on every reconnect. `handle_login` has no rate-limiting/lockout and never logs failed attempts, so once an operator sets a memorable password (min length is only 8 chars) nothing slows down guessing it. `cors_allowed_origins="*"` widens this further.
- [ ] **Several endpoints leak fleet data with no auth at all** — `/facade/list_modules`, `/facade/module_health`, `/facade/exported_recordings`, and `/update/package` (the staged update zip itself) have no auth check, unlike `/facade/send_command`. `get_bug_report` is also unauthenticated, and its `bug_report_ready` event (carrying the diagnostics-zip download token) is broadcast to **every connected socket** instead of just the requester (missing `room=request.sid`, inconsistent with every other emit in the file) — any guest gets a live link to a bundle of raw, unredacted `journalctl` output. If "stage current version" is ever used, its file walk doesn't exclude `active_config.json`, so a live Samba/Teams-webhook credential could ride along in that same unauthenticated `/update/package` response.
- [ ] **No transport encryption on the Samba/CIFS export** — mount commands (`export.py`, `web.py`) never set the SMB3 `seal` option, so recorded research data — and the mount credentials themselves, briefly visible via `ps`/`cmdline` while the mount command runs — cross the LAN in plaintext.
- [ ] **All network services bind to `0.0.0.0` with no interface scoping** — ZMQ ROUTER/SUB and the Flask/SocketIO server listen on every interface. If the controller ever doubles as the internet gateway, the unauthenticated command bus and the sniffable-password web UI become reachable from the WAN side too, not just the PoE LAN. No firewall/iptables rule scopes any of this to `eth0` only.
- [ ] **Fleet image cloning never rotates the OS user's password/SSH keys** — `multiclone.sh` correctly regenerates SSH *host* keys and machine-id automatically for every cloned device, and `clone_prep.sh` covers the same for out-of-pipeline duplication (only if an operator remembers to run it manually). Neither `install.sh`, `setup.sh`, nor any clone script touches the OS user's login password or `~/.ssh/authorized_keys` (`clone_prep.sh` documents leaving `authorized_keys` unchanged as *intentional*) — whatever credential the master image's imager set is shared, unrotated, across every device cloned from it.
- [ ] **`push_credentials.sh` disables SSH host-key checking** (`StrictHostKeyChecking=no`) when pushing Samba credentials to a module, and passes the password as a literal SSH command-line argument (briefly visible via `ps`/`/proc/<pid>/cmdline` on the module during the push).
- [ ] **`saviour.service` runs fully as root with no systemd hardening** — no `NoNewPrivileges`, `ProtectSystem`, or capability bounding, so every finding above has an unrestricted-root blast radius rather than a scoped one. (Reasonable as-is for `ptp4l`/`phc2sys`, which need raw clock hardware access — not obviously necessary for the whole ZMQ/web/update surface.)
- [ ] **Frontend dependency vulnerabilities** — `npm audit` currently reports high-severity issues (react-router, transitive via react-router-dom); no equivalent Python-side scanning (`pip-audit`) exists in CI.

### Medium priority — reliability / UX

- [x] **CI never runs on PRs / lints with flake8 / excludes examples / testpaths gap** (found 2026-07-09 review) — all four resolved: PR triggers were already present on `main`/`staging`/`develop`; CI now runs `ruff` (dev dependency, converged from flake8); `src/modules/examples` is no longer excluded (fixing this required correcting `[tool.ruff] target-version` from the false `py38` to `py311` — the old value made `match` statements register as syntax errors); `testpaths` already included `src/modules/tests`. One real bug the newly-included directory caught immediately: `src/modules/examples/arduino/arduino_module.py` subclassed `Command` without importing it — fixed. Note `arduino_module.py` has other latent issues beyond that (e.g. `ArduinoCommand` methods reference `self.callbacks`, which nothing ever sets — the base `Command` class populates `self.commands`) not yet addressed.
- [ ] **`web.py:400`: broken timestamp format in legacy `send_command start_recording` path** (found 2026-07-09 review) — `strftime("%Y%M%d_%H%m%s")` has month/minute swapped and non-portable `%s`; should be `"%Y%m%d_%H%M%S"`.
- [ ] **`web.py`: NAS exported-recordings listing is broken** (found 2026-07-09 review) — `get_nas_recordings()` scans `/mnt/nas` but calls `mount_nas()` which mounts at `/mnt/controller_export`, so the scan never sees the mount. Also logs one INFO line per file found — a journal flood on large shares. Feature appears dead; either fix the mount point or remove it.
- [ ] **`pyproject.toml` hygiene** (found 2026-07-09 review) — `requires-python = ">=3.8"` is false (code needs 3.11+); `pytest` is a runtime dependency (belongs in dev extras only). (The `[tool.ruff]` deprecated top-level `select`/`ignore` part of this is now fixed — moved to `[tool.ruff.lint]`.)
- [x] **`export.py` / `module.py`: blocking subprocess calls on network thread** — `_mount_share()` has no timeout and `update_saviour()` blocks ZMQ command processing; move to background threads.
- [x] **`config.py`: `set()` fires `on_module_config_change()` even when value is unchanged** — guard with an equality check before calling `configure_module()`.
- [x] **`config.py`: `reset_to_defaults()` doesn't purge stale keys** — keys removed from the module config file persist in `active_config.json` after a reset; rebuild from scratch rather than merging.
- [x] **`web.py`: `_`-prefixed (internal) config keys not filtered on inbound socket events** — the frontend can overwrite `_communication.*`, `_codec`, etc.; apply `filterPrivateKeys` equivalent server-side before merging.
- [x] **`modules.py`: online/offline status can oscillate without hysteresis** — a single delayed heartbeat immediately brings a module back online; add a short debounce (e.g. require 2 consecutive heartbeats before marking online again).
- [x] **`controller/network.py`: infinite loop waiting for `nmcli`** — if NetworkManager is not running the controller hangs at startup; add a timeout and a clear error message.
- [x] **Session metadata not retried if NAS unavailable at session start** — refactored into `_try_write_metadata()` (returns bool) and `_retry_write_metadata()` (background thread, backoff 30 s → 1 min → 2 min → 5 min → 10 min); `_write_session_metadata()` spawns the retry thread on first failure.
- [x] **`facade.py`: `apply_section_to_type` has no ack timeout** — bulk config pushes that are never acknowledged leave the frontend in a permanent "pending" state.

### Low priority — observability / maintenance

- [ ] **No correlation IDs on ZMQ commands** — matching a `cmd_ack` to its originating command is impossible under concurrent load; add a `msg_id` round-trip in the command envelope.
- [ ] **Dead code sweep** (found 2026-07-09 review) — `controller/database.py` (never instantiated), `export.py` `unmount()` (references undefined `self.current_mount`) and `_ensure_export_folder_exists()` (calls `_create_export_path()` with no args — TypeError), `web.py` inbound `module_status` socket event (frontend should never send module status — the TODO comment on it agrees). Note: the hardcoded `admin`/`secret` `login` handler this item used to flag is gone — replaced by a real (if still limited, see Security section) shared-password system.
- [ ] **`module.py` logging: computed `format_string` never used** (found 2026-07-09 review) — `logging.basicConfig` at module import hardcodes the systemd format, so module logs lose timestamps when run outside systemd. `controller.py` does the same thing correctly — copy that.
- [ ] **`docs/PROTOCOL_V1.md` is stale** (found 2026-07-09 review) — documents PUB/SUB transport + JSON envelope + msg_id ack/retry that were never built. Rewrite to describe the actual DEALER/ROUTER string protocol, or keep as the design target for the correlation-ID work and label it clearly as such. `docs/CONFIG_STRUCTURE.md` (Aug 2025) likely also needs a pass.
- [x] **`phc2sys_offset` field had no unit suffix** — renamed to `phc2sys_offset_ns` across `src/shared/health.py`, `src/modules/ptp.py`, `src/modules/health.py`, `src/controller/health.py`, `src/controller/ptp.py`, `src/controller/recording.py`, and `src/tests/test_ptp_parsing.py`. Note: exported health CSV column name changes accordingly — update any downstream analysis scripts that reference `phc2sys_offset` by name.
- [x] **Hardcoded IP ranges in three files** — `network._valid_ip_prefixes` added to both `base_config.json` files; `controller/network.py` reads from config; dead `valid_ips` list removed from `modules/network.py`; redundant `"10.0.0.1"` fallback removed from `export.py` (base config is the authoritative default).
- [x] **`switch_role.sh`: `ROLE=` / `TYPE=` values written without sanitisation** — `switch_role.sh` is now a deprecated shim that execs `saviour-config`. In `saviour-config`, ROLE and TYPE values are set exclusively from fixed whiptail menu selections; there is no free-text input path to `write_config`, so injection is not possible.
- [x] **`setup.sh`: `imx500-all` blocks install on devices without Pi AI camera repo** — moved to `OPTIONAL_PACKAGES`; failures warn but do not abort. Removed `apt-get upgrade -y`.
- [x] **Module version stays stale after restart** — `update_service` and `add_service` both call `zeroconf.get_service_info()` for fresh properties, construct a new `Module` object (including updated `version`), and pass it to `module_discovery()` → `add_module()` which replaces the stored entry wholesale.

### Architectural concerns

These are larger structural issues that require significant refactoring. Recorded here so they are not lost.

- [x] **PTP sync unvalidated before recording** — added `_check_ptp_sync()` gate in `create_session()` and `_start_scheduled_session()`; "Check Ready" now runs a controller-side PTP check and surfaces results to the frontend (240626). Gate checks `ptp4l_offset` and `phc2sys_offset` (both < `ptp_threshold_us`, default 50 µs). Note: `phc2sys_freq` absolute magnitude is NOT gated — settled crystals run at 20–30 kppb permanently; what matters is inter-camera difference (see hardware gotchas).
- [x] **Mid-recording PTP degradation undetected** — `_check_ptp_mid_recording()` runs each monitor cycle for ACTIVE sessions; fires on transitions only (newly degraded / newly recovered); surfaces amber `ptp_warning` field on the session card and sends a Teams alert (240626).
- [x] **Session state has no durability** — already implemented: `_save_sessions()` is called at every state transition; `_load_sessions()` on startup marks interrupted ACTIVE sessions as ERROR; `module_back_online()` re-issues `start_recording` and recovers ERROR → ACTIVE when modules reconnect; `handle_module_health_response()` handles the controller-restart case by probing module state and resuming or marking stopped accordingly.
- [x] **ZMQ PUB/SUB is the wrong transport for commands** — PUB/SUB drops messages to subscribers that haven't connected yet (slow-joiner problem). `start_recording` can silently drop and a session starts on some modules but not others, with no timeout or error surfaced. Commands requiring reliable delivery should use DEALER/ROUTER or REQ/REP. High effort — transport-layer change across every module.
- [ ] **Module base class is a god object** — `module.py` (~1275 lines) owns config, export, PTP, recording, health, network, commands, and lifecycle. No concern can be tested in isolation; contributors must understand the entire base before writing a single sensor. High effort — requires composition refactor across all module types.
- [x] **Camera module family is three parallel forks** (found 2026-07-09 review, fixed 2026-07-23) — extracted `src/modules/camera_base.py`: `CameraBase(Module)` owns Picamera2 lifecycle, `_configure_camera`, the MJPEG streaming server, segmented recording, and the timestamp-CSV sidecar, exposing `_process_main_frame`/`_process_lores_frame`/`_after_frame_hook`/`_configure_module_extra` hooks plus `CSV_EXTRA_COLUMNS` for subclass-specific work. `camera_module.py` (1207→47 lines), `apa_camera_module.py` (1208→590), and `loom_camera_module.py` (1707→1067) all now subclass it. Unifying also fixed real gaps: hflip/vflip and `camera.sync_mode` now work for apa/loom (previously silently ignored), and loom's `_configure_camera` exception handler now rebuilds encoders on failure like the other two always did. CSV schema and per-frame draw order were deliberately unified across all three — **not yet hardware-verified**; run the manual smoke-test checklist (recording, MJPEG stream, sensor modes, sync_mode pairing, rotation/hflip/vflip, and for apa/loom detection/tracking + shock-zone/crossing events, plus loom's HDMI stimulus) on real rig hardware before this reaches a live experiment.
- [ ] **`web.py` is a 2000-line grab-bag** (found 2026-07-09 review) — sessions, config sync, chunked update uploads, deployments, bug-report collection, NAS probing (three duplicated mount/umount snippets with different mount points), time setting, and power control all live in two giant registration methods. Split into Flask blueprints / per-concern services. The indentation bug above is a direct symptom.
- [ ] **Unsupervised threading + broad exception policy** (found 2026-07-09 review) — 59 ad-hoc `threading.Thread(daemon=True)` spawns and ~280 `except Exception` handlers. A crashed monitor/retry thread dies silently and its job simply stops happening. Consider a tiny supervised-thread helper (log-and-restart) for the long-lived loops, and narrower exceptions on the data path.
- [ ] **Frontend variant selection is a source edit** (found 2026-07-09 review) — `main.jsx` hardcodes `import App from './loom/App'`; deploying to a different rig type silently ships the wrong UI unless the import is remembered. Make it build-time (`VITE_VARIANT` env → `import.meta.env`) or runtime (controller config → dynamic import).
- [ ] **Samba is the wrong export transport** — designed for Windows interoperability; adds credential management, mount failure modes, and an unreliable driver stack on a homogenous Linux PoE network. `rsync` over SSH or a simple HTTP PUT endpoint would be simpler and easier to debug. The complexity of `export.py` (PENDING rename, staged lists, thread locks) partly compensates for Samba fragility. High effort — requires rewriting all export logic.
- [x] **Health schema is duplicated** — canonical `ModuleHealthSnapshot` dataclass lives in `src/shared/health.py`; both `src/modules/health.py` and `src/controller/health.py` import from it.
- [ ] **No authentication on the command bus** — see the Security section (2026-07-27 review) above for the full detail, including the identity-hijack path and the unauthenticated-RCE chain this enables via `update_saviour`.

### Tests

- [x] **Config merge has no unit tests** — `_merge_defaults`, `_merge_dicts`, `_merge_internal_defaults`, and `reset_to_defaults` are all untested; add `pytest` cases covering each merge path and edge cases (stale keys, `_`-prefix re-application).
- [x] **Export pipeline has no unit tests** — `src/modules/tests/test_export.py` and `src/controller/tests/test_export_queue.py` cover PENDING rename/rollback, mount retry, concurrency guard, retry-on-failure, stale dispatch timeout, delete_on_export, and queue persistence.
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

### Camera framesync (multi-camera timing)

The camera module supports `camera.sync_mode: "server" | "client" | "none"`. This uses **libcamera's software sync mechanism**, not GPIO. The server broadcasts timing packets over UDP; clients adjust their framerate to match. Key facts:

- **`SyncTimer` metadata**: counts down (in µs) to the agreed sync point, then goes negative. A very negative value (e.g. −26 seconds) means sync was established long before recording started — not an error.
- **`SyncReady` metadata**: True on the single frame where synchronisation fires. The encoder's `sync_enable = True` flag discards frames until this fires, then starts recording. `SyncFrames` is set in `_pre_create_first_segment()` to force a fresh sync point close to T=0. The per-frame timestamp CSV is opened in `_start_new_recording()` (not `_pre_create_first_segment()`) so CSV row 0 always corresponds to video frame 0.
- **Phase offset**: Even after sync-lock, there is a fixed per-session inter-camera phase offset (typically 0–8333 µs at 120 fps). This is a hardware characteristic of when the client's frame clock happened to be when sync was established — **not** a PTP error. It is constant within a session and can be calibrated out from the `framesync_per_frame.csv` sidecar.
- **120 fps limitation**: libcamera sync requires the target framerate to be significantly below the camera's maximum so the client can speed up to catch the server. At 120 fps on Pi Camera Module 3 (which maxes at ~120 fps at the recording resolution), the client has no headroom and cannot phase-lock. The `sync_enable` / `SyncFrames` approach is still used (best-effort), with a 2-second fallback timeout.
- **PTP two-servo rule**: `ptp4l` disciplines the PHC; `phc2sys` disciplines `CLOCK_REALTIME`. The PTP gate in `recording.py` checks both `ptp4l_offset` and `phc2sys_offset` (both must be < `ptp_threshold_us`, default 50 µs). `phc2sys_freq` (the frequency correction in ppb) reflects the crystal oscillator's natural offset and is typically 20,000–30,000 ppb on settled hardware — **this is normal and should not be gated on**. What matters is the *difference* between cameras' freq values, not the absolute magnitude. Wait at least 5–10 minutes after a camera reboot before recording for phc2sys to converge its frequency estimate to the correct value for that crystal.
- **Framesync analysis**: `tools/analyse_framesync.py` reads per-session timestamp CSVs and reports inter-camera offset statistics including clock drift (µs/sec) and detrended jitter (the true timing noise floor once slow PTP drift is removed). Run with `source env2/bin/activate` (needs pandas). The "mean offset" includes the fixed phase offset; the **detrended p95** is the meaningful accuracy figure (<20 µs with settling PTP, <5 µs when fully converged).
