#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Facade

It provides an interface for module objects to interact with one another, e.g. the communication object getting module_id from the main program, or the export object querying controller_ip from the network object.

Author: Andrew SG
Created: 12/01/2026
"""

import logging
import os
from typing import Dict, Any, Optional

class ModuleFacade():
    def __init__(self, module):
        self.logger = logging.getLogger(__name__)
        self.logger.info("Instantiating ModuleAPI...")
        self.module = module


    """Getter Methods"""
    def get_module_id(self) -> str:
        return self.module.module_id


    def get_module_name(self) -> str:
        return self.module.get_module_name()

    
    def get_module_type(self) -> str:
        return self.module.module_type


    def get_module_group(self) -> str:
        return self.module.get_module_group()


    def get_controller_ip(self) -> str:
        return self.module.network.controller_ip

    
    def get_recording_folder(self) -> str:
        return self.module.recording.recording_folder
    

    def get_to_export_folder(self) -> str:
        return self.module.export.to_export_folder

    
    def get_exported_folder(self) -> str:
        return self.module.export.exported_folder


    def get_recording_status(self) -> bool:
        return self.module.recording.is_recording


    def get_recording_session_id(self) -> str:
        return self.module.recording_session_id

    
    def get_staged_files(self) -> list:
        return self.module.export.staged_for_export
    
    
    def get_ptp_status(self) -> dict:
        return self.module.ptp.get_status()

    
    def get_health(self) -> dict:
        return self.module.health.get_health()


    def get_segment_id(self) -> int:
        return self.module.recording.segment_id


    def get_segment_start_time(self) -> int:
        return self.module.recording.segment_start_time


    def get_utc_time(self, timestamp: int) -> str:
        return self.module.get_utc_time(timestamp)

    
    def get_utc_date(self, timestamp: int) -> str:
        return self.module.get_utc_date(timestamp)


    def get_filename_prefix(self) -> str:
        """Return the prefix for all recorded files - typically looks like <recording_folder>/<session_name>_<recording_session_id> e.g. rec/habitat_wistar21_C2_1"""
        return self.module.recording.current_filename_prefix


    def get_module_name(self) -> str:
        return self.module.get_module_name()

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


    """Module Specific Recording Methods"""
    def start_new_recording(self) -> bool:
        """Implement module specific logic to start initial recording segment."""
        return self.module._start_new_recording()


    def start_next_recording_segment(self) -> bool:
        """Implement module specific logic to start next recording segment."""
        return self.module._start_next_recording_segment()


    def stop_recording(self) -> bool:
        """Implement module specific stop recording logic."""
        self.logger.info("Executing module specific stop recording functionality")
        return self.module._stop_recording()


    """File Export"""
    def get_current_session_name(self) -> str:
        return self.module.recording.current_session_name


    def export_staged(self, export_path: str):
        return self.module.export.export_staged(export_path)

    
    def stage_file_for_export(self, filename: str) -> None:
        """Stage a file for export when next segment starts or recording is stopped."""
        self.module.export.stage_file_for_export(filename)

    
    def add_session_file(self, filename: str) -> None:
        """Add a file to the recording session - mayb be redundant with stage_file_for_export"""
        self.module.export.add_session_file(filename)


    def _export_files(self, files: list):
        """Exports all files in the to_export list"""
        try:
            # Use the export manager's method for consistency
            if self.module.export.export_current_session_files(
                session_files=files,
                recording_folder=self.facade.get_recording_folder(),
                recording_session_id=self.facade.get_recording_session_id(),
                session_name=self.facade.get_current_session_name()
            ):
                self.logger.info("Auto-export completed successfully")

                if self.module.config.get("delete_on_export", True):
                    self.module._clear_recordings(filenames=files)
                    self.module._clear_exported_files_from_session_files()
                    self.module.recording.to_export = [] # empty the list of files to export
            else:
                self.logger.warning("Auto-export failed, but recording was successful")
        except Exception as e:
            self.logger.error(f"Auto-export error: {e}")


    """Event callbacks"""
    def on_module_config_change(self, updated_keys: Optional[list[str]]) -> None:
        """When the module config changes, this is triggered"""
        self.module.on_module_config_change(updated_keys)
    
    
    def when_controller_discovered(self, controller_ip: str, controller_port: int) -> None:
        """When the Network object discovers a controller via mDNS/zeroconf, this gets triggered""" 
        self.module.when_controller_discovered(controller_ip, controller_port)

    
    def controller_disconnected(self) -> None:
        self.module.controller_disconnected()


    def when_recording_starts(self):
        self.module.export.when_recording_starts()        
    

    """Network"""
    def subscribe_to_topic(self, topic: str):
        """Subscribe to commands for supplied topic."""
        self.module.communication.subscribe_to_topic(topic)

    
    def unsubscribe_from_topic(self, topic: str):
        """Unsubscribe from commands related to given topic. Typically used when module changes group."""
        self.module.communication.unsubscribe_from_topic(topic)



