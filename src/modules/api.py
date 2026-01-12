#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module API

This class is used to glue the various other classes that comprise a module together.

It provides an interface for module objects to interact with one another, e.g. the communication object getting module_id from the main program, or the export object querying controller_ip from the network object.

Note this is an internal API for use between parts of the module program. An External API for the controller-module relationship would be a separate concern and does not yet exist.

Author: Andrew SG
Created: 12/01/2026
"""

import logging
import os
from typing import Dict, Any, Optional

class ModuleAPI():
    def __init__(self, module):
        self.logger = logging.getLogger(__name__)
        self.logger.info("Instantiating ModuleAPI...")
        self.module = module


    """Getter Methods"""
    def get_module_id(self) -> str:
        return self.module.module_id

    def get_controller_ip(self) -> str:
        return self.module.network.controller_ip

    
    def get_recording_folder(self) -> str:
        return self.module.recording.recording_folder
    

    def get_recording_status(self) -> bool:
        return self.module.recording.is_recording
    
    
    def get_ptp_status(self) -> dict:
        return self.module.ptp.get_status()


    """Utility Methods"""
    def generate_session_id(self, module_id: str) -> str:
        return self.module.generate_session_id(module_id)


    """Communication Methods"""
    def send_status(self, status_data: Dict[str, Any]) -> None:
        """Send a response to the controller"""
        self.module.communication.send_status(status_data)

    def handle_command(self, raw_command: str) -> None:
        """Handle an incoming command from the controller"""
        self.module.command.handle_command(raw_command)


    """Event callbacks"""
    def on_module_config_change(self, updated_keys: Optional[list[str]]) -> None:
        """When the module config changes, this is triggered"""
        self.module.on_module_config_change(updated_keys)
    
    
    def when_controller_discovered(self, controller_ip: str, controller_port: int) -> None:
        """When the Network object discovers a controller via mDNS/zeroconf, this gets triggered""" 
        self.module.when_controller_discovered(controller_ip, controller_port)

    
    def controller_disconnected(self) -> None:
        self.module.controller_disconnected()

    




