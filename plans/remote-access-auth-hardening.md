# Web UI / REST auth hardening + access logging

- **Status:** proposed
- **Created:** 2026-09-08
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Security (open)" — the "Web UI: single shared
  password, plaintext, no lockout" bullet.

## Why

SAVIOUR's v1.0 posture is *"safe on a closed lab LAN"* — the web UI's
guest/admin split is to stop operator accidents, not to resist an attacker.
The moment the controller is reachable over Tailscale (or any routed path),
the single shared admin password is the only thing between a remote actor and
every recording, the NAS browser, `/api/v1`, and `/facade/send_command`
(arbitrary module commands). Today that password has:

- **No rate-limiting or lockout.** `handle_login` (`web.py`, Socket.IO
  `"login"`) just calls `_check_admin_password` and adds the sid to
  `_authenticated_sids` on success. An attacker can send `login` events as
  fast as the socket allows. Same for `_bearer_auth_kind()` on `/api/v1` and
  `/facade/*` (CLAUDE.md: "no rate-limiting or lockout on a bad token").
- **No failed-attempt logging.** A brute-force leaves no trace.
- **No session expiry.** A sid stays in `_authenticated_sids` until it
  disconnects; `authStorage.js` caches the raw password in `localStorage`
  indefinitely and replays it on reconnect.
- **`cors_allowed_origins="*"`** on the SocketIO server (`web.py:267`).
- **No audit trail** of who accessed what — the compliance question
  ("who could have viewed the animal-procedure / face footage, and when") has
  no answer.

## Scope — five parts, roughly in priority order

### 1. Login rate-limit + lockout + logging

- A per-source-IP counter (`request.remote_addr`; behind the `:80→:5000`
  redirect and any reverse proxy, honour `X-Forwarded-For` only from a
  configured trusted proxy list, else use the socket peer).
- On a failed `handle_login` / failed bearer check: increment; after
  `auth.max_fails` (default 5) within `auth.fail_window_s` (default 300), lock
  that IP out for `auth.lockout_s` (default 900) — return a generic error, do
  not leak "locked" vs "wrong". Add a small fixed delay (~250 ms) to every
  auth attempt regardless, to blunt fast brute-forcing.
- Log **every** failed attempt at WARNING (`ts`, `ip`, `route`), every success
  at INFO. These lines feed part 4.
- One shared limiter used by the Socket.IO `login`, `/api/v1` `require_auth`,
  and `/facade/*` `_check_bearer_auth`.
- Config keys under a new `auth` section in `base_config.json`.

### 2. Session tokens with expiry (replace "sid in a set forever")

- On successful login the controller mints a signed, expiring session token
  (reuse the `_issue_download_token` / `download_token` machinery already in
  `web.py`). TTL `auth.session_ttl_s` (default 8 h); a sliding refresh on
  activity.
- `authStorage.js` stores the **token**, not the password. On expiry / a
  `401`-equivalent, prompt for the password again (don't silently replay).
- `_authenticated_sids` becomes "sid presented a currently-valid token"; a
  reconnect re-presents the token, not the password.
- `change_admin_password` invalidates all live tokens.

### 3. CORS lockdown

- `cors_allowed_origins` from `"*"` to a config value
  (`interface.allowed_origins`, default = the controller's own
  `https?://<hostname|ip>:5000` set, computed at start). Same value used for
  the MJPEG `/snapshot.jpg` `Access-Control-Allow-Origin`
  (see `mjpeg-stream-auth.md`).

### 4. Access log (the compliance artefact)

- An append-only `access.log` (its own file under the controller's log dir,
  rotated) with structured lines: `{ts, ip, identity, action, target,
  result}` for:
  - login success / failure, logout, token refresh
  - `/api/v1` request (method, path, token name if a named token)
  - `/facade/send_command` (command + module)
  - a session opened / stopped / deleted, a marker added
  - a **livestream / snapshot opened** — needs the module stream server or the
    controller proxy to report the access back (ties into
    `mjpeg-stream-auth.md` option B, or a lightweight "stream token used"
    ping from the module).
- Best-effort, never blocks a request. Also mirrored onto the `/api/v1/events`
  SSE `alert` stream at WARNING+ so a monitoring script can watch it.
- Doc: `docs/ACCESS_LOG.md` — the format, retention, and how to answer
  "who could have seen session X's footage".

### 5. (phase 2) Named accounts

- Replace the one shared admin password with named users (`auth.users` — name
  → argon2 hash + role), so the access log carries real identities instead of
  just "admin from 100.x.y.z". Guest/admin roles stay. Bigger change; the
  first four parts are useful without it and it slots in behind them.

## Acceptance

- 6 wrong passwords from one IP in 60 s → that IP is refused for
  `auth.lockout_s`; `access.log` has 6 WARNING lines with the IP; a **7th**
  attempt from a *different* IP still works (lockout is per-IP).
- Every auth attempt takes ≥ ~250 ms.
- A session token past its TTL → the UI prompts for the password again; the
  raw password is never in `localStorage`.
- `curl -H 'Origin: https://evil.example' ...` against a browser-only route →
  CORS-rejected.
- `access.log` after a normal session shows: login (ip, ok), N `/api/v1`
  calls, a livestream open, session create/stop — enough to reconstruct
  "who could have viewed the footage".
- `change_admin_password` logs every other session out.

## Interaction with the threat model

This does **not** change the "LAN = trust boundary" stance for the ZMQ bus,
Samba, or `update_saviour` signing (all deliberately deferred). It raises the
bar on the **one path that is actually exposed when the fleet is remoted** —
the web UI + REST — from "shared password, no lockout, no log" to
"authenticated, rate-limited, audited", which is the minimum for a
defensible "we access animal-procedure and face footage remotely" story.
Pair with Tailscale tailnet-lock + tight ACLs (network layer) and
`mjpeg-stream-auth.md` (the stream layer).
