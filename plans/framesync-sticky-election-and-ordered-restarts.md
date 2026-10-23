# FrameSync: keep the elected server, restart clients after it

- **Status:** not started
- **Created:** 2026-10-23
- **Owner:** ascottg
- **CLAUDE.md ref:** "Hardware gotchas → Camera framesync" (the libcamera client/`stop()` freeze this keeps us away from)

## Why

On 2026-10-23 the APA rig's `camera_0fcc` was killed by the systemd watchdog
with nobody touching sync settings. From the controller and both camera
journals:

| Time | What happened |
|---|---|
| 09:30:12 | `3606` re-registers as `apa_camera_3606` (was `hailo_camera_3606`, re-provisioned). The controller drops the old ID and adds the new one. |
| 09:30:13 | `reconcile_framesync()` elects the lowest module_id. `apa_camera_3606` sorts before `camera_0fcc` (the old `hailo_...` didn't), so the role moves: 3606 → `server`, 0fcc → `client` (pinned to `sensor_mode_index` 0). Both `set_config`s go out in the same millisecond, and duplicates follow as config echoes arrive. |
| 09:30:14 | Both cameras restart their streams at once. The client comes up while the server is restarting. |
| 09:30:16 | 0fcc: `Dequeue timer of 1000000.00us has expired!` / `Camera frontend has timed out!`. Its camera is wedged from here on. |
| 09:30:27 | An operator saves 3606's settings in the GUI (`sensor_mode_index` among them). `handle_save_module_config` propagates it to the client straight away (`Propagating server fps/sensor_mode_index to sync client camera_0fcc`). The client restarts again, and `picam2.stop()` (`camera_base.py` `_configure_camera`) never returns. |
| 09:31:13 | The command listener has been stuck for 45 s, so the watchdog keep-alive stops. systemd aborts the process, and it comes back about 13 s later, synced. |

No session was running, so no data was lost. Two controller behaviours made it
happen, and both run during normal use:

1. **The election isn't sticky.** The server is "lowest module_id among online,
   FrameSync-enabled cameras", worked out from scratch on every call. Adding,
   renaming or re-typing a camera can move the role away from a healthy server.
   That restarts every camera on the rig for no benefit.
2. **Server and clients are restarted at the same time.** Neither
   `reconcile_framesync()` nor the web save path waits for the server to come
   back before restarting clients. A client that starts while its server is
   down or restarting is exactly the case libcamera 0.5.2 handles badly: it
   wedges, and the next `stop()` freezes.

## Current code

- `ControllerFacade.reconcile_framesync()` (`src/controller/facade.py`).
  Candidates = online cameras with `camera.framesync_enabled`. Transmitter =
  `sorted(candidates)[0]`. Each online camera whose `true_config` `sync_mode`
  differs from its target gets `set_config {"camera": ...}`. Clients also get
  the transmitter's `fps`/`sensor_mode_index`, but **only when their role
  changes**. A camera that is recording is deferred (re-run on
  `recording_stopped`).
- It's called from `ModuleManager.received_module_config()` (every camera config
  echo, `modules.py`), from module online/offline (`controller.py`), and after
  `recording_stopped`.
- `handle_save_module_config` (`web.py`, around line 2221) handles the case where
  a save changes the server's `fps`/`sensor_mode_index`. It pushes the change to
  every client **immediately**, before the server's own `set_config` goes out.
  It sends the client's **whole** cached `true_config`, which `reconcile`'s own
  comment warns can carry stale sections (it once wiped `export.share_password`).
- `PATCH /api/v1/modules/<id>/config` (`rest_api.py`) has no propagation, so a
  REST change to the server's fps leaves the clients unpinned.
- A module echoes its config only after `set_all()` returns, and `set_all()`
  restarts the camera synchronously (stack trace from the incident:
  `set_config → set_all → configure_module → _configure_camera`). So **"server
  echoed `sync_mode=server`" already means "server's stream has restarted"**.
  That's the signal ordering can wait on.

## Plan

### 1. Sticky election

Keep the current server while it stays eligible. Re-elect only when it isn't.

- Add `self._framesync_transmitter: str | None` to `ControllerFacade`. This is
  controller-side memory, not a module's claim.
