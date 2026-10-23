#!/usr/bin/env python3
"""
Controller Facade

This class provides an interface for controller objects to interact with one another.

Author: Andrew SG
Created: 20/01/2026
"""

import logging
import time
from dataclasses import asdict

from src.controller.modules import ConfigSyncStatus


class ControllerFacade:
    def __init__(self, controller):
        self.logger = logging.getLogger(__name__)
        self.logger.info("Instantiating ControllerAPI...")
        self.controller = controller
        # FrameSync transmitter this controller elected (sticky; see
        # reconcile_framesync), and the clients last logged as waiting on it.
        self._framesync_transmitter: str | None = None
        self._framesync_waiting: list[str] = []
        self._started_mono = time.monotonic()


    """Getter Methods"""
    def get_modules(self) -> dict:
        return self.controller.modules.get_modules()


    def get_module_ip(self, module_id: str) -> str:
        return self.controller.modules.get_module_ip(module_id)


    def get_modules_by_target(self, target: str) -> dict:
        return self.controller.modules.get_modules_by_target(target)


    def get_module_health(self, module_id: str | None = None):
        return self.controller.health.get_module_health(module_id)


    def get_health_summary(self) -> dict:
        return self.controller.health.get_health_summary()


    def get_module_config(self, module_id: str) -> dict:
        return self.controller.get_module_config(module_id)


    def get_module_configs(self) -> dict:
        return self.controller.modules.get_module_configs()


    def get_export_credentials(self) -> dict:
        return self.controller.get_export_credentials()

    def get_controller_own_share_info(self) -> dict:
        return self.controller.get_controller_own_share_info()


    def get_share_path(self) -> str:
        return self.controller.config.get("export.mount_path", "/home/pi/controller_share")


    def get_config(self) -> dict:
        return self.controller.config.get_all()


    def get_uptime(self) -> int:
        return round(time.time() - self.controller.start_time, 0)


    def get_ptp_sync(self) -> int:
        """Return the offset for the worst-synced module"""
        return self.controller.health.get_ptp_sync()


    def export_ptp_history_csv(self, hours: float | None = 24.0):
        return self.controller.health.export_ptp_history_csv(hours)


    def get_recording_status(self) -> bool:
        return self.controller.recording.get_recording_status()


    def get_recording_sessions(self) -> dict:
        return self.controller.recording.get_recording_sessions()

    def get_sync_selftest_result(self, module_id: str) -> dict | None:
        return getattr(self.controller, "sync_selftest_results", {}).get(module_id)


    def get_system_state(self) -> dict:
        """A compact rollup of controller state. Consumed by the Socket.IO
        `get_system_state` event and the REST /api/v1/state route. The
        `uptime` / `ptp_sync` keys are kept (not renamed) because the
        habitat Dashboard reads them directly."""
        sessions = self.get_recording_sessions()
        counts: dict = {}
        for s in sessions.values():
            counts[str(s.state)] = counts.get(str(s.state), 0) + 1
        summary = self.get_health_summary()
        try:
            ptp_ns = self.get_ptp_sync()
        except Exception:
            ptp_ns = None
        return {
            "recording": self.get_recording_status(),
            "uptime": self.get_uptime(),  # seconds since controller start
            "ptp_sync": ptp_ns,  # largest |ptp4l_offset_ns| across modules
            "sessions_total": len(sessions),
            "sessions_active": counts.get("active", 0),
            "sessions_pending": counts.get("pending", 0),
            "sessions_scheduled": counts.get("scheduled", 0),
            "modules_total": summary.get("total_modules", 0),
            "modules_online": summary.get("online_modules", 0),
            "modules_offline": summary.get("offline_modules", 0),
        }


    """Utilities"""
    def remove_module(self, module_id: str):
        self.controller._remove_module(module_id)


    def send_command(self, module_id: str, command: str, params: dict) -> None:
        self.controller.communication.send_command(module_id, command, params)

    def remove_dealer(self, module_id: str) -> None:
        self.controller.communication.remove_dealer(module_id)


    """Callbacks"""
    def on_status_change(self, module_id: str, status: str):
        self.controller.on_module_status_change(module_id, status)


    def push_module_update_to_frontend(self, modules: dict):
        self.controller.web.push_module_update(modules)


    """Recording"""
    def start_recording(self, target: str, session_name: str, duration: int = 0) -> dict:
        """Start recording by creating a session. Kept for backwards-compatibility with web events."""
        return self.controller.recording.create_session(session_name, target)

    def stop_recording(self, target: str) -> None:
        """Stop recording for a target by finding and stopping its session."""
        session_name = self.controller.recording.get_session_name_from_target(target)
        if session_name:
            self.controller.recording.stop_session(session_name)
        else:
            # No managed session — send command directly as a fallback
            self.controller.communication.send_command(target, "stop_recording", {})

    def check_ptp_sync(self, target: str) -> dict:
        modules = list(self.get_modules_by_target(target).keys())
        if not modules:
            return {"ok": True}
        return self.controller.recording._check_ptp_sync(modules)

    def create_session(self, session_name: str, target: str, duration_minutes=None, researcher=None, raw_name=False, unattended=False) -> dict:
        return self.controller.recording.create_session(session_name, target, duration_minutes, researcher, raw_name, unattended)

    def update_pending_session(self, session_name: str, new_session_name=None, duration_minutes=None) -> dict:
        return self.controller.recording.update_pending_session(session_name, new_session_name, duration_minutes)

    def create_scheduled_session(self, session_name: str, target: str, start_time: str, end_time: str, days=None, researcher=None, raw_name=False) -> dict:
        return self.controller.recording.create_scheduled_session(session_name, target, start_time, end_time, days, researcher, raw_name)

    def stop_session(self, session_name: str) -> None:
        return self.controller.recording.stop_session(session_name)

    def add_marker(self, session_name: str, label: str, source: str | None = None,
                   client_wall_ns: int | None = None) -> dict:
        return self.controller.recording.add_marker(
            session_name, label, source, client_wall_ns)

    def get_markers(self, session_name: str, since_ns: int | None = None,
                    limit: int | None = None) -> dict:
        return self.controller.recording.get_markers(
            session_name, since_ns, limit)

    def create_habitat_session(self, session_name: str, plans: list,
                               researcher=None, duration_minutes=None) -> dict:
        return self.controller.recording.create_habitat_session(
            session_name, plans, researcher, duration_minutes
        )

    def pause_session(self, session_name: str) -> dict:
        return self.controller.recording.pause_session(session_name)

    def resume_session(self, session_name: str) -> dict:
        return self.controller.recording.resume_session(session_name)

    def estimate_habitat_volume(self, plans: list, expected_minutes: float) -> dict:
        return self.controller.recording.estimate_habitat_volume(
            plans, expected_minutes
        )

    def estimate_session_projection(self, session_name: str,
                                    horizon_minutes: float | None = None) -> dict:
        return self.controller.recording.estimate_session_projection(
            session_name, horizon_minutes
        )

    def set_session_expected_duration(self, session_name: str,
                                      minutes: float | None) -> dict:
        return self.controller.recording.set_session_expected_duration(
            session_name, minutes
        )

    def force_start_scheduled_session(self, session_name: str) -> dict:
        return self.controller.recording.force_start_scheduled_session(session_name)

    def force_start_session(self, session_name: str) -> dict:
        return self.controller.recording.force_start_session(session_name)

    def delete_session(self, session_name: str, delete_files: bool = True,
                       force: bool = False) -> dict:
        return self.controller.recording.delete_session(session_name, delete_files, force)

    def clear_ended_sessions(self, delete_files: bool = False, force: bool = False) -> dict:
        return self.controller.recording.clear_ended_sessions(delete_files, force)

    def delete_sessions(self, names: list, delete_files: bool = True,
                        force: bool = False) -> dict:
        return self.controller.recording.delete_sessions(names, delete_files, force)

    def request_recording_state_refresh(self, session_name: str) -> dict:
        return self.controller.recording.request_recording_state_refresh(session_name)

    def retry_failed_exports(self, session_name: str) -> dict:
        return self.controller.recording.retry_failed_exports(session_name)

    def resolve_pending_exports(self, session_name: str) -> dict:
        return self.controller.recording.resolve_pending_exports(session_name)

    def clear_fault(self, session_name: str) -> dict:
        return self.controller.recording.clear_fault(session_name)

    def add_module_to_session(self, session_name: str, module_id: str) -> dict:
        return self.controller.recording.add_module_to_session(session_name, module_id)

    def module_stopped(self, module_id: str) -> None:
        self.controller.recording.module_stopped(module_id)


    def update_sessions(self, sessions: dict) -> None:
        serializable_sessions = {k: asdict(v) for k, v in sessions.items()}
        self.controller.web.socketio.emit("sessions_update", serializable_sessions)
        try:
            self.controller.web._publish_api_event(
                "sessions", {"sessions": serializable_sessions})
        except Exception:
            pass

    # -- Sync-quality validation (framesync_check.SyncCheckWorker) ------- #
    def submit_framesync_check(self, spec: dict) -> None:
        """Queue a sync-quality check. Called from Recording._monitor_sessions
        (auto) — the worker is owned by web.py."""
        self.controller.web._get_framesync_worker().submit(spec)

    def apply_framesync_verdict(self, session_name: str, scope: str,
                                date_dir: str | None, verdict: dict,
                                report_rel: str | None) -> None:
        """Worker -> here -> Recording: store the verdict on the session."""
        self.controller.recording.apply_framesync_verdict(
            session_name, scope, date_dir, verdict, report_rel
        )

    """Set config"""
    def set_config(self, new_config: dict) -> bool:
        self.controller.config.set_all(new_config, persist=True)
        updated_config = self.controller.config.get_all()
        return new_config == updated_config

    """Module Management"""
    def is_module_recording(self, module_id: str) -> bool:
        return self.controller.modules.is_module_recording(module_id)


    def is_module_online(self, module_id: str) -> bool:
        return self.controller.modules.is_module_online(module_id)


    def received_module_config(self, module_id: str, module_config: dict) -> None:
        self.controller.modules.received_module_config(module_id, module_config)


    def set_target_module_config(self, module_id: str, module_config: dict) -> None:
        self.controller.modules.set_target_module_config(module_id, module_config)


    def apply_section_to_cameras(self, section: str, section_data: dict) -> None:
        """Merge section_data into the given config section on every camera module."""
        self.apply_section_to_type("camera", section, section_data)

    def apply_section_to_type(self, module_type: str | None, section: str, section_data: dict) -> None:
        """Merge section_data into the given config section on every module of module_type.
        Pass module_type=None to target all modules regardless of type."""
        targets = self.controller.modules.apply_section_to_type(module_type, section, section_data)
        for module_id, config in targets:
            self.controller.communication.send_command(module_id, "set_config", config)
        label = module_type if module_type else "all"
        self.logger.info(
            f"apply_section_to_type: sent '{section}' to {len(targets)} {label} module(s)"
        )


    def _camera_config_states(self) -> dict:
        """Return {module_id: ModuleConfigState} for every known camera-type module."""
        modules = self.controller.modules
        return {
            mid: state
            for mid, state in modules._config_states.items()
            if (module := modules._modules.get(mid)) and "camera" in module.type
        }

    # Before this long after start, reconcile waits for every online camera's
    # first config: they arrive one by one, and electing on a partial view
    # could pick a client before the rig's actual server has reported.
    _FRAMESYNC_COLD_START_S = 60.0

    def reconcile_framesync(self) -> dict:
        """Ensure exactly one online, FrameSync-enabled camera is the sync
        transmitter ('server'), every other online enabled camera is a
        'client' pinned to the transmitter's fps/sensor_mode_index, and any
        camera that is no longer enabled (or has gone offline) is corrected
        to 'none' once reachable.

        Election is sticky: the transmitter this controller elected stays
        elected while it is online and enabled, so adding, renaming or
        re-typing a camera never moves the role off a healthy server (on
        2026-10-23 a re-provisioned camera's new ID sorted first and the swap
        wedged the other camera). Only when there is no such transmitter
        (controller start, or it dropped/was disabled) is one elected: the
        camera already reporting 'server' (the lowest, if several claim it
        after a reconnect, so there is never a second server), else the
        lowest module_id.

        Clients are only touched once the transmitter has confirmed
        'server' (its echoed true_config, not PENDING). A module echoes
        set_config after its camera restart returns, so this restarts the
        server first and its clients after; a client that starts while its
        server is restarting wedges in libcamera 0.5.2. Clients are pinned
        to the transmitter's *confirmed* fps/sensor_mode_index, so they
        follow what it actually runs; that also covers a transmitter
        settings change from the web UI or PATCH /api/v1, which re-enters
        here on the transmitter's config echo.

        Called whenever a camera's config is confirmed, whenever any module's
        online status changes, after recording_stopped, and when a camera's
        config sync times out. Safe to call redundantly: a no-op once every
        online camera matches its target, and a camera whose PENDING target
        already holds the wanted values isn't pushed again.
        """
        modules = self.controller.modules
        states = self._camera_config_states()

        def online(mid: str) -> bool:
            module = modules._modules.get(mid)
            return bool(module and module.online)

        def camera_cfg(mid: str) -> dict:
            return (states[mid].true_config or {}).get("camera", {})

        if time.monotonic() - self._started_mono < self._FRAMESYNC_COLD_START_S:
            waiting = sorted(
                mid for mid, module in modules._modules.items()
                if "camera" in module.type and module.online
                and not (mid in states and states[mid].true_config)
            )
            if waiting:
                self.logger.debug(
                    f"FrameSync reconcile: waiting for first config from {waiting}")
                return {}

        candidates = sorted(
            mid for mid in states
            if online(mid) and camera_cfg(mid).get("framesync_enabled")
        )
        claims = [m for m in candidates if camera_cfg(m).get("sync_mode") == "server"]
        if self._framesync_transmitter in candidates:
            transmitter_id = self._framesync_transmitter
        elif claims:
            transmitter_id = claims[0]
        else:
            transmitter_id = candidates[0] if candidates else None
        if transmitter_id != self._framesync_transmitter:
            self.logger.info(
                f"FrameSync transmitter: {self._framesync_transmitter} -> {transmitter_id}")
            self._framesync_transmitter = transmitter_id

        pin = None
        if transmitter_id:
            tcam = camera_cfg(transmitter_id)
            if (tcam.get("sync_mode") == "server"
                    and states[transmitter_id].status != ConfigSyncStatus.PENDING):
                pin = {k: tcam.get(k) for k in ("fps", "sensor_mode_index")
                       if tcam.get(k) is not None}

        roles = {}
        waiting_clients = []
        for mid, state in states.items():
            if not online(mid) or not state.true_config:
                continue  # nothing to base a push on until it reports
            target_role = (
                "server" if mid == transmitter_id
                else "client" if mid in candidates
                else "none"
            )
            roles[mid] = target_role
            wanted = {"sync_mode": target_role}
            if target_role == "client":
                if pin is None:
                    if camera_cfg(mid).get("sync_mode") != "client":
                        waiting_clients.append(mid)
                    continue
                wanted.update(pin)
            current = {"sync_mode": "none", **camera_cfg(mid)}
            if all(current.get(k) == v for k, v in wanted.items()):
                continue
            target_camera = (state.target_config or {}).get("camera", {})
            if (state.status == ConfigSyncStatus.PENDING
                    and all(target_camera.get(k) == v for k, v in wanted.items())):
                continue  # already sent; waiting on the echo
            # A role change restarts the camera, which must never happen
            # mid-recording (it orphans the running encoder).
            # Re-run on recording_stopped (Controller.handle_status_update).
            if self.is_module_recording(mid):
                self.logger.info(
                    f"FrameSync reconcile: {mid} should be {wanted} but is "
                    f"recording -- deferred until it stops")
                continue

            new_camera = dict(current)
            new_camera.update(wanted)
            new_config = dict(state.true_config or {})
            new_config["camera"] = new_camera
            # true_config carries the module's internal/private (_-prefixed) keys
            # verbatim; every other caller of set_target_module_config sends only
            # the filtered, public view (that's what a module's echoed-back
            # true_config is compared against in received_module_config), so an
            # unfiltered target_config here would show as a spurious mismatch on
            # every single reconcile push.
            new_config = modules._filter_private_keys(new_config)
            self.logger.info(f"FrameSync reconcile: {mid} -> {wanted}")
            self.set_target_module_config(mid, new_config)
            # Only send the camera section over the wire, not the full merged
            # new_config: state.true_config is a cache that can go stale for
            # sections nothing re-echoes after changing (e.g. export.share_password
            # stays "" forever in true_config, since set_export_config's own ack
            # carries no `config` field to refresh it -- see module.py's
            # set_export_config). Sending the full object would let the
            # module's set_all() persist those stale values (it wiped
            # share_password on restart).
            self.send_command(mid, "set_config", {"camera": new_config["camera"]})

        if waiting_clients and waiting_clients != self._framesync_waiting:
            self.logger.info(
                f"FrameSync reconcile: {waiting_clients} wait for {transmitter_id} "
                f"to confirm sync_mode=server")
        self._framesync_waiting = waiting_clients
        return roles

    def sync_export_to_module(self, module_id: str) -> dict:
        """Push this controller's saved export credentials to a single module."""
        creds = self.controller.get_export_credentials()
        if not creds:
            return {"success": False, "error": "No export credentials configured on controller"}
        return self._push_export_creds_to_module(module_id, creds)

    def sync_export_with_creds(self, module_id: str, creds: dict) -> dict:
        """Push explicit export credentials to a single module."""
        return self._push_export_creds_to_module(module_id, creds)

    def _push_export_creds_to_module(self, module_id: str, creds: dict) -> dict:
        """Send set_export_config to a module, persisting to base_config.json on the module."""
        modules = self.controller.modules.get_modules()
        if module_id not in modules:
            return {"success": False, "error": "Module not found"}
        self.controller.communication.send_command(module_id, "set_export_config", creds)
        return {"success": True}


    """Events"""
    """Export Queue"""
    def enqueue_export(self, module_id: str, export_path: str,
                       staged_bytes: int | None = None,
                       file_count: int | None = None) -> None:
        self.controller.export_queue.note_staged(module_id, staged_bytes, file_count)
        # export_queue.enqueue() drops export_ready signals for a module that
        # is already queued/active as duplicates -- only count "pending" for
        # a signal it actually accepted, or pending_exports drifts upward
        # forever on the dropped ones (a module producing several export_ready
        # signals close together, e.g. habitat_camera closing multiple clips,
        # never gets a matching complete/failed for the duplicates).
        if self.controller.export_queue.enqueue(module_id, export_path):
            self.controller.recording.module_export_update(module_id, export_path, "pending")
        self.export_queue_changed()

    def export_complete(self, module_id: str, export_path: str = "") -> None:
        self.controller.export_queue.on_export_complete(module_id)
        self.controller.recording.module_export_update(module_id, export_path, "complete")
        self.export_queue_changed()

    def export_failed(self, module_id: str, export_path: str = "") -> None:
        is_final = self.controller.export_queue.on_export_failed(module_id)
        self.controller.recording.module_export_update(module_id, export_path, "failed", final=is_final)
        self.export_queue_changed()

    def export_progress(self, module_id: str, progress: dict) -> None:
        self.controller.export_queue.update_progress(module_id, progress)
        self.export_queue_changed()

    def get_export_queue(self) -> dict:
        return self.controller.export_queue.snapshot()

    def export_queue_changed(self) -> None:
        """Push the export queue (who's exporting, how far, who's waiting)
        to browsers -- the session page's per-module export rows."""
        try:
            self.controller.web.socketio.emit(
                "export_queue", self.controller.export_queue.snapshot())
        except Exception:  # noqa: BLE001 -- display only; never break export handling
            self.logger.debug("export_queue emit failed", exc_info=True)

    def module_offline(self, module_id: str) -> None:
        # Tell anyone who cares that a module has gone offline
        self.controller.recording.module_offline(module_id)

    def report_module_fault(self, module_id: str, message: str) -> None:
        # A module reported a recording failure directly (rather than the
        # controller inferring it from a missed heartbeat/poll)
        self.controller.recording.report_module_fault(module_id, message)


    def handle_recording_health_status(self, module_id: str, status: str,
                                       message: str | None,
                                       source: str = "liveness") -> None:
        # A module's self-monitor reported its recording capture is
        # unhealthy/recovered (see Recording._monitor_recording_health on
        # the module side), or its local disk is running low (source="disk")
        # — softer than report_module_fault above, surfaced as a warning
        # rather than a session ERROR.
        self.controller.recording.handle_recording_health_status(
            module_id, status, message, source)


    def module_recording_started(self, module_id: str) -> None:
        # A module confirmed it is recording: close its open data gaps
        # (session_gaps.json). See Recording.module_recording_started.
        self.controller.recording.module_recording_started(module_id)


    def module_self_stopped(self, module_id: str, data: dict) -> None:
        # A module stopped recording on its own to protect itself (e.g. local
        # disk critically low) rather than because an operator asked --
        # a fault, not a clean stop. See Recording.module_self_stopped.
        self.controller.recording.module_self_stopped(module_id, data)


    def module_back_online(self, module_id: str) -> None:
        # What to do when a module comes back online
        self.controller.export_queue.module_back_online(module_id)
        self.controller.recording.module_back_online(module_id)
        self.export_queue_changed()

    def notify_module_recording(self, module_id: str) -> None:
        """Mark a module as RECORDING in the modules tracker immediately,
        before the start_recording ack arrives, so the session monitor does
        not see a brief discrepancy and raise a spurious error."""
        self.controller.modules.notify_recording_started(module_id, {"recording": True})

    def handle_module_health_for_recovery(self, module_id: str, is_recording: bool) -> None:
        self.controller.recording.handle_module_health_response(module_id, is_recording)

    def module_rediscovered(self, module_id: str):
        self.controller.health.module_rediscovered(module_id)
        self.controller.modules.module_rediscovered(module_id)


    def module_discovery(self, module):
        # If a module at the same IP exists under a different ID, the device has
        # changed role (switch_role.sh) or its IP was reassigned by DHCP. A device
        # can only be one thing at a time, so evict the old entry — the new mDNS
        # announcement is proof the old identity is gone. _remove_module cleans up
        # both modules and health tracking in one call.
        if module.ip:
            for existing_id, existing in list(self.controller.modules._modules.items()):
                if existing.ip == module.ip and existing_id != module.id:
                    self.logger.info(
                        f"New module {module.id} at {module.ip} displacing "
                        f"{'online' if existing.online else 'offline'} "
                        f"{existing_id} — auto-removing"
                    )
                    self.controller._remove_module(existing_id)
                    break

        self.controller.modules.module_discovery(module)
        self.controller.health.module_discovery(module)
        # Fetch config immediately so module name is known before any card is opened
        self.controller.communication.send_command(module.id, "get_config", {})
        # Push current export credentials so modules always point at this controller
        creds = self.controller.get_export_credentials()
        if creds:
            self.controller.communication.send_command(module.id, "set_export_config", creds)


    def module_id_changed(self, old_module_id: str, new_module_id: str) -> None:
        self.controller.modules.module_id_changed(old_module_id, new_module_id)
        self.controller.health.module_id_changed(old_module_id, new_module_id)


    def update_module_version(self, module_id: str, version: str) -> None:
        self.controller.modules.update_module_version(module_id, version)

    def module_ip_changed(self, module_id: str, new_module_ip: str) -> None:
        self.controller.modules.module_ip_changed(module_id, new_module_ip)


    """Notifications"""
    def send_alert(self, key: str, title: str, message: str, severity: str = "error") -> None:
        # Mirror every alert onto the /api/v1/events SSE stream first --
        # unlike the Teams path this does not depend on a webhook being
        # configured or the controller having internet.
        try:
            self.controller.web._publish_api_event("alert", {
                "key": key, "title": title,
                "message": message, "severity": severity,
            })
        except Exception:
            pass
        self.controller.notifier.send_alert(key, title, message, severity)

    def check_internet(self) -> bool:
        return self.controller.notifier.check_internet()
