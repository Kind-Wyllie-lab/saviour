# SAVIOUR Telemetry — Design

**Status:** scoped 2026-09-01, **not implemented**. This document is the plan;
no telemetry code or server exists yet. It is committed so the design and its
governance reasoning aren't lost before someone picks it up, and so the
"What is transmitted" section below can serve as the consent artifact.

---

## 1. Motivation

- **Diagnostics are currently archaeology.** Every recent multi-day
  investigation — the habitat PTP auto-restart feedback loop, the 5 unexplained
  module reboots in session `CRLLT3-247Cameras-camera-173650`, the export
  contention question — was reconstructed after the fact from a manually pulled
  `saviour_diagnostics_*` bundle with only ~2 h of journal retention. By the
  time anyone looked, the evidence had rotated out. A passive trickle of
  structured metrics off each controller would make this analysis routine
  instead of forensic, and would catch slow trends (disk fill, export backlog
  growth, PTP drift) that no single snapshot shows.
- **Fleet-level statements.** "SAVIOUR recorded X TB across Y sessions over Z
  device-weeks", PTP stability distributions, throughput and storage trends —
  useful for grant reporting, the conference poster, and for prioritising which
  reliability work actually matters at scale.

## 2. Principles

1. **Opt-in, explicit consent. Default off.** SAVIOUR is intended for use
   beyond the developing lab. Another institution's operational data must never
   leave their network without a deliberate operator action, and they must be
   able to see exactly what would be sent before enabling it.
2. **Structured, allowlisted events only.** Never raw log text, never config
   values, never file paths, session names, IP/MAC addresses, or hostnames.
   The allowlist is enforced by a test, not by diligence — this repo has
   shipped credential leaks from "walk the whole thing" code paths before.
3. **Best-effort, never blocks recording.** Same shape as the existing Teams
   webhook (`src/controller/notify.py`): a background thread, a short timeout, a
   failed POST is buffered and retried, never surfaced as a fault.
4. **Controller-only.** Modules already report health to the controller, which
   already aggregates and retains it. Telemetry reads those existing structures
   and ships a rollup; it adds no new collection on the module side.
5. **Endpoint-agnostic.** The client POSTs to a configurable URL with a bearer
   token. Where the server lives, and whether it ever moves, is an ops decision
   that does not touch client code.

## 3. What is transmitted

### 3.1 Heartbeat — every `telemetry.interval_s` (default 300 s)

| Field | Notes |
|---|---|
| `install_id` | Random UUIDv4, generated once on first run, stored in active config. **Not** derived from hostname, MAC, or any hardware ID. |
| `site_label` | Operator-set free text, **blank by default**. The only identifying field, and only if the operator deliberately sets it (e.g. "Kind-Wyllie habitat rig A") for support purposes. |
| `schema_version` | Telemetry payload schema version. |
| `saviour_version` | `src/__version__.py`. |
| `variant` | Controller variant (`basic` / `apa` / `habitat` / …). |
| `controller_uptime_s` | |
| `module_count`, `modules_recording` | Counts only. |
| `session_active` | Bool. |

Per-module, keyed by a **per-install stable pseudonym** (`m01`, `m02`, …
assigned by the controller — never the real `module_id`, which can encode a
MAC suffix):

| Field | Notes |
|---|---|
| `ptp4l_offset_ns` | min / max / p95 over the interval (from the existing 1 s PTP buffer). |
| `phc2sys_offset_ns` | min / max / p95. |
| `throttled` | Pi under-voltage / thermal bitmask (`decode_throttled` in `src/shared/health.py`). |
| `disk_used_gb`, `disk_free_gb`, `disk_total_gb` | |
| `rec_bytes_per_s` | Measured recording throughput. |
| `cpu_temp`, `cpu_usage`, `mem_usage` | |

### 3.2 Events

| Event | Payload | Explicitly excluded |
|---|---|---|
| `controller_start` / `controller_stop` | `install_id`, timestamp | — |
| `session_start` | `install_id`, timestamp, `module_count` | session name |
| `session_stop` | `install_id`, timestamp, `duration_s`, `total_bytes`, `segment_count`, `module_count`, `exports_failed` (count) | session name, file list, file names |
| `fault` | `install_id`, timestamp, module pseudonym, typed enum, one bounded numeric detail (e.g. offset in ns) | free-text message |

