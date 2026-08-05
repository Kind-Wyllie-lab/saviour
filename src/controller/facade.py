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


class ControllerFacade:
    def __init__(self, controller):
        self.logger = logging.getLogger(__name__)
        self.logger.info("Instantiating ControllerAPI...")
        self.controller = controller


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


    def get_samba_info(self):
        return self.controller.get_samba_info()

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


    def get_recording_status(self) -> bool:
        return self.controller.recording.get_recording_status()


    def get_recording_sessions(self) -> dict:
        return self.controller.recording.get_recording_sessions()


    def get_system_state(self) -> dict:
        return {
            "example": "This is an example system state object",
            "recording": self.get_recording_status(),
            "uptime": self.get_uptime(), # Uptime in minutes
            "ptp_sync": self.get_ptp_sync() # Largest ptp4l_offset_ns across modules, in nanoseconds
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

    def create_session(self, session_name: str, target: str, duration_minutes=None, researcher=None, raw_name=False) -> dict:
        return self.controller.recording.create_session(session_name, target, duration_minutes, researcher, raw_name)

    def create_scheduled_session(self, session_name: str, target: str, start_time: str, end_time: str, days=None, researcher=None, raw_name=False) -> dict:
        return self.controller.recording.create_scheduled_session(session_name, target, start_time, end_time, days, researcher, raw_name)

    def stop_session(self, session_name: str) -> None:
        return self.controller.recording.stop_session(session_name)

    def force_start_scheduled_session(self, session_name: str) -> dict:
        return self.controller.recording.force_start_scheduled_session(session_name)

    def delete_session(self, session_name: str, delete_files: bool = True) -> dict:
        return self.controller.recording.delete_session(session_name, delete_files)

    def clear_ended_sessions(self, delete_files: bool = False) -> dict:
        return self.controller.recording.clear_ended_sessions(delete_files)

    def add_module_to_session(self, session_name: str, module_id: str) -> dict:
        return self.controller.recording.add_module_to_session(session_name, module_id)

    def module_stopped(self, module_id: str) -> None:
        self.controller.recording.module_stopped(module_id)


    def update_sessions(self, sessions: dict) -> None:
        serializable_sessions = {k: asdict(v) for k, v in sessions.items()}
        self.controller.web.socketio.emit("sessions_update", serializable_sessions)

    """Set config"""
    def set_config(self, new_config: dict) -> bool:
        self.controller.config.set_all(new_config, persist=True)
        updated_config = self.controller.config.get_all()
        return new_config == updated_config

    """Module Management"""
    def is_module_recording(self, module_id: str) -> bool:
        return self.controller.modules.is_module_recording(module_id)


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

    def reconcile_framesync(self) -> dict:
        """Ensure exactly one online, FrameSync-enabled camera is the sync
        transmitter ('server'), every other online enabled camera is a
        'client' pinned to its fps/sensor_mode_index, and any camera that is
        no longer enabled (or has gone offline) is corrected to 'none' once
        reachable.

        Deterministic and stateless by design: the elected transmitter is
        always the lowest module_id among currently online+enabled cameras,
        recomputed fresh on every call — it never trusts a camera's own
        self-reported `sync_mode` as an input, only `framesync_enabled`
        (user intent, stable across reconnects) and liveness. This is what
        prevents a module that reconnects still locally believing it's the
        transmitter from ever being treated as one: its stale `sync_mode`
        is only ever compared against, never read as a vote.

        Called whenever a camera's config is confirmed (so a toggled
        checkbox or a reconnecting module's fresh config gets picked up)
        and whenever any module's online status changes (so a dropped
        transmitter is replaced promptly rather than waiting on an
        unrelated config echo). Safe to call redundantly — a no-op once
        every online camera's sync_mode already matches its target role.
        """
        modules = self.controller.modules
        states = self._camera_config_states()

        def online(mid: str) -> bool:
            module = modules._modules.get(mid)
            return bool(module and module.online)

        def camera_cfg(mid: str) -> dict:
            return (states[mid].true_config or {}).get("camera", {})

        candidates = sorted(
            mid for mid in states
            if online(mid) and camera_cfg(mid).get("framesync_enabled")
        )
        transmitter_id = candidates[0] if candidates else None

        transmitter_params = None
        if transmitter_id:
            tcam = camera_cfg(transmitter_id)
            transmitter_params = {
                "fps": tcam.get("fps"),
                "sensor_mode_index": tcam.get("sensor_mode_index"),
            }

        roles = {}
        for mid, state in states.items():
            if not online(mid):
                continue
            target_role = (
                "server" if mid == transmitter_id
                else "client" if mid in candidates
                else "none"
            )
            roles[mid] = target_role
            if camera_cfg(mid).get("sync_mode", "none") == target_role:
                continue

            new_camera = dict(camera_cfg(mid))
            new_camera["sync_mode"] = target_role
            if target_role == "client" and transmitter_params:
                if transmitter_params["fps"] is not None:
                    new_camera["fps"] = transmitter_params["fps"]
                if transmitter_params["sensor_mode_index"] is not None:
                    new_camera["sensor_mode_index"] = (
                        transmitter_params["sensor_mode_index"]
                    )

            new_config = dict(state.true_config or {})
            new_config["camera"] = new_camera
            self.logger.info(f"FrameSync reconcile: {mid} -> sync_mode={target_role}")
            self.set_target_module_config(mid, new_config)
            self.send_command(mid, "set_config", new_config)

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
    def enqueue_export(self, module_id: str, export_path: str) -> None:
        self.controller.recording.module_export_update(module_id, export_path, "pending")
        self.controller.export_queue.enqueue(module_id, export_path)

    def export_complete(self, module_id: str, export_path: str = "") -> None:
        self.controller.export_queue.on_export_complete(module_id)
        self.controller.recording.module_export_update(module_id, export_path, "complete")

    def export_failed(self, module_id: str, export_path: str = "") -> None:
        self.controller.export_queue.on_export_failed(module_id)
        self.controller.recording.module_export_update(module_id, export_path, "failed")

    def module_offline(self, module_id: str) -> None:
        # Tell anyone who cares that a module has gone offline
        self.controller.recording.module_offline(module_id)


    def module_back_online(self, module_id: str) -> None:
        # What to do when a module comes back online
        self.controller.recording.module_back_online(module_id)

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
        self.controller.notifier.send_alert(key, title, message, severity)

    def check_internet(self) -> bool:
        return self.controller.notifier.check_internet()