- Election in `reconcile_framesync()`:
  1. If the remembered transmitter is still online and enabled, keep it.
  2. Otherwise, **cold start** (controller just started, nothing remembered): if
     exactly one candidate's `true_config` says `sync_mode == "server"`, adopt
     it. That stops a controller restart from swapping roles on a rig that was
     already synced.
  3. Otherwise, take the lowest module_id among the candidates (today's rule).
     This also breaks a tie when more than one camera claims to be the server.
- Remember the result. Clear it when no candidates remain.
- **Changed invariant:** the docstring's "never trusts a camera's self-reported
  `sync_mode`" becomes "trusts it only on cold start, and only when exactly one
  camera claims the role". The goal of never having two servers still holds:
  with several claims, the lowest wins and the rest are demoted.
  `test_reconnecting_module_with_stale_self_reported_server_is_demoted` asserts
  the old rule (the lower-ID camera wins over a single stale claim). It changes
  to: two claims → lowest wins. Add a new test: one claim on cold start → kept.
- **Module replaced or renamed** (today's trigger): `displace` removes the old
  ID, and the remembered transmitter was 0fcc anyway, so it stays server and
  `apa_camera_3606` joins as a client. If the replaced camera *was* the server,
  it's offline/removed, so step 3 elects a new one, as today.

### 2. Restart clients only after the server confirms

Make the server's confirmed config the trigger for every client change, and put
it all in `reconcile_framesync()` so there's one code path.

- **Gate:** a camera counts as "server confirmed" when its `true_config` says
  `sync_mode == "server"` and its `ModuleConfigState.status` is not `PENDING`.
  Until then, reconcile pushes only to the server and to cameras going to
  `none`, and defers every client push (log it once per change). The server's
  echo calls `received_module_config()` → `reconcile_framesync()` again, which
  then pushes the clients. No timers and no new thread.
- **Pinning moves into reconcile:** a client is out of date if its `sync_mode`
  **or** its `fps`/`sensor_mode_index` differ from the confirmed server's
  `true_config`, not only when its role changes. Then:
  - delete the propagation block in `handle_save_module_config`, so a save to
    the server goes to the server only and the clients follow on its echo;
  - REST `PATCH` on the server gets pinning without changes of its own.
  Push only the `camera` section, as reconcile already does, which also fixes
  the web path's whole-`true_config` send.
- **Fewer duplicates:** today one change sends the same `set_config` 2-4 times,
  because every echo re-runs reconcile before `true_config` has caught up. Skip
  a camera whose `target_config` already holds the wanted role/fps/mode while
  its status is `PENDING`. Each duplicate is a no-op on the module (`Updated
  keys: []`), but they clutter the logs and the retry counters.
- **Server echo that never arrives** (server crashes or freezes mid-restart):
  the clients stay on their old settings. That's safer than the incident's
  behaviour. When the server comes back online, or goes offline (election step
  3), reconcile runs again. The existing config retry/timeout (`_resolve_sync_status`)
  marks it `FAILED`. Treat `FAILED` as unconfirmed too, and let the UI show it
  as it does today.
- **Settle time:** the server's echo arrives once `start_streaming()` has
  returned. The server reports `Sync achieved` 3-4 s later. In the incident the
  client wedged because the server was mid-restart, not in that later window,
  so start with no delay. If a client still times out on the rig, add a short
  `threading.Timer` before the client pushes (open question 2).

### Out of scope here (backlog)

- `_configure_camera`: after a libcamera frontend timeout, skip `picam2.stop()`
  and restart the service right away instead of waiting for the watchdog.
  That's about 15 s faster to recover, and it only matters once 1 and 2 have
  removed the usual trigger.
- `set_target_module_config ... missing top-level keys: {'logging', 'network'}`:
  a warning logged on every reconcile push. It's noise, but it hides real
  mismatches.

## Tests (`src/controller/tests/test_facade.py`, `test_modules_config_sync.py`, `test_web*.py`)

- Sticky: server `camera_b`, then add `apa_a` → `camera_b` stays server and
  `apa_a` → client. Remembered server goes offline → lowest remaining elected.
  Remembered server disabled → re-elect.
- Cold start: one camera claims server → kept even if it isn't the lowest. Two
  claim → lowest kept, other demoted. None claim → lowest.
- Ordering: role change pushes the server only. After the server's config echo
  (`received_module_config` with `sync_mode=server`), the clients are pushed
  with the server's fps/mode. While the server is `PENDING`, no client push.
- Pinning: server's `true_config` fps changes (echo) → client pushed. Client
  already matching → no push.
- Web save to the server no longer sends to clients directly.
- Duplicates: two echoes in a row while the target is pending → one push.
- Recording deferral still holds for clients and for the server.

## Validation on hardware (APA rig: `camera_0fcc` + `apa_camera_3606`)

1. Repeat the incident: change one camera's `module.name` or re-type it so its
   ID sorts first, with FrameSync on both. Expect no role change and no camera
   restarts, apart from the renamed camera's own.
2. Turn FrameSync off on the server: the other camera is elected. Expect the
   server restart, then the client restart once the server's echo arrives
   (controller log order), with no `frontend has timed out` and no watchdog.
3. Change the server's sensor mode in the GUI, then fps via
   `PATCH /api/v1/modules/<id>/config`. The client follows each one, after the
   server.
4. Restart the controller service while both cameras are synced: no role
   change.
5. Run a 1 min session afterwards and check `framesync_report.json` as usual.

## Open questions

1. Is trusting a single self-reported server on cold start acceptable? The
   alternative keeps the controller fully stateless across restarts, at the
   cost of one swap whenever the controller restarts on a rig whose server
   isn't the lowest ID.
2. Do we need a settle delay after the server's echo? Measure on the rig first
   (validation step 2).
