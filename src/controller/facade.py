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


class ControllerFacade():
    def __init__(self, controller):
        self.logger = logging.getLogger(__name__)
        self.logger.info("Instantiating ControllerAPI...")
        self.controller = controller

    
    """Getter Methods"""
    def get_modules(self) -> dict:
        return self.controller.modules.get_modules()


    def get_modules_by_target(self, target: str) -> dict:
        return self.controller.modules.get_modules_by_target(target)


    def get_module_health(self, module_id: Optional[str] = None):
        return self.controller.health.get_module_health(module_id)

    
    def get_discovered_modules(self) -> dict:
        return self.controller.network.get_modules()


    def get_module_config(self, module_id: str) -> dict:
        return self.controller.get_module_config(module_id)
        
    
    def get_module_configs(self) -> dict:
        return self.controller.get_module_configs()

    
    def get_samba_info(self):
        return self.controller.get_samba_info()


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
            "ptp_sync": self.get_ptp_sync() # Largest ptp offset from a module in ms
        }


    """Utilities"""
    def remove_module(self, module_id: str):
        self.controller._remove_module(module_id)


    def send_command(self, module_id: str, command: str, params: Dict) -> None:
        self.controller.communication.send_command(module_id, command, params)
            

    """Callbacks"""
    def on_status_change(self, module_id: str, status: str):
        self.controller.on_module_status_change(module_id, status)


    def notify_module_update(self, discovered_modules: dict):
        self.controller.network_notify_module_update(discovered_modules)


    def notify_module_id_change(self, old_id: str, new_id: str):
        self.controller.network_notify_module_id_change(old_id, new_id)


    def notify_module_ip_change(self, id: str, new_ip: str):
        pass


    def push_module_update_to_frontend(self, modules: dict):
        self.controller.web.push_module_update(modules)


    """Recording"""
    def start_recording(self, target: str, session_name: str, duration: int):
        return self.controller.recording.start_recording(target, session_name, duration)

    
    def stop_recording(self, target: str):
        return self.controller.recording.stop_recording(target)


    """Set config"""
    def set_config(self, new_config: dict) -> bool:
        self.controller.config.set_all(new_config)
        updated_config = self.controller.config.get_all()
        if new_config != updated_config:
            return False
        else: 
            return True

