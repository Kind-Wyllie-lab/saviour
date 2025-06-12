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
import datetime
from typing import Union

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
        
    def _create_export_manifest(self, files_to_export: list, destination: Union[str, 'ExportManager.ExportDestination'], export_folder: str) -> str:
        """Create an export manifest file listing all files to be exported
        
        Args:
            files_to_export: List of filenames that will be exported
            destination: Where the files will be exported to (string or enum)
            export_folder: Path to the folder where files will be exported
            
        Returns:
            str: Name of the created manifest file
        """
        try:
            manifest_timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            manifest_filename = f"export_manifest_{manifest_timestamp}.txt"
            manifest_path = os.path.join(export_folder, manifest_filename)
            
            # Handle both string and enum destination values
            destination_str = destination.value if hasattr(destination, 'value') else str(destination)
            
            with open(manifest_path, 'w') as f:
                f.write(f"Export Manifest - {manifest_timestamp}\n")
                f.write(f"Module ID: {self.module_id}\n")
                f.write(f"Destination: {destination_str}\n")
                f.write(f"Export Folder: {os.path.basename(export_folder)}\n")
                f.write(f"Files to be exported:\n")
                for file in files_to_export:
                    f.write(f"- {file}\n")
                    # Add file size and modification time from source
                    file_path = os.path.join(self.recording_folder, file)
                    if os.path.exists(file_path):
                        stat = os.stat(file_path)
                        size_mb = stat.st_size / (1024 * 1024)  # Convert to MB
                        mod_time = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                        f.write(f"  Size: {size_mb:.2f} MB\n")
                        f.write(f"  Modified: {mod_time}\n")
            
            self.logger.info(f"(EXPORT MANAGER) Created export manifest: {manifest_filename}")
            return manifest_filename
            
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Failed to create export manifest: {e}")
            return None

    def export_file(self, filename: str, destination: 'ExportManager.ExportDestination') -> bool:
        """Export a single file to the specified destination
        
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
                    
            # Create timestamped export folder
            export_timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            export_folder = os.path.join(self.mount_point, f"export_{export_timestamp}")
            os.makedirs(export_folder, exist_ok=True)
            
            # Copy the file
            source_path = os.path.join(self.recording_folder, filename)
            dest_path = os.path.join(export_folder, filename)
            shutil.copy2(source_path, dest_path)
            
            # Copy timestamps if they exist
            timestamp_file = f"{base_name}_timestamps.txt"
            timestamp_source = os.path.join(self.recording_folder, timestamp_file)
            timestamp_dest = os.path.join(export_folder, timestamp_file)
            
            exported_files = [filename]
            if os.path.exists(timestamp_source):
                shutil.copy2(timestamp_source, timestamp_dest)
                exported_files.append(timestamp_file)
            
            # Create export manifest
            manifest_filename = self._create_export_manifest(exported_files, destination, export_folder)
            if not manifest_filename:
                self.logger.error("(EXPORT MANAGER) Failed to create export manifest")
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Export failed: {e}")
            return False

    def export_all_files(self, destination: 'ExportManager.ExportDestination') -> bool:
        """Export all files in the recording folder to the specified destination
        
        Args:
            destination: Where to export to (NAS or Controller)
            
        Returns:
            bool: True if export successful
        """
        try:
            # Mount the destination if not already mounted
            if self.current_mount != destination:
                if not self._mount_destination(destination):
                    return False
                    
            # Create timestamped export folder
            export_timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            export_folder = os.path.join(self.mount_point, f"export_{export_timestamp}")
            os.makedirs(export_folder, exist_ok=True)
            
            # First, identify all files to export
            files_to_export = []
            for filename in os.listdir(self.recording_folder):
                # Skip non-recording files
                if not filename.startswith('REC_'):
                    continue
                    
                # Skip timestamp files as they'll be handled with their main files
                if filename.endswith('_timestamps.txt'):
                    continue
                    
                # Add main file
                files_to_export.append(filename)
                
                # Add timestamps file if it exists
                base_name = os.path.splitext(filename)[0]  # Remove extension
                timestamp_file = f"{base_name}_timestamps.txt"
                if os.path.exists(os.path.join(self.recording_folder, timestamp_file)):
                    files_to_export.append(timestamp_file)
            
            # Create manifest first
            manifest_filename = self._create_export_manifest(files_to_export, destination, export_folder)
            if not manifest_filename:
                self.logger.error("(EXPORT MANAGER) Failed to create export manifest")
                return False
            
            # Now export all files
            for filename in files_to_export:
                source_path = os.path.join(self.recording_folder, filename)
                dest_path = os.path.join(export_folder, filename)
                try:
                    shutil.copy2(source_path, dest_path)
                    self.logger.info(f"(EXPORT MANAGER) Exported file: {filename}")
                except Exception as e:
                    self.logger.error(f"(EXPORT MANAGER) Failed to export file {filename}: {e}")
                    return False
            
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
            
    def _mount_controller(self) -> bool:
        """Mount the controller's Samba share"""
        try:
            # Get controller IP from callback
            controller_ip = self.callbacks['get_controller_ip']()
            if not controller_ip:
                self.logger.error("(EXPORT MANAGER) Could not get controller IP from callback")
                return False
                
            share_path = self.config.get('controller_share_path', '/share')
            username = self.config.get('controller_username', 'pi')
            password = self.config.get('controller_password', 'pass')
                
            # Unmount if already mounted
            if os.path.ismount(self.mount_point):
                subprocess.run(['sudo', 'umount', self.mount_point], check=True)
                
            # Mount the Samba share with credentials
            mount_cmd = [
                'sudo', 'mount', '-t', 'cifs',
                f'//{controller_ip}/{share_path}',
                self.mount_point,
                '-o', f'username={username},password={password},vers=3.0'
            ]
            
            # Run mount command and capture output
            result = subprocess.run(mount_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                self.logger.error(f"(EXPORT MANAGER) Failed to mount controller share: {result.stderr}")
                return False
                
            self.logger.info(f"(EXPORT MANAGER) Successfully mounted controller share at {self.mount_point}")
            self.current_mount = ExportManager.ExportDestination.CONTROLLER
            return True
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"(EXPORT MANAGER) Failed to mount controller share: {e.stderr if hasattr(e, 'stderr') else str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Controller mount failed: {str(e)}")
            return False
            
    def _mount_nas(self) -> bool:
        """Mount the NAS share"""
        try:
            nas_ip = self.config.get('nas_ip')
            share_path = self.config.get('nas_share_path', '/share')
            recordings_path = self.config.get('nas_recordings_path', 'recordings')
            username = self.config.get('nas_username')
            password = self.config.get('nas_password')
            
            if not all([nas_ip, username, password]):
                self.logger.error("(EXPORT MANAGER) Missing NAS configuration (IP, username, or password)")
                return False
                
            # Unmount if already mounted
            if os.path.ismount(self.mount_point):
                subprocess.run(['sudo', 'umount', self.mount_point], check=True)
                
            # Mount the NAS share with credentials
            mount_cmd = [
                'sudo', 'mount', '-t', 'cifs',
                f'//{nas_ip}/{share_path}',
                self.mount_point,
                '-o', f'username={username},password={password}'
            ]
            
            subprocess.run(mount_cmd, check=True)
            
            # Create recordings folder and module-specific subfolder on NAS if they don't exist
            recordings_folder = os.path.join(self.mount_point, recordings_path)
            module_folder = os.path.join(recordings_folder, self.module_id)
            
            try:
                # Create recordings folder first
                os.makedirs(recordings_folder, exist_ok=True)
                self.logger.info(f"(EXPORT MANAGER) Created/verified recordings folder on NAS: {recordings_folder}")
                
                # Then create module folder inside recordings
                os.makedirs(module_folder, exist_ok=True)
                self.logger.info(f"(EXPORT MANAGER) Created/verified module folder on NAS: {module_folder}")
            except Exception as e:
                self.logger.error(f"(EXPORT MANAGER) Failed to create folders on NAS: {e}")
                # Don't return False here as the mount was successful
            
            self.logger.info(f"(EXPORT MANAGER) Successfully mounted NAS share at {self.mount_point}")
            self.current_mount = ExportManager.ExportDestination.NAS
            return True
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"(EXPORT MANAGER) Failed to mount NAS share: {e}")
            return False
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) NAS mount failed: {e}")
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