`fault` enum (mirrors the controller's existing fault types): `ptp_degraded`,
`module_offline`, `export_stall`, `disk_low`, `recording_start_failed`,
`module_rebooted`.

### 3.3 Never transmitted, under any code path

- Session names, experiment codes, researcher or PI names
- File paths, directory names, file names
- IP addresses, MAC addresses, hostnames, SSIDs
- Samba credentials, Teams webhook URLs, or any other config value
- Raw journald / application log lines
- Video, audio, CSV sidecars, or any recorded data or derivative of it

## 4. Client design

- New `src/controller/telemetry.py`: `TelemetryReporter`, a supervised
  background thread (same helper the other long-lived controller loops should
  use — see the "unsupervised threading" architectural item).
- Reads only from what the controller already has: `module_health_history`,
  `PTP.get_recent_offset_range()`, `_nas_history`, the facade's session view.
  No new sampling.
- Config block (in `base_config.json`, controller side):

  ```json
  "telemetry": {
    "enabled": false,
    "endpoint": "",
    "token": "",
    "install_id": "",
    "site_label": "",
    "interval_s": 300
  }
  ```

  `install_id` self-populates on first run when empty and is written back to
  active config. `enabled` with an empty `endpoint` is a no-op (logged once).

- **Transport:** HTTPS only; refuses a plain-`http://` endpoint. Sends
  `Authorization: Bearer <token>`. Short connect/read timeout. One retry, then
  spool.
- **Buffering — "delayed, not lost":** on any send failure, append the rollup
  to a local spool (`/var/lib/saviour/telemetry_spool/`, capped at ~24 h or a
  few MB, oldest dropped on overflow). Flush oldest-first on the next
  successful send. A desk-hosted endpoint being down for a reboot therefore
  costs latency, not data.
- **`test_telemetry.py`:** builds a payload from a fixture whose health/session
  data contains a Samba password, a Teams webhook URL, a session name with a
  person's name in it, and a `/home/researcher/...` path. Asserts none of those
  strings appear anywhere in the serialised payload, **and** that every key in
  the payload is in the documented allowlist (new keys fail the test until
  added to both the allowlist and this doc).

## 5. Server design

Committed at `deploy/telemetry-server/`:

- `docker-compose.yml` — Postgres + a ~50-line ingest app (Flask or FastAPI) +
  Caddy for automatic TLS.
- `schema.sql` — a single append-only table:

  ```
  telemetry_events(
    id           bigserial primary key,
    received_at  timestamptz not null default now(),
    kind         text not null,          -- 'heartbeat' | 'session_start' | ...
    install_id   uuid not null,
    site_label   text,
    payload      jsonb not null
  )
  ```

  No `UPDATE`, no `DELETE` in the application path.
- `README.md` — deploy steps, the retention job, and a `pg_dump` backup cron to
  separate storage.
- **Auth:** one static bearer token, checked on every request. Insert-only; no
  read API is exposed to devices. A viewing tool (Grafana, or a small static
  page) connects to Postgres directly, server-side.
- **Retention:** a scheduled job trims rows older than N months (proposed
  default: 18).

## 6. Hosting (current)

- **All current deployments are on the University of Edinburgh network**, so the
  endpoint does **not** need public-internet exposure. It sits on an internal
  wired subnet reachable by the rigs' controllers — initially a Pi (or small
  box) on a registered UoE device address, database on **NVMe, not the boot SD
  card** (continuous small inserts are a heavy SD-wear pattern).
- **Verify reachability before building around a given host.** eduroam clients
  are frequently NAT'd and cannot accept inbound connections from other
  clients; the endpoint needs a routable wired research-subnet address (ideally
  a DNS name), and a controller should be confirmed able to reach it first.
- **Single point of failure is accepted** for now: if the endpoint is down,
  controllers spool and flush later (§4). A `pg_dump` cron off the host guards
  against a dead disk.
- **Not locked in.** Moving to a University IS-managed VM, or an external VPS,
  is a `telemetry.endpoint` config change plus `docker compose up` elsewhere —
  the client doesn't care.
- **First deployment outside UoE:** at that point that site either runs its own
  endpoint, or the UoE endpoint is exposed through a university-approved route
  (reverse proxy / DMZ). Not needed until it actually happens.

## 7. Consent surface

- **`setup.sh`:** a prompt during setup — *"Enable anonymous operational
  telemetry to the SAVIOUR developers? [y/N]"* — defaulting to no, with a
  one-line summary and a pointer to this document.
- **Web UI:** a toggle in Settings (a new "Telemetry" section on the Alerts
  tab, or its own tab — open question), showing the configured endpoint, the
  last successful send time, and a link to the field list. Guest-disabled like
  the other admin controls.
- **`docs/TELEMETRY.md`:** a short user-facing extract of §3 ("What is
  transmitted") for operators and their institutions' data-protection offices.
  §3 of *this* doc is the authoritative list.

## 8. Governance statement (draft, for external deployments)

> SAVIOUR can optionally transmit operational telemetry — clock-synchronisation
> accuracy, storage and throughput metrics, and typed fault events — to a
> University of Edinburgh research server. Transmission is disabled by default
> and requires explicit operator opt-in. Payloads are identified only by a
> randomly generated installation ID, plus an optional operator-set label. No
> personal data, no recorded research data, and no file names, session names,
> network addresses, or credentials are transmitted. Data is stored
> append-only, retained for up to 18 months, and used solely to improve
> SAVIOUR's reliability and to report aggregate usage.

## 9. Phased delivery

1. **Controller client** — `src/controller/telemetry.py`, config block,
   spool/flush buffering, `test_telemetry.py`. Endpoint-agnostic; inert until
   configured. Can land and be reviewed with no server in existence.
2. **`deploy/telemetry-server/`** — compose file, schema, README.
3. **Consent surface** — `setup.sh` prompt, web UI toggle, `docs/TELEMETRY.md`.
4. **Enrichment** — typed fault events beyond the heartbeat; a viewing
   dashboard.

## 10. Open questions

- Exact host + DNS name on the UoE network (ops, not code).
- Web UI: dedicated Telemetry tab vs a section on the Alerts tab.
- Retention period (proposed: 18 months).
- Whether a set `site_label` should trigger extra wording in the consent
  prompt ("this will identify your rig group to the developers").
- Heartbeat interval default (proposed: 300 s) vs payload volume on the desk
  host.
