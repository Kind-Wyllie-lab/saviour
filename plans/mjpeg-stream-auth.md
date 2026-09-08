# Authenticate the per-module MJPEG livestream

- **Status:** proposed
- **Created:** 2026-09-08
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Security (open)" — the "Web UI: single shared
  password" and "All network services bind `0.0.0.0`" bullets; also the
  "Direct-from-module recording download" bullet (same `MJPEGStreamServer`,
  same open-auth question).

## Why

Each module runs its own `MJPEGStreamServer` (`src/modules/mjpeg_stream.py`)
bound to `0.0.0.0` with **no authentication** — cameras `:8080`, microphone
`:8081`, ttl `:8082`, rfid `:8083`. The frontend hits them **directly from the
browser** (`src/controller/frontend/src/basic/utils/streamUrls.js` builds
`http://<module.ip>:8080/video_feed`; the controller is not in the path), plus
`/snapshot.jpg` (which additionally sends `Access-Control-Allow-Origin: *`) and
the config editors' `:8080/roi` / `:8080/roi_line`.

On a closed lab LAN the project's threat model accepts this ("LAN = trusted").
But the moment the fleet is reachable over Tailscale (or any routed remote
path), **any node that can route to a module Pi can pull the live feed with a
single `curl`** — no password, no controller involvement. For an
establishment with Home-Office / info-governance constraints on footage of
procedures and on identifiable human faces (both are in the preview stream),
that's the gap to close.

## Options

| # | Approach | Works with `<img src>` | Effort | Residual |
|---|---|---|---|---|
| A | **Query-string token** — `/video_feed?t=<token>`; 401 without it. Token is a shared secret pushed to modules in config (same channel as the Samba creds) or a short-lived per-web-session token minted by the controller. | yes | small | tokens land in logs / browser history; a leaked long-lived token = full access until rotated |
| B | **Controller proxy** — browser hits `http://<controller>:5000/stream/<module_id>/video_feed`, gated by the existing web session; the controller fetches the module stream (with a module-side shared token) and re-streams. Module stream server binds `eth0` + the controller IP only. | yes (same-origin) | medium | N concurrent MJPEG proxies on the controller (bounded — habitat livestream grid is already capped); frontend URL builder + a few config editors change |
| C | **Network-layer only** — bind the module stream servers off `0.0.0.0` (to `eth0` + an allowlist incl. the controller), and rely on Tailscale ACLs + the firewall for who may reach them. No app-level auth. | n/a | small | still open to any *allowlisted* host; no per-request identity for the access log (plan `remote-access-auth-hardening.md`) |

## Recommendation

**C now (defence in depth) + A as the interim app control, B as the target.**

1. **Bind narrowing (C):** `MJPEGStreamServer._run` takes a bind address
   (config `stream._bind`, default `0.0.0.0` to not break existing installs;
   `saviour-config` / `mend.sh` set it to the module's `eth0` address on a
   provisioned fleet). Also drop the `Access-Control-Allow-Origin: *` on
   `/snapshot.jpg` → the configurable origin from
   `remote-access-auth-hardening.md`.
2. **Token gate (A):** a `require_stream_token` before-request check on
   `/video_feed`, `/snapshot.jpg`, `/roi*`. Token source, cheapest first:
   - **v1:** a fleet `stream.token` in config, pushed to modules like
     `export.share_password`; the frontend gets it from the controller over
     the authenticated Socket.IO channel and appends `?t=`. Rotatable via
     `saviour-config`.
   - **v2:** the controller mints a short-lived (e.g. 30 min) per-web-session
     stream token — reuse the `_issue_download_token` / `download_token`
     pattern already in `web.py` — and the module validates it against a
     controller-published signing key or a shared HMAC secret. No long-lived
     secret in a URL.
3. **Proxy (B):** only if B's list of consumers (livestream cards, fullscreen
   video, crop editor, loom ROI editor, APA cards) is worth the reroute — do
   it after A/C are in.

## Also in scope

- **`stream.enabled_when_idle`** (default `false`) — a module does not start
  its `MJPEGStreamServer` unless a recording session is active or an operator
  explicitly opens the live view (a `start_stream` command with an idle
  timeout). Data minimisation + shrinks the exposure window; independent of
  the auth work and arguably ship it first.
- `apa_arduino` / `rfid` / `sound` don't run a stream server — no change.
- The `:8080/roi` and `:8080/roi_line` GETs (camera config editors) go behind
  the same token check.

## Acceptance

- `curl http://<module>:8080/video_feed` with no token → `401`; with a bad
  token → `401`; with the current token → stream.
- The frontend livestream, snapshot button, and crop/ROI editors still work
  end to end (with the token wired into `streamUrls.js` + the editor fetches).
- From a tailnet node **not** on the module's bind/allowlist:
  connection refused, not just 401.
- Rotating `stream.token` via `saviour-config` invalidates old URLs fleet-wide.
- With `stream.enabled_when_idle=false`: no listener on `:8080` between
  sessions (`ss -ltn` on the module).

## Not doing

- TLS on the module stream server — over Tailscale/WireGuard the transport is
  already encrypted; on a bare LAN it's out of scope for this plan (see the
  web-UI TLS item, deferred for v1.0).
- Re-encoding / watermarking the stream.
