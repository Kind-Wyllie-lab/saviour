#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller API

This class is used to glue the various other classes that comprise a controller together.

It provides an interface for controller objects to interact with one another.

Note this is an internal API for use between parts of the controller program. An External API for the controller-module relationship would be a separate concern and does not yet exist.

Author: Andrew SG
Created: 20/01/2026
"""

import logging
import os
from typing import Dict, Any, Optional


class ControllerAPI():
    def __init__(self, controller):
        self.logger = logging.getLogger(__name__)
        self.logger.info("Instantiating ControllerAPI...")
        self.controller = controller

    
    """Getter Methods"""
    def get_modules(self) -> dict:
        return self.controller._get_modules_for_frontend()


    def send_command(self, module_id: str, command: str, params: Dict) -> None:
        self.controller.communication.send_command(module_id, command, params)


    def get_module_health(self, module_id: Optional[str] = None):
        return self.controller.health.get_module_health(module_id)

    
    def get_discovered_modules(self) -> dict:
        return self.controller.network.get_modules()

    
    def get_module_configs(self) -> dict:
        return self.controller.get_module_configs()

    
    def get_samba_info(self):
        return self.controller.get_samba_info()


    def remove_module(self, module_id: str):
        self.controller._remove_module(module_id)


    def get_config(self) -> dict:
        return self.controller.config.get_all()


    def get_system_state(self) -> dict:
        return {
            "example": "This is an example system state object",
            "recording": True,
            "uptime": 1357, # Uptime in minutes
            "ptp_sync": 6 # Largest ptp offset from a module in ms
        }



            
        # Register status change callback with health monitor
        self.health.set_callbacks({
            "on_status_change": self.on_module_status_change,
            "send_command": self.communication.send_command
        })

        self.network.notify_module_update = self.network_notify_module_update
        self.network.notify_module_id_change = self.network_notify_module_id_change

        self.modules.push_module_update_to_frontend = self.web.push_module_update

    """Set config"""
    def set_config(self, new_config: dict) -> bool:
        self.controller.config.set_all(new_config)
        updated_config = self.controller.config.get_all()
        if new_config != updated_config:
            return False
        else: 
            return True

