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

class Export:
    """Manages file exports to different destinations (NAS or Controller)"""
    
    class ExportDestination(Enum):
        """Enum for export destinations"""
        CONTROLLER = "controller"
        NAS = "nas"

        @classmethod
        def from_string(cls, value: str) -> 'Export.ExportDestination':
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
        
    def _export_config_file(self, export_folder: str) -> bool:
        """Export the module's config file for traceability
        
        Args:
            export_folder: Destination export folder
            
        Returns:
            bool: True if config file was exported successfully
        """
        try:
            # Look for config files in common locations
            config_locations = [
                f"{self.module_id}_config.json",  # Module-specific config
                "config.json",  # Generic config
                "apa_arduino_config.json",  # APA Arduino config
                "apa_camera_config.json",   # APA Camera config
                os.path.join(os.path.dirname(self.recording_folder), "config.json"),  # Parent directory
                os.path.join(os.path.dirname(self.recording_folder), f"{self.module_id}_config.json")  # Parent with module ID
            ]
            
            config_source = None
            for config_path in config_locations:
                if os.path.exists(config_path):
                    config_source = config_path
                    break
            
            if not config_source:
                # Try to find config in the module's directory
                module_dir = os.path.dirname(os.path.abspath(__file__))
                for root, dirs, files in os.walk(module_dir):
                    for file in files:
                        if file.endswith('_config.json') and self.module_id.split('_')[0] in file:
                            config_source = os.path.join(root, file)
                            break
                    if config_source:
                        break
            
            if config_source and os.path.exists(config_source):
                # Copy config file to export folder with timestamp for this export session
                timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                timestamped_config = f"config_export_{timestamp}.json"
                dest_path = os.path.join(export_folder, timestamped_config)
                shutil.copy2(config_source, dest_path)
                
                self.logger.info(f"(EXPORT MANAGER) Exported config file: {timestamped_config}")
                return True
            else:
                self.logger.warning(f"(EXPORT MANAGER) No config file found for module {self.module_id}")
                return False
                
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Error exporting config file: {e}")
            return False

    def _create_export_manifest(self, files_to_export: list, destination: Union[str, 'Export.ExportDestination'], export_folder: str, experiment_name: str = None) -> str:
        """Create an export manifest file listing all files to be exported
        
        Args:
            files_to_export: List of filenames that will be exported
            destination: Where the files will be exported to (string or enum)
            export_folder: Path to the folder where files will be exported
            experiment_name: Optional experiment name for the export
            
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
                if experiment_name:
                    f.write(f"Experiment Name: {experiment_name}\n")
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

    def export_file(self, filename: str, destination: 'Export.ExportDestination', experiment_name: str = None) -> bool:
        """Export a single file to the specified destination
        
        Args:
            filename: Name of the file to export
            destination: Where to export to (NAS or Controller)
            experiment_name: Optional experiment name to include in export directory
            
        Returns:
            bool: True if export successful
        """
        self.logger.info(f"(EXPORT MANAGER) Attempting to export recordings for {experiment_name}")
        try:
            # Mount the destination if not already mounted
            if self.current_mount != destination:
                if not self._mount_destination(destination):
                    return False
                    
            # Create hierarchical export folder structure
            export_timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            if experiment_name:
                # Sanitize experiment name for filesystem safety
                safe_experiment_name = "".join(c for c in experiment_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                safe_experiment_name = safe_experiment_name.replace(' ', '_')
                
                # Create top-level experiment folder (without timestamp)
                experiment_folder = os.path.join(self.mount_point, safe_experiment_name)
                os.makedirs(experiment_folder, exist_ok=True)
                
                # Ensure the experiment folder has write permissions for all modules
                try:
                    os.chmod(experiment_folder, 0o777)  # rwxrwxrwx - full permissions
                    self.logger.info(f"(EXPORT MANAGER) Set permissions on experiment folder: {experiment_folder}")
                except Exception as e:
                    self.logger.warning(f"(EXPORT MANAGER) Could not set permissions on experiment folder: {e}")
                
                # Create module-specific subfolder with timestamp
                module_subfolder = f"{self.module_id}_{export_timestamp}"
                export_folder = os.path.join(experiment_folder, module_subfolder)
                
                self.logger.info(f"(EXPORT MANAGER) Created experiment folder: {experiment_folder}")
                self.logger.info(f"(EXPORT MANAGER) Created module subfolder: {module_subfolder}")
            else:
                # Fallback for experiments without names
                export_folder = os.path.join(self.mount_point, f"export_{export_timestamp}_{self.module_id}")
            
            os.makedirs(export_folder, exist_ok=True)
            
            # Copy the file
            source_path = os.path.join(self.recording_folder, filename)
            dest_path = os.path.join(export_folder, filename)
            shutil.copy2(source_path, dest_path)
            
            # Copy timestamps if they exist
            base_name = os.path.splitext(filename)[0]  # Remove extension
            timestamp_file = f"{base_name}_timestamps.txt"
            timestamp_source = os.path.join(self.recording_folder, timestamp_file)
            timestamp_dest = os.path.join(export_folder, timestamp_file)
            
            exported_files = [filename]
            if os.path.exists(timestamp_source):
                shutil.copy2(timestamp_source, timestamp_dest)
                exported_files.append(timestamp_file)
            
            # Export the module's config file for traceability
            config_exported = self._export_config_file(export_folder)
            if config_exported:
                exported_files.append("config_file")  # Add to manifest
                self.logger.info(f"(EXPORT MANAGER) Exported config file")
            else:
                self.logger.warning(f"(EXPORT MANAGER) Could not export config file")
            
            # Create export manifest
            manifest_filename = self._create_export_manifest(exported_files, destination, export_folder, experiment_name)
            if not manifest_filename:
                self.logger.error("(EXPORT MANAGER) Failed to create export manifest")
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Export failed: {e}")
            return False

    def export_all_files(self, destination: 'Export.ExportDestination', experiment_name: str = None) -> bool:
        """Export all files in the recording folder to the specified destination
        
        Args:
            destination: Where to export to (NAS or Controller)
            experiment_name: Optional experiment name to include in export directory
            
        Returns:
            bool: True if export successful
        """
        try:
            # Mount the destination if not already mounted
            if self.current_mount != destination:
                if not self._mount_destination(destination):
                    return False
                    
            # Create hierarchical export folder structure
            export_timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            if experiment_name:
                # Sanitize experiment name for filesystem safety
                safe_experiment_name = "".join(c for c in experiment_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                safe_experiment_name = safe_experiment_name.replace(' ', '_')
                
                # Create top-level experiment folder (without timestamp)
                experiment_folder = os.path.join(self.mount_point, safe_experiment_name)
                os.makedirs(experiment_folder, exist_ok=True)
                
                # Ensure the experiment folder has write permissions for all modules
                try:
                    os.chmod(experiment_folder, 0o777)  # rwxrwxrwx - full permissions
                    self.logger.info(f"(EXPORT MANAGER) Set permissions on experiment folder: {experiment_folder}")
                except Exception as e:
                    self.logger.warning(f"(EXPORT MANAGER) Could not set permissions on experiment folder: {e}")
                
                # Create module-specific subfolder with timestamp
                module_subfolder = f"{self.module_id}_{export_timestamp}"
                export_folder = os.path.join(experiment_folder, module_subfolder)
                
                self.logger.info(f"(EXPORT MANAGER) Created experiment folder: {experiment_folder}")
                self.logger.info(f"(EXPORT MANAGER) Created module subfolder: {module_subfolder}")
            else:
                # Fallback for experiments without names
                export_folder = os.path.join(self.mount_point, f"export_{export_timestamp}_{self.module_id}")
            
            os.makedirs(export_folder, exist_ok=True)
            
            # Export all files in the recording folder
            files_to_export = []
            for filename in os.listdir(self.recording_folder):
                # Skip directories
                file_path = os.path.join(self.recording_folder, filename)
                if os.path.isdir(file_path):
                    continue
                    
                # Add all files to export list
                files_to_export.append(filename)
                self.logger.info(f"(EXPORT MANAGER) Found file to export: {filename}")
            
            if not files_to_export:
                self.logger.warning(f"(EXPORT MANAGER) No files found in recording folder: {self.recording_folder}")
                return True  # Return True as this is not an error, just no files to export
            
            # Create manifest first
            manifest_filename = self._create_export_manifest(files_to_export, destination, export_folder, experiment_name)
            if not manifest_filename:
                self.logger.error("(EXPORT MANAGER) Failed to create export manifest")
                return False
            
            # Export the module's config file for traceability
            config_exported = self._export_config_file(export_folder)
            if config_exported:
                files_to_export.append("config_file")  # Add to manifest
                self.logger.info(f"(EXPORT MANAGER) Exported config file")
            else:
                self.logger.warning(f"(EXPORT MANAGER) Could not export config file")
            
            # Now export all files
            for filename in files_to_export:
                if filename == "config_file":  # Skip the config file entry we just added
                    continue
                source_path = os.path.join(self.recording_folder, filename)
                dest_path = os.path.join(export_folder, filename)
                try:
                    shutil.copy2(source_path, dest_path)
                    self.logger.info(f"(EXPORT MANAGER) Exported file: {filename}")
                except Exception as e:
                    self.logger.error(f"(EXPORT MANAGER) Failed to export file {filename}: {e}")
                    return False
            
            self.logger.info(f"(EXPORT MANAGER) Successfully exported {len(files_to_export)-1} files + config to {export_folder}")
            return True
            
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Export failed: {e}")
            return False
            
    def _mount_destination(self, destination: 'Export.ExportDestination') -> bool:
        """Mount the specified destination
        
        Args:
            destination: Where to mount (NAS or Controller)
            
        Returns:
            bool: True if mount successful
        """
        try:
            if destination == Export.ExportDestination.NAS:
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
            
            self.logger.info(f"(EXPORT MANAGER) Attempting to mount controller share: //{controller_ip}/{share_path}")
            self.logger.info(f"(EXPORT MANAGER) Using credentials: username={username}")
                
            # Unmount if already mounted
            if os.path.ismount(self.mount_point):
                self.logger.info(f"(EXPORT MANAGER) Unmounting existing mount at {self.mount_point}")
                subprocess.run(['sudo', 'umount', self.mount_point], check=True)
                
            # Try different SMB versions in order of preference
            smb_versions = ['3.0', '2.1', '1.0']
            
            for version in smb_versions:
                try:
                    self.logger.info(f"(EXPORT MANAGER) Trying SMB version {version}")
                    
                    # Mount the Samba share with credentials
                    mount_cmd = [
                        'sudo', 'mount', '-t', 'cifs',
                        f'//{controller_ip}/{share_path}',
                        self.mount_point,
                        '-o', f'username={username},password={password},vers={version}'
                    ]
                    
                    # Run mount command and capture output
                    result = subprocess.run(mount_cmd, capture_output=True, text=True)
                    
                    if result.returncode == 0:
                        self.logger.info(f"(EXPORT MANAGER) Successfully mounted controller share at {self.mount_point} using SMB {version}")
                        self.current_mount = Export.ExportDestination.CONTROLLER
                        return True
                    else:
                        self.logger.warning(f"(EXPORT MANAGER) Failed to mount with SMB {version}: {result.stderr}")
                        
                except subprocess.CalledProcessError as e:
                    self.logger.warning(f"(EXPORT MANAGER) Mount command failed with SMB {version}: {e}")
                    continue
            
            # If we get here, all SMB versions failed
            self.logger.error(f"(EXPORT MANAGER) All SMB versions failed. Last error: {result.stderr if 'result' in locals() else 'Unknown error'}")
            self.logger.error(f"(EXPORT MANAGER) Please check:")
            self.logger.error(f"(EXPORT MANAGER) 1. Samba service is running on controller: sudo systemctl status smbd")
            self.logger.error(f"(EXPORT MANAGER) 2. Share '{share_path}' exists on controller")
            self.logger.error(f"(EXPORT MANAGER) 3. Credentials are correct (username: {username})")
            self.logger.error(f"(EXPORT MANAGER) 4. Network connectivity to {controller_ip}")
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
            self.current_mount = Export.ExportDestination.NAS
            return True
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"(EXPORT MANAGER) Failed to mount NAS share: {e}")
            return False
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) NAS mount failed: {e}")
            return False
            
    def export_current_session_files(self, recording_folder: str, recording_session_id: str, experiment_name: str = None) -> bool:
        """Export only the files from the current recording session
        
        Args:
            recording_folder: Path to the recording folder
            recording_session_id: Session ID to filter files by
            experiment_name: Optional experiment name for folder structure
            
        Returns:
            bool: True if export successful
        """
        try:
            # Mount the export destination
            if not self._mount_destination(self.ExportDestination.CONTROLLER):
                self.logger.error("(EXPORT MANAGER) Failed to mount export destination")
                return False
            
            # Create hierarchical export folder structure with conflict prevention
            export_timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            if experiment_name:
                safe_experiment_name = "".join(c for c in experiment_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                safe_experiment_name = safe_experiment_name.replace(' ', '_')
                
                # Create top-level experiment folder (without timestamp)
                experiment_folder = os.path.join(self.mount_point, safe_experiment_name)
                os.makedirs(experiment_folder, exist_ok=True)
                
                # Ensure the experiment folder has write permissions for all modules
                try:
                    os.chmod(experiment_folder, 0o777)  # rwxrwxrwx - full permissions
                    self.logger.info(f"(EXPORT MANAGER) Set permissions on experiment folder: {experiment_folder}")
                except Exception as e:
                    self.logger.warning(f"(EXPORT MANAGER) Could not set permissions on experiment folder: {e}")
                
                # Create module-specific subfolder with timestamp
                module_subfolder = f"{self.module_id}_{export_timestamp}"
                export_folder = os.path.join(experiment_folder, module_subfolder)
                
                self.logger.info(f"(EXPORT MANAGER) Created experiment folder: {experiment_folder}")
                self.logger.info(f"(EXPORT MANAGER) Created module subfolder: {module_subfolder}")
            else:
                # Fallback for experiments without names
                export_folder = os.path.join(self.mount_point, f"export_{export_timestamp}_{self.module_id}")
            
            os.makedirs(export_folder, exist_ok=True)
            self.logger.info(f"(EXPORT MANAGER) Created export folder: {export_folder}")
            
            # Find files that belong to the current session
            session_files = []
            for filename in os.listdir(recording_folder):
                if recording_session_id in filename:
                    session_files.append(filename)
                    self.logger.info(f"(EXPORT MANAGER) Found session file to export: {filename}")
            
            if not session_files:
                self.logger.warning("(EXPORT MANAGER) No session files found to export")
                return True
            
            # Export each session file
            exported_count = 0
            for filename in session_files:
                try:
                    source_path = os.path.join(recording_folder, filename)
                    dest_path = os.path.join(export_folder, filename)
                    shutil.copy2(source_path, dest_path)
                    self.logger.info(f"(EXPORT MANAGER) Exported: {filename}")
                    exported_count += 1
                except Exception as e:
                    self.logger.error(f"(EXPORT MANAGER) Failed to export {filename}: {e}")
                    return False
            
            # Export the module's config file for traceability
            config_exported = self._export_config_file(export_folder)
            if config_exported:
                exported_count += 1
                session_files.append("config_file")  # Add to manifest
                self.logger.info(f"(EXPORT MANAGER) Exported config file")
            else:
                self.logger.warning(f"(EXPORT MANAGER) Could not export config file")
            
            # Create export manifest
            manifest_filename = self._create_export_manifest(session_files, self.ExportDestination.CONTROLLER, export_folder, experiment_name)
            if not manifest_filename:
                self.logger.error("(EXPORT MANAGER) Failed to create export manifest")
                return False
            
            self.logger.info(f"(EXPORT MANAGER) Successfully exported {exported_count} session files to {export_folder}")
            self.logger.info(f"(EXPORT MANAGER) Created export manifest: {manifest_filename}")
            return True
            
        except Exception as e:
            self.logger.error(f"(EXPORT MANAGER) Export error: {e}")
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