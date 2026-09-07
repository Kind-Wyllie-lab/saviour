# Controller REST API (v1)

A resource-oriented HTTP API on the controller for external programs on the
lab LAN — an experiment controller (pyControl, MATLAB, a bespoke acquisition
script) that needs to read system state or start/stop recordings without
driving the Socket.IO web UI.

Implemented as a Flask blueprint in `src/controller/rest_api.py`, mounted at
`/api/v1`. It is a thin layer over the same `ControllerFacade` the web UI's
Socket.IO handlers call, so the two entry points stay in step.

The older `/facade/*` routes (`list_modules`, `module_health`,
`exported_recordings`, `send_command`) are unchanged and still available;
`/facade/send_command` remains the escape hatch for issuing an arbitrary
module command, which `/api/v1` deliberately does not expose.

## Authentication

Every route except the index authenticates with a bearer token:

```
Authorization: Bearer <secret>
```

`<secret>` is **either**:

- the **shared admin password** — the same credential the web UI login uses
  (`sudo cat /etc/saviour/admin_credentials` on the controller); **or**
- a **named API token** minted via `POST /api/v1/tokens` (see
  [API tokens](#api-tokens)). Preferred for a script — it can be revoked on
  its own without disturbing the web UI login.

The `/api/v1/tokens` management routes require the **admin password
specifically** (not an API token), so a leaked scoped token cannot mint more.

The controller serves **plain HTTP** (no TLS) — the token crosses the LAN in
the clear, exactly as the web UI login already does. The project threat
model treats the LAN as the trust boundary (see the top-level `CLAUDE.md`).
Do not expose the controller off-LAN. There is no rate-limiting or lockout
on a bad token, same as the web UI.

## Conventions

| Outcome | Response |
|---|---|
| Success | the resource as bare JSON, `200` (`201` for a created session) |
| Failure | `{"error": {"code": "<slug>", "message": "<text>", ...}}` |

Error statuses: `400` invalid request, `401` bad/missing token, `404`
unknown module or session, `409` conflict (a module already recording, PTP
not synced, a session not in a state that allows the action), `503` the
export share is configured but unreachable.

`base_url` below is `http://<controller>:5000`.

---

## Read endpoints

### `GET /api/v1/` — index

Unauthenticated. Confirms the API is up and lists mounted routes. Discloses
no state.

### `GET /api/v1/state` — system rollup

```json
{
  "version": "v0.9-271-gd49ae06e",
  "uptime_s": 43201,
  "recording": true,
  "sessions": {"total": 3, "active": 1, "pending": 0, "scheduled": 1,
               "paused": 0, "stopped": 1, "error": 0},
  "modules": {"total": 6, "online": 6, "offline": 0},
  "ptp": {"worst_offset_ns": 8200, "start_gate_ns": 50000, "synced": true},
  "disk": {"status": "ok", "free_gb": 812.4, "free_pct": 41.3, ...}
}
```

`ptp.synced` is `worst_offset_ns <= start_gate_ns` (the
`recording.ptp_start_gate_us` threshold, ×1000). It is the same condition
the recording-start gate enforces, so a script can check it *before*
starting a session. `worst_offset_ns` is `null` when no PTP data is
available yet — treat that as not synced. `disk` is the controller's last
cached share probe; reading it does not trigger a mount.

### `GET /api/v1/health`

```json
{"summary": { ... get_health_summary() ... },
 "modules": {"camera_a1b2": { ...per-module health... }, ...}}
```

### `GET /api/v1/ptp`

Per-module PTP offsets plus the fleet-worst and the gate verdict:

```json
{
  "worst_offset_ns": 8200,
  "start_gate_ns": 50000,
  "synced": true,
  "modules": {
    "camera_a1b2": {"ptp4l_offset_ns": 8200, "phc2sys_offset_ns": 1100,
                    "ptp4l_freq": -24310}
  }
}
```

### `GET /api/v1/modules` · `GET /api/v1/modules/<id>`

The module registry (`{id: {...}}`), or one module. `404` for an unknown id.

### `GET /api/v1/modules/<id>/health`

One module's health dict. `404` for an unknown id; `{}` if the module is
known but has not reported health yet.

### `GET /api/v1/sessions` · `GET /api/v1/sessions/<name>`

All recording sessions (`{name: {...}}`) or one, serialised from the
`RecordingSession` dataclass (`state`, `modules`, `start_time`,
`duration_minutes`, `pending_exports`, `plans`, …). `404` for an unknown
name.

### `GET /api/v1/exports`

```json
{"exported_recordings": ["MyExp_.../20260907/camera_a1b2/seg0.mp4", ...],
 "pending_exports": 0, "failed_exports": 0}
```

### `GET /api/v1/events` — Server-Sent Events stream

A `text/event-stream` of typed controller events, so a caller doesn't have to
poll. Event types:

| `type` | payload | fired when |
|---|---|---|
| `sessions` | `{"sessions": {name: {...}}}` | any session state change (full snapshot) |
| `modules` | `{"modules": {id: {...}}}` | the module registry changes (online/offline, discovery) |
| `alert` | `{"key", "title", "message", "severity"}` | any controller fault — module offline, PTP degraded, export stall, low disk, … (independent of whether Teams alerting is configured) |
| `marker` | `{"session", "label", "recv_wall_ns"}` | a marker is accepted |

Every event also carries `type` and `ts` (controller epoch seconds). Optional
`?types=marker,alert` restricts the stream. Comment lines (`: keep-alive`)
arrive every 15 s.

```python
import requests, json
r = requests.get(f"{BASE}/events?types=sessions,alert",
                 headers=AUTH, stream=True)
for line in r.iter_lines():
    if line.startswith(b"data: "):
        evt = json.loads(line[6:])
        print(evt["type"], evt)
```

Each subscriber holds a worker thread for the life of the connection — keep
the number of concurrent `/events` clients small.

---

## Recording lifecycle

### `POST /api/v1/sessions` — create (and optionally start) a session

Request body:

| field | type | default | notes |
|---|---|---|---|
| `name` | string | — | **required**; formatted into the final session name (target + date appended) unless it already looks complete |
| `target` | string | `"all"` | module id, module type (`"camera"`), or `"all"` |
| `duration_minutes` | number | `null` | `null` = manual stop |
| `researcher` | string | `null` | |
| `unattended` | bool | `false` | long-term posture: self-heal instead of terminal `ERROR` |
| `autostart` | bool | `false` | if true, start recording immediately after creation |

Preflight: the export share is checked for free space first — `503`
`share_unavailable` if it is configured but unreachable or full.

`201` on success with the created session object. If `autostart` was
requested but the start gate rejected it (e.g. PTP not yet converged), the
session is still created `PENDING` and the body carries an
`"autostart_error"` field — retry with `POST .../<name>/stop` +
recreate, or wait and call the start path via the web UI. A facade
rejection (no online modules, name clash, modules already recording) is
`409` `session_rejected`.

```bash
curl -X POST "$base_url/api/v1/sessions" \
  -H "Authorization: Bearer $PW" -H "Content-Type: application/json" \
  -d '{"name": "loom_batch7", "target": "all", "autostart": true}'
```

### `POST /api/v1/sessions/<name>/stop`

Sends `stop_recording` to the session's modules. `200` with the session
object (it stays `ACTIVE` until every module confirms, then flips to
`STOPPED`). `404` for an unknown name.

### `POST /api/v1/sessions/<name>/pause` · `.../resume`

Habitat Sessions only (sessions with per-plan strategies). `409`
`pause_rejected` / `resume_rejected` for a plain session or one not in the
right state.

### `POST /api/v1/sessions/<name>/marker` — drop an event marker

Appends a labelled row to `<session>/markers.csv` on the share, stamped with
the controller's wall clock (it is the PTP grandmaster, so the same timebase
as module frame timestamps). This is how an external experiment controller
gets its behavioural events — trial start, stimulus onset, reward, poke —
into the recording timeline.

| body field | type | | |
|---|---|---|---|
| `label` | string | **required** | the event name |
| `source` | string | optional | e.g. `"pyControl"` |
| `t` | number | optional | the caller's own epoch time for the event, in **seconds** (as from `time.time()`) |

Only valid while the session is `ACTIVE` — `409` `marker_rejected` otherwise,
`404` for an unknown name. `201` on success with
`{recv_wall_ns, recv_iso, label}`. `recv_wall_ns` is the controller's receive
time (good to ~1 ms on the LAN); `markers.csv` columns are
`recv_wall_ns, recv_iso, label, source, client_wall_ns`.

```bash
curl -X POST "$base_url/api/v1/sessions/$S/marker" \
  -H "Authorization: Bearer $PW" -H "Content-Type: application/json" \
  -d '{"label": "trial_1", "source": "pyControl", "t": 1757246400.512}'
```

### `DELETE /api/v1/sessions/<name>`

Query params: `files` (`true`/`false`, default `true`) — also delete the
session's files from the share; `force` (`true`/`false`, default `false`) —
delete even with unresolved/failed exports.

`200` `{"deleted": true, "session_name": "..."}`. `404` for an unknown
name. `409` for an active/scheduled session (stop it first) or unresolved
exports without `force` — the latter carries `export_warning`,
`pending_exports`, `total_exports_failed` in the error body.

---

## API tokens

Named bearer tokens as an alternative to embedding the web-UI admin password
in a script. Stored on the controller as SHA-256 hashes in
`/etc/saviour/api_tokens.json` (mode 600) — the raw token is shown once, at
creation, and is not recoverable afterwards. These routes require the
**admin password** (not an API token).

### `GET /api/v1/tokens`

`{"tokens": [{"name": "pyControl-rig3", "created": "2026-09-07T..."}]}` —
metadata only, never the hash.

### `POST /api/v1/tokens`

Body `{"name": "pyControl-rig3"}`. `201` `{"name", "token", "created"}` —
**record `token` now**. `409` if the name is taken, `400` if empty.

### `DELETE /api/v1/tokens/<name>`

`200` `{"revoked": true, "name": "..."}`; `404` for an unknown name. The
token stops authenticating immediately.

```bash
PW=$(ssh controller sudo cat /etc/saviour/admin_credentials)
curl -X POST "$base_url/api/v1/tokens" \
  -H "Authorization: Bearer $PW" -H "Content-Type: application/json" \
  -d '{"name": "pyControl-rig3"}'
# -> {"name":"pyControl-rig3","token":"XZ...","created":"..."}
```

---

## pyControl integration sketch

From a pyControl *host* task-definition file (plain Python), gate the
experiment on rig readiness, then bracket it with start/stop:

```python
import time
import requests

BASE = "http://192.168.0.98:5000/api/v1"
AUTH = {"Authorization": "Bearer " + open("/etc/saviour_token").read().strip()}

def saviour_start(name):
    st = requests.get(f"{BASE}/state", headers=AUTH, timeout=5).json()
    if not st["ptp"]["synced"]:
        raise RuntimeError(f"PTP not synced: {st['ptp']['worst_offset_ns']} ns")
    r = requests.post(f"{BASE}/sessions", headers=AUTH, timeout=10,
                      json={"name": name, "target": "all", "autostart": True})
    r.raise_for_status()
    body = r.json()
    if body.get("autostart_error"):
        raise RuntimeError(body["autostart_error"])
    return body["session_name"]

def saviour_mark(session_name, label):
    requests.post(f"{BASE}/sessions/{session_name}/marker",
                  headers=AUTH, timeout=5,
                  json={"label": label, "source": "pyControl",
                        "t": time.time()}).raise_for_status()

def saviour_stop(session_name):
    requests.post(f"{BASE}/sessions/{session_name}/stop",
                  headers=AUTH, timeout=10).raise_for_status()
```

Call `saviour_mark(s, "trial_1")` (etc.) from the task's event handlers so the
behavioural timeline lands in `markers.csv` alongside the video/audio. For a
watchdog, tail `GET /events?types=alert,sessions` on a background thread and
abort the run if a module drops.

## Not yet in the API

Arbitrary module commands (`/facade/send_command` still covers this),
scheduled-session and Habitat-session *creation*, config reads/writes,
module management (reboot/update), a per-session file manifest + bearer-minted
download token. Candidates for a later version; the blueprint is the place to
add them.
