# SAVIOUR Habitat Controller — Session Handover
*Written 2026-07-03 for Claude Code instance on habitat-controller-3590*

## Current situation (as of ~14:xx July 3)

The PoE switch was rebooted. 8 of 20 modules are still unreachable at the network layer.
The recording system is currently idle (stopped before switch reboot).

---

## Ping sweep results

### UP (12/20)
| Module ID | Name | IP |
|-----------|------|----|
| camera_d540 | A2 | 192.168.1.129 |
| camera_98b7 | A4 | 192.168.1.130 |
| microphone_9ec9 | Col_2 | 192.168.1.141 |
| camera_9d0d | B3 | 192.168.1.146 |
| microphone_9acd | Col_4 | 192.168.1.149 |
| microphone_999e | Col_3 | 192.168.1.167 |
| camera_d165 | C1 | 192.168.1.169 |
| camera_d569 | C3 | 192.168.1.170 |
| camera_d62d | B1 | 192.168.1.172 |
| camera_d443 | A1 | 192.168.1.196 |
| camera_d586 | A3 | 192.168.1.198 |
| camera_d589 | C2 | 192.168.1.201 |

### DOWN (8/20) — likely a specific switch port bank
| Module ID | Name | IP |
|-----------|------|----|
| camera_a2d2 | D4 | 192.168.1.133 |
| camera_d549 | B2 | 192.168.1.137 |
| camera_34ec | D2 | 192.168.1.148 |
| camera_3533 | B4 | 192.168.1.154 |
| camera_340b | D3 | 192.168.1.179 |
| microphone_9af1 | Col_1 | 192.168.1.185 |
| camera_34aa | D1 | 192.168.1.210 |
| camera_33ff | C4 | 192.168.1.232 |

Pattern: ALL D-row cameras down. B2 + B4 down. Col_1 mic down.
Hypothesis: these share a PoE switch port block that didn't recover.

---

## Dashboard anomalies

The web UI (`http://localhost:5000`) is showing some modules in wrong groups:
- `camera_d569` (C3) appearing in the wrong place — it also lost its name config
- `A1` appearing under group "camera" — this may be `microphone_9af1` (Col_1)
  with corrupted config, or a display artefact

Modules known to be UP at network layer but potentially not showing in UI yet:
run `python3 tools/snapshot_labels.py` to see the current controller-side view.

---

## Config losses (happened when services restarted before switch reboot)

These were stored in `/etc/saviour/module/active_config.json` on each module Pi:

1. **camera_d569 (C3)** — lost `module.name = "C3"` (now shows as `camera_d569`)
2. **microphone_9af1 (Col_1)** — lost all `audiomoth_labels`
3. **microphone_9ec9 (Col_2)** — lost all `audiomoth_labels`
4. **microphone_9acd (Col_4)** — lost all `audiomoth_labels`

A restore script with the correct values (from journal logs at 10:11) is at:
`/tmp/restore_labels.py`

Run it once the modules are back and the controller can reach them:
```bash
source env/bin/activate
python3 /tmp/restore_labels.py
```

Then verify with:
```bash
python3 tools/snapshot_labels.py
```

---

## Code changes made this session (already committed)

All on branch `high_fps`. Key changes:

### `src/controller/recording.py`
- Added 2-strike debounce before declaring a module "not recording" → ERROR
- Prevents false-positive ERROR during 60-minute segment transitions
- `_not_recording_strikes` dict: `(session_name, module_id) → consecutive miss count`
- Threshold: `_NOT_RECORDING_STRIKES_THRESHOLD = 2`

### `src/controller/web.py`
- Fixed cmd_ack WARNING spam: unrecognized command acks now log at DEBUG not WARNING
- Enabled REST facade: `self.rest_facade = True` (was `False`)
- REST endpoints: `GET /facade/list_modules`, `POST /facade/send_command`

### `mend.sh`
- Git fetch falls back to HTTPS if SSH fails (no key on habitat controller)
- Frontend build detects controller role from systemd journal if `/etc/saviour/config` missing

### `tools/snapshot_labels.py` (new)
- Prints camera/mic/audiomoth label tables from running controller
- Writes timestamped JSON archive to `tools/label_snapshot_YYYYMMDD-HHMMSS.json`

### `tools/ping_sweep.sh` (new)
- Pings all 20 module IPs and reports UP/DOWN with names

---

## Key architecture reminders

- Controller: Flask + SocketIO on port 5000, ZMQ PUB/SUB for module commands
- Modules register via mDNS (Zeroconf); heartbeat timeout = 90s
- Config on modules: `/etc/saviour/module/active_config.json`
- Controller state: `/var/lib/saviour/` (sessions, exports)
- Service: `sudo systemctl status saviour.service`
- Logs: `sudo journalctl -u saviour.service -f`
- Virtual env: `source /usr/local/src/saviour/env/bin/activate`
- Working dir: `/usr/local/src/saviour`

## Immediate tasks

1. **Find out why 8 modules are DOWN** — physical switch issue? PoE budget? Check switch admin UI or power cycle specific ports.
2. **Once modules reconnect**, run `/tmp/restore_labels.py` to restore config labels.
3. **Investigate dashboard anomalies** — A1 in wrong group, C3 showing as camera_d569.
4. **Start a new recording session** once all (or enough) modules are online.

---

## Useful one-liners

```bash
# Controller logs live
sudo journalctl -u saviour.service -f

# Controller logs filtered for discovery/online/offline events
sudo journalctl -u saviour.service -n 200 --no-pager | grep -E "discovered|online|offline|registered|heartbeat"

# Snapshot of current module states
source env/bin/activate && python3 tools/snapshot_labels.py

# Ping sweep
bash tools/ping_sweep.sh

# Restore lost labels (run after modules reconnect)
source env/bin/activate && python3 /tmp/restore_labels.py

# SSH into a specific module (if sshd is up)
ssh pi@192.168.1.170  # C3

# Check module config on the module itself
ssh pi@192.168.1.170 "cat /etc/saviour/module/active_config.json"
```
