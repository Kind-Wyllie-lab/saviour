#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Facade

This class provides an interface for controller objects to interact with one another.

Author: Andrew SG
Created: 20/01/2026
"""

import logging
import os
from typing import Dict, Any, Optional
import time
from dataclasses import asdict


class ControllerFacade():
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


    def get_module_health(self, module_id: Optional[str] = None):
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


    def get_share_path(self):
        return "/home/pi/controller_share"


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
            "ptp_sync": self.get_ptp_sync() # Largest ptp4l_offset across modules, in nanoseconds
        }


    """Utilities"""
    def remove_module(self, module_id: str):
        self.controller._remove_module(module_id)


    def send_command(self, module_id: str, command: str, params: Dict) -> None:
        self.controller.communication.send_command(module_id, command, params)
            

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

    def create_session(self, session_name: str, target: str) -> dict:
        return self.controller.recording.create_session(session_name, target)

    def create_scheduled_session(self, session_name: str, target: str, start_time: str, end_time: str) -> dict:
        return self.controller.recording.create_scheduled_session(session_name, target, start_time, end_time)

    def stop_session(self, session_name: str) -> None:
        return self.controller.recording.stop_session(session_name)

    def delete_session(self, session_name: str, delete_files: bool = True) -> dict:
        return self.controller.recording.delete_session(session_name, delete_files)

    def add_module_to_session(self, session_name: str, module_id: str) -> dict:
        return self.controller.recording.add_module_to_session(session_name, module_id)

    def module_stopped(self, module_id: str) -> None:
        self.controller.recording.module_stopped(module_id)

    
    def update_sessions(self, sessions: dict) -> None:
        serializable_sessions = {k: asdict(v) for k, v in sessions.items()}
        self.controller.web.socketio.emit("sessions_update", serializable_sessions)

    """Set config"""
    def set_config(self, new_config: dict) -> bool:
        self.controller.config.set_all(new_config)
        updated_config = self.controller.config.get_all()
        if new_config != updated_config:
            return False
        else: 
            return True

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


    def sync_export_to_module(self, module_id: str) -> dict:
        """Push this controller's Samba credentials into a single module's export config.

        Returns {"success": True} or {"success": False, "error": "..."}.
        """
        creds = self.controller.get_export_credentials()
        if not creds:
            return {"success": False, "error": "Credentials file not found on controller"}
        result = self.controller.modules.apply_section_to_module(module_id, "export", creds)
        if result is None:
            return {"success": False, "error": "Module not found or config not yet confirmed"}
        _, config = result
        self.controller.communication.send_command(module_id, "set_config", config)
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

    def handle_module_health_for_recovery(self, module_id: str, is_recording: bool) -> None:
        self.controller.recording.handle_module_health_response(module_id, is_recording)

    def module_rediscovered(self, module_id: str):
        self.controller.health.module_rediscovered(module_id)
        self.controller.modules.module_rediscovered(module_id)


    def module_discovery(self, module):
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
    

    def module_ip_changed(self, module_id: str, new_module_ip: str) -> None:
        self.controller.modules.module_ip_changed(module_id, new_module_ip)

