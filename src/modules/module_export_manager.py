#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOSDACS System - Module Export Manager

This class is used to send files to either a controller or NAS running a samba server.

Author: Andrew SG
Created: 12/06/2025
License: GPLv3
"""

import os
import shutil
from enum import Enum
import logging
import subprocess

class ExportManager:
    """Manages file exports to different destinations (NAS or Controller)"""
    
    class ExportDestination(Enum):
        """Enum for export destinations"""
        CONTROLLER = "controller"
        NAS = "nas"

        @classmethod
        def from_string(cls, value: str) -> 'ExportManager.ExportDestination':
            """Convert string to ExportDestination enum"""
            try:
                return cls(value.lower())
            except ValueError:
                raise ValueError(f"Invalid destination: {value}. Must be one of: {[d.value for d in cls]}")
    
    def __init__(self, module_id: str, recording_folder: str, config: dict, logger: logging.Logger):
        self.module_id = module_id
        self.recording_folder = recording_folder
        self.config = config
        self.logger = logger
        self.current_mount = None
        self.mount_point = "/mnt/export"  # Could be configurable
        self.callbacks = {}
        
        # Create mount point directory if it doesn't exist
        try:
            os.makedirs(self.mount_point, exist_ok=True)
            self.logger.info(f"(EXPORT MANAGER) Created mount point directory: {self.mount_point}")
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Failed to create mount point directory: {e}")
        
    def set_callbacks(self, callbacks: dict):
        """Set callbacks for the export manager
        
        Args:
            callbacks: Dictionary of callback functions
                - 'get_controller_ip': Callback to get controller IP
        """
        # Validate required callbacks
        required_callbacks = ['get_controller_ip']
        missing_callbacks = [cb for cb in required_callbacks if cb not in callbacks]
        if missing_callbacks:
            raise ValueError(f"Missing required callbacks: {missing_callbacks}")
            
        self.callbacks = callbacks
        
    def export_file(self, filename: str, destination: 'ExportManager.ExportDestination') -> bool:
        """Export a file to the specified destination
        
        Args:
            filename: Name of the file to export
            destination: Where to export to (NAS or Controller)
            
        Returns:
            bool: True if export successful
        """
        try:
            # Mount the destination if not already mounted
            if self.current_mount != destination:
                if not self._mount_destination(destination):
                    return False
                    
            # Copy file to mounted destination
            source_path = os.path.join(self.recording_folder, filename)
            dest_path = os.path.join(self.mount_point, filename)
            
            # Copy file
            shutil.copy2(source_path, dest_path)
            
            # Copy timestamps if they exist
            timestamp_file = f"{filename}_timestamps.txt"
            if os.path.exists(os.path.join(self.recording_folder, timestamp_file)):
                shutil.copy2(
                    os.path.join(self.recording_folder, timestamp_file),
                    os.path.join(self.mount_point, timestamp_file)
                )
                
            return True
            
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Export failed: {e}")
            return False
            
    def _mount_destination(self, destination: 'ExportManager.ExportDestination') -> bool:
        """Mount the specified destination
        
        Args:
            destination: Where to mount (NAS or Controller)
            
        Returns:
            bool: True if mount successful
        """
        try:
            if destination == ExportManager.ExportDestination.NAS:
                return self._mount_nas()
            else:
                return self._mount_controller()
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Mount failed: {e}")
            return False
            
    def _mount_nas(self) -> bool:
        """Mount the NAS share"""
        try:
            # Mount NAS using smbclient or mount.cifs
            # Example: mount -t cifs //nas_ip/share /mnt/export -o username=user,password=pass
            self.current_mount = ExportManager.ExportDestination.NAS
            return True
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) NAS mount failed: {e}")
            return False
            
    def _mount_controller(self) -> bool:
        """Mount the controller's Samba share"""
        try:
            # Get controller IP from callback
            controller_ip = self.callbacks['get_controller_ip']()
            if not controller_ip:
                self.logger.error("(EXPORT MANAGER) Could not get controller IP from callback")
                return False
                
            share_path = self.config.get('controller_share_path', '/share')
                
            # Unmount if already mounted
            if os.path.ismount(self.mount_point):
                subprocess.run(['sudo', 'umount', self.mount_point], check=True)
                
            # Mount the Samba share with credentials
            mount_cmd = [
                'sudo', 'mount', '-t', 'cifs',
                f'//{controller_ip}/{share_path}',
                self.mount_point,
                '-o', 'username=pi,password=pass'
            ]
            
            subprocess.run(mount_cmd, check=True)
            self.logger.info(f"(EXPORT MANAGER) Successfully mounted controller share at {self.mount_point}")
            self.current_mount = ExportManager.ExportDestination.CONTROLLER
            return True
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"(EXPORT MANAGER) Failed to mount controller share: {e}")
            return False
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Controller mount failed: {e}")
            return False
            
    def unmount(self) -> bool:
        """Unmount current destination"""
        try:
            if self.current_mount:
                # Unmount using umount
                # Example: umount /mnt/export
                self.current_mount = None
                return True
            return True
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Unmount failed: {e}")
            return False