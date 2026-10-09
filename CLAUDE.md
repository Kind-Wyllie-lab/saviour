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
pytest src/controller/tests/test_facade.py   # single file

# Lint — ruff is a dev dependency (pip install -e ".[dev]") and is what CI runs
ruff check .

# Type check (aspirational — strict mypy config has never passed)
mypy src/
```

`ruff check .` is a CI gate (rule set and the reasons for each ignore in `pyproject.toml`); keep it at zero. Use `# noqa: <code> -- <why>` only where the pattern is intended.

### Frontend (React/Vite)

```bash
cd src/controller/frontend
npm run dev          # dev server (proxies /socket.io to Flask on port 5000)
npm run dev:mock     # run the UI with an in-memory fake socket, no controller needed
npm run build        # production build → dist/
npm run lint
```

**After making any frontend change, run `npm run build`** so the user can review the result (they don't run a dev server themselves). If it fails with `EACCES`/`unlink` errors under `dist/`, that's the known root-owned-`dist/` issue — `sudo chown -R $(whoami) src/controller/frontend/dist` then rebuild, rather than deleting `dist/` outright.

### Analysis tools

```bash
# env2 is a second venv for analysis scripts that need pandas
# (numpy IS in the main env; only pandas is omitted to keep module installs light)
source env2/bin/activate

# Framesync analysis — compare per-frame timestamp CSVs from a session date dir
python3 tools/analyse_framesync.py /path/to/session/date_dir

# Frame-aligned side-by-side video — checks PTP quality, strips pre-stage frames,
# computes per-camera skip, calls ffmpeg
python3 tools/make_aligned_video.py /path/to/session [--output out.mp4] [--layout side|stack|grid]

# Compose a layout video from a session's cameras — no PTP sync_mode required,
# aligns by each camera's own per-frame timestamp; OpenCV only, no ffmpeg dep
python3 src/controller/video_compose.py /path/to/session/date_dir [--output out.mp4] [--layout auto|loom]
```

```bash
# Automated camera-config × framesync sweep — needs only `requests` + key-based
# SSH to the controller (no env2). Drives PATCH /api/v1/modules/<id>/config +
# the session lifecycle, SSH-pulls each session's framesync_report.json /
# _recording.json, ffprobes the .ts, writes results.csv + a mean/sd pivot.
# Snapshots + restores both cameras' config. Used to characterise the
# hailo-inference-load vs sync-client-frame-drops relationship.
SAVIOUR_TOKEN=<admin-pw-or-token> python tools/framesync_sweep.py \
    --repeats 5 --fps 30 --duration-min 1 --out ./sweep_run1
```

### Installation & role assignment

```bash
./setup.sh              # full system setup (run once per device)
sudo saviour-config     # assign role (controller | module) and type — interactive TUI
```

## Architecture

### Two-role system

Every device runs one of two roles, set in `/etc/saviour/config`:

- **Controller** (`src/controller/`) — PTP grandmaster, mDNS service discovery, ZeroMQ command hub, Flask+SocketIO web interface on port 5000, recording session orchestration, file export queue to Samba/NAS.
- **Module** (`src/modules/`) — PTP slave, registers via Zeroconf, connects to the controller's ZeroMQ sockets, records to `/var/lib/saviour/recordings`, exports files to the controller's Samba share.

Concrete implementations live under `src/controller/variants/` and `src/modules/variants/`. Each subclasses the abstract `Controller` or `Module` base class.

### Inter-service communication

ZeroMQ **ROUTER/DEALER** for controller→module commands; modules publish status/heartbeats on PUB/SUB:

- Controller binds a ROUTER socket (port 5555); each module connects a DEALER socket with its `module_id` as the ZMQ identity and sends a `"hello"` frame on connect to register. Tracked in `_connected_dealers`; heartbeat timeout evicts a module via `remove_dealer()`.
- Modules publish on `status/<module_id>` (PUB, port 5556); controller SUBs to all topics.
- **Actual wire format**: commands are plain strings `"{command} {json_params}"` routed by identity; status messages are JSON dicts with `type`, `timestamp`, `module_id`, `module_name` plus type-specific fields. No envelope, no `msg_id`, no ack correlation.
- ⚠ `docs/PROTOCOL_V1.md` is **aspirational, not descriptive** — it documents PUB/SUB `cmd/` topics and a `msg_id`/ack/retry envelope that were never implemented. Do not use it as a reference for the current protocol.

### Config layering

JSON config is merged in three layers: `base_config.json` → `active_config.json` → `.env` overrides. Keys prefixed with `_` are internal defaults not meant to be user-overridden. The `Config` class in `config.py` handles this for both sides.

### Module command system

Module methods decorated with `@command()` are auto-registered as remotely callable RPCs. `@check()` registers status/health reporters. Commands are dispatched by the `Communication` class when a matching message arrives.

### Frontend↔backend

The React frontend communicates with Flask exclusively via **Socket.IO** (not REST). The Flask server emits module state, health, and recording events; the frontend sends commands back as Socket.IO events. The Vite dev server proxies `/socket.io` to `localhost:5000`.

**REST API for external programs** (not the frontend): `src/controller/rest_api.py` is a Flask blueprint at `/api/v1` — a thin bearer-authed layer over the same `ControllerFacade` the Socket.IO handlers use, for an experiment controller (pyControl etc.) on the LAN to read state or start/stop recordings. Bearer token = the shared admin password; plain HTTP (no TLS), consistent with the threat model. Full reference: `docs/REST_API.md`. The older `/facade/*` routes (incl. `send_command`, the arbitrary-command escape hatch `/api/v1` omits) are unchanged.

The frontend variant (basic / loom / apa / habitat / acoustic_startle) is selected at build time via the `VITE_VARIANT` env var, which `saviour-config`'s `build_frontend()` writes; `vite.config.js` resolves the `virtual:active-app` alias to `src/${variant}/App.jsx`. Switching rigs means re-running the build with a different `VITE_VARIANT`, not editing source. `basic` doubles as the shared component library — `apa`/`habitat` `App.jsx` import pages straight from `../basic/`.

### Key source files

| File | Purpose |
|------|---------|
| `src/controller/controller.py` | Abstract `Controller` base class |
| `src/controller/facade.py` | `ControllerFacade` — internal API for intra-component calls |
| `src/controller/web.py` | Flask server + all Socket.IO event handlers (~4,600 lines; blueprint split is post-v1.0) |
| `src/controller/rest_api.py` | `/api/v1` Flask blueprint — bearer-authed REST for external experiment controllers (see `docs/REST_API.md`) |
| `src/controller/system_update.py` | Self-update primitives (git fetch/reset as the checkout owner, stage-zip, snapshot, module notify, build+restart) — used by `POST /api/v1/system/update` and the web UI update handlers |
| `src/controller/modules.py` | Tracks discovered module states |
| `src/modules/module.py` | Abstract `Module` base class (god object — config, export, PTP, recording, health, network, commands, lifecycle) |
| `src/modules/facade.py` | `ModuleFacade` |
| `src/modules/export.py` | Samba-based file export, config export, traffic shaping |
| `src/modules/config.py` | Config layering: base → active, `set_all`, `save_active` |
| `src/modules/camera_base.py` | Shared camera base (recording, framesync, MJPEG preview) |
| `src/modules/variants/microphone/microphone_module.py` | AudioMoth recording + monitoring stream |
| `src/modules/variants/template/` | Boilerplate for a new module type |

### Module types

`camera`, `microphone`, `ttl`, `rfid`, `apa_camera`, `apa_arduino`, `sound`, `lightning_camera`, `habitat_camera`, `hailo_camera`, `basler_camera` — each under `src/modules/variants/<type>/`.

## Conventions

- **Conventional commits** with `feat/`, `fix/`, `refactor/` branch prefixes. Branch flow: PRs → `staging` → `main`.
- Python line length: 88 (ruff). Code requires **3.11+** (`match`, `StrEnum`, numpy ≥ 2.2) despite pyproject claiming py38; all deployed Pis run Bookworm/3.11+.
- Systemd-aware logging: timestamps are skipped when `INVOCATION_ID` is set (systemd sets this).
- PTP log parsing in `src/*/ptp.py`; health metrics in `src/*/health.py`.
- `src/__version__.py` is written by a **pre-commit hook** (`git describe`) so ZIP deploys carry the version — inherently one commit behind; never "fix" this with a manual bump. Hook is tracked at `scripts/git-hooks/pre-commit`; each clone must run `git config core.hooksPath scripts/git-hooks` once. If the version goes stale again, check `core.hooksPath` is set on whatever machine is committing.

## Project status & threat model

Currently **v0.10** (latest tag), targeting **v1.0 = "safe to run unattended on a closed lab LAN without silent data loss or easy compromise"**, not "hardened for internet exposure". v1.0 deliberately excludes the big structural refactors (`web.py` blueprint split, Samba→rsync, module god-object composition refactor). Exit checklist: `plans/v1.0-roadmap.md` (test D, a 72 h fault-injection soak on the desk fleet, is the release gate).

**Threat model (settled 2026-08-25):** **LAN access is the trust boundary**: anyone who can reach the network is treated as authorized, the same as SSH/physical console access. The web UI's guest/admin split stops an operator *accidentally* breaking a running experiment or leaking data, **not** a malicious LAN-resident actor. So `update_saviour` package-signature verification and the ZMQ identity-hijack path are **deliberately deferred**. A credential committed to git history is compromised regardless; those are fixed separately. The tailnet is only the owner's machines; controller services bind eth0 only (`interface.listen_on`), wlan0 is default-deny (`docs/NETWORK_FIREWALL.md`). Reach the web UI over Tailscale with `tailscale serve --bg http://10.0.0.1:5000`.

**CI:** `python-app.yml` (ruff gate + pytest) and `frontend.yml` (`npm run build` × 5 variants + `npm run lint`) run on push/PR to `main` and `staging`; `build` and `frontend-build` are required checks on `main`.

**Where things live:** completed work → **[docs/CHANGELOG.md](docs/CHANGELOG.md)** (write it up there when done). Plans for features / non-trivial fixes → one file each in **[plans/](plans/)**. Open items without a plan → **[plans/backlog.md](plans/backlog.md)**. Full hardware findings → **[docs/HARDWARE_NOTES.md](docs/HARDWARE_NOTES.md)**.

## Open work (index; detail in `plans/backlog.md` unless a plan is named)

**v1.0 / data loss**
- Recording liveness gaps: no severity ramp for sustained silence, RFID has no signal, TTL override not on-device verified (roadmap A1).
- Gap record: gaps are stamped at detection, not at loss → `plans/metadata-gap-record.md`.
- No PTP start gate on a `module_back_online` re-arm after a power-loss reboot.
- Export deletes the local copy with no content check; no per-file hashes → `plans/recording-file-hashing.md`.
- `_check_ptp_health` restart-loop fix owes a 24 h habitat validation (roadmap A8).
- `pyproject.toml`: `hatchling` in build-requires breaks offline `pip install -e .` on modules; also `requires-python`/mypy say 3.8, `pytest` is a runtime dep.
- Recorded rotation at 90°/270° is dropped for non-square resolutions (hardware limit); decided: rotate downstream (frontend / `video_compose`), not built.
- Legacy string command path: `start_recording duration=60` → `TypeError`; `web.py` legacy `send_command start_recording` has a broken `strftime`.
- Sync-client camera lags the server in compose (CSV rows for discarded pre-`SyncReady` frames) → `plans/multicam-frame-alignment-and-sync-provenance.md`.
- `habitat_camera`: shipped motion-trigger defaults catch 0/28 real events; a clip mid-trigger at session start exports with the next session.

**Security (closed-LAN caveats; see threat model)**
- Web UI: one shared plaintext password, no lockout, `cors_allowed_origins="*"` → `plans/remote-access-auth-hardening.md`.
- Module MJPEG stream (`:8080+`) unauthenticated on `0.0.0.0` → `plans/mjpeg-stream-auth.md`.
- No SMB3 `seal` on CIFS; clones share the OS login credential; `push_credentials.sh` disables host-key checks; `saviour.service` runs as root unhardened; modules have no firewall.
- GitHub settings reminders (secret scanning, deletions on `main`, …): `plans/backlog.md`.

**Reliability / UX**
- Config changes while `ACTIVE`: `save_controller_config` still ungated; frontend has no `module_config_error` listener.
- Session list multi-select delete + the rapid-delete 404 race → `plans/session-list-multi-select-delete.md`.
- TTL: edge timestamps on the Python callback path → `plans/ttl-kernel-timestamping.md`; hardening work → `plans/ttl-module-hardening.md`.
- pyControl visibility → `plans/pycontrol-live-event-bridge.md`.
- Frontend: `FaultAlertModal` missing from acoustic_startle; per-module "not ready" reasons hidden in 4 variants; bulk Update/Reboot All need a home on `System.jsx`; preview FPS overlay shows sensor rate.
- `saviour-config`/`mend.sh` build the frontend as root (root-owned `dist/`); `clone_prep.sh` clears the wrong active-config path.
- A/V sync residual (~150-200 ms audio): `plans/audio-video-sync-residual-validation.md`; buzzer/LED rig built, hardware pending (`docs/AV_SYNC_TEST.md`).

**Maintenance / architecture**
- Pre-v1.0 behaviour-neutral cleanup → `plans/pre-v1-codebase-cleanup.md` (in progress).
- No ZMQ correlation IDs; `docs/PROTOCOL_V1.md` stale; small latent bugs listed in the backlog.
- After v1.0: `Module` god object, `web.py` blueprints, Samba → rsync, invariant variant launcher, NWB export, telemetry, habitat-scale UI (design docs in `docs/`).
- In-flight / unvalidated-on-hardware features (REST API under load, compose/ephys, basler, occupancy trigger, cadence check, ML training pipelines): `plans/backlog.md` → "In-flight".

## Hardware gotchas (condensed; full findings in `docs/HARDWARE_NOTES.md`)

- **AudioMoth:** the USB device name encodes the sample rate, so after `configure_audiomoth()` any stored PulseAudio device ID is stale; re-discover. Usable bandwidth is well below Nyquist (~5 kHz at 48 kHz, ~70 kHz+ at 192 kHz); 192 kHz is the only rate for USV work. Monitoring and recording are separate recorders on one device by design.
- **Controller clock:** `phc2sys` disciplines it, so `timedatectl set-time` fails while NTP is on; `set-ntp false` → set → `set-ntp true` in a try/finally.
- **Hailo:** `dkms` must be installed before `hailort-pcie-driver` (ordered in `variant.conf`). Fix a broken Pi with `apt install dkms && apt install --reinstall hailort-pcie-driver && reboot`. Diagnose: `lspci | grep -i hailo` → `lsmod` → `dmesg` → `dkms status`; confirm 8 vs 8L with `hailortcli fw-control identify`.
- **Pi 5 PoE power:** NVMe controllers need `nvme_core.default_ps_max_latency_us=0` (APST wedges the drive); every Pi needs `PSU_MAX_CURRENT=5000` in the EEPROM (PoE HATs never negotiate USB-PD). `setup.sh`/`mend.sh` apply both; `mend.sh` writes `/run/reboot-required` and exits 10 when a reboot is needed (`--reboot` to do it).
- **journald:** RPi OS ships a `Storage=volatile` drop-in; ours is `/etc/systemd/journald.conf.d/99-saviour.conf`. Every session exports a journal snapshot; diagnostics bundles include the previous boot.
- **"Pi needed a power cycle" checklist:** `journalctl --list-boots` → `journalctl -b -1 -k -g nvme` → `vcgencmd get_throttled` → `rpi-eeprom-config | grep PSU_MAX_CURRENT` → NVMe `power/control` and `default_ps_max_latency_us`.
- **Background OS jobs on modules:** PackageKit (the desktop panel's daily update check) is masked and the apt-daily timers disabled (`saviour-config`, `mend.sh`): they stalled capture on a loaded camera. Watch for any new daily job when chasing a time-of-day stall.
- **Offline detection:** ungraceful disconnects send no mDNS goodbye; the 90 s heartbeat timeout (`modules.py`) is the only signal, and only after a first heartbeat.
- **PTP topology:** fleet switches are plain L2 (no transparent clock), so hop count and bulk traffic add uncorrected jitter. Fix by wiring, never code: controller + share on one switch, ≤2 hops to any module, no third switch in the PTP path (link habitat switches SFP-to-SFP), building uplink on an edge port; beyond that, PTP transparent-clock switches.
- **Camera framesync** (`camera.sync_mode` server/client/none, libcamera software sync over UDP):
  - Kept on by default (2026-10-02). With libcamera 0.5.2 a client whose server vanishes blocks in `recvfrom()`; a later `Camera.stop()` then freezes the process holding the GIL. The systemd watchdog (`WatchdogSec=60`, `src/shared/sd_watchdog.py`) restarts it; the controller re-arms and records the gap. FrameSync reconcile is deferred while a camera records.
  - `SyncTimer` very negative = synced long ago (fine). A fixed per-session inter-camera phase offset (0-8333 µs at 120 fps) is normal; calibrate from `framesync_per_frame.csv`. At 120 fps on Camera Module 3 the client can't phase-lock.
  - PTP start gate: `recording.ptp_start_gate_us` (50 µs) on both `ptp4l` and `phc2sys` offsets; `ptp_threshold_us` is only the mid-recording warning. `phc2sys_freq` of 20-30k ppb is normal; don't gate on it. Wait 5-10 min after a reboot.
  - CPU/NPU load (e.g. `hailo.infer_every_n=1`) costs only the sync *client* frames. Re-check `framesync_report.json` / `tools/analyse_framesync.py` (detrended p95 is the accuracy figure) after changing fps, sensor mode, `sync_mode` or hailo model; `tools/framesync_sweep.py` automates sweeps.
