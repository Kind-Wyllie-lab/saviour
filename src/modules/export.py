#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Export Manager

This class is used to send files to either a controller or NAS running a samba server.

Author: Andrew SG
Created: 12/06/2025
"""

import os
import shutil
from enum import Enum
import logging
import subprocess
import datetime

class Export:
    """Manages Samba based file exports"""
    def __init__(self, module_id: str, recording_folder: str, config: dict):
        self.module_id = module_id
        self.recording_folder = recording_folder
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.current_mount = None
        self.mount_point = "/mnt/export"  # This is where the samba share gets mounted
        self.callbacks = {}
        
        # Samba details (configurable)
        self.samba_share_ip = None # 192.168.1.1 for controller
        self.samba_share_path = None # The name of the top level folder on the samba share
        self.samba_share_username = None 
        self.samba_share_password = None

        self._update_samba_settings()

        # Create mount point directory if it doesn't exist
        try:
            os.makedirs(self.mount_point, exist_ok=True)
            self.logger.info(f"Created mount point directory: {self.mount_point}")
        except Exception as e:
            self.logger.error(f"Failed to create mount point directory: {e}")
        
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
                if "config.json" not in os.listdir(export_folder):
                    # timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                    # timestamped_config = f"config_export_{timestamp}.json"
                    dest_path = os.path.join(export_folder, "config.json")
                    shutil.copy2(config_source, dest_path)
                    # self.logger.info(f"Exported config file: {timestamped_config}")
                else:
                    self.logger.info(f"Config already exported for this session; skipping")
                return True
            else:
                self.logger.warning(f"No config file found for module {self.module_id}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error exporting config file: {e}")
            return False

    def _create_export_manifest(self, files_to_export: list, export_folder: str, experiment_name: str = None) -> str:
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
            
            with open(manifest_path, 'w') as f:
                f.write(f"Export Manifest - {manifest_timestamp}\n")
                f.write(f"Module ID: {self.module_id}\n")
                f.write(f"Destination: //{self.samba_share_ip}/{self.samba_share_path}\n")
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
            
            self.logger.info(f"Created export manifest: {manifest_filename}")
            return manifest_filename
            
        except Exception as e:
            self.logger.error(f"Failed to create export manifest: {e}")
            return None

    def export_file(self, filename: str, experiment_name: str = None) -> bool:
        """Export a single file to the specified destination
        
        Args:
            filename: Name of the file to export
            destination: Where to export to (NAS or Controller)
            experiment_name: Optional experiment name to include in export directory
            
        Returns:
            bool: True if export successful
        """
        self.logger.info(f"Attempting to export recordings for {experiment_name}")
        try:
            # Mount the destination if not already mounted
            if not self._mount_share():
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
                    self.logger.info(f"Set permissions on experiment folder: {experiment_folder}")
                except Exception as e:
                    self.logger.warning(f"Could not set permissions on experiment folder: {e}")
                
                # Create module-specific subfolder with timestamp
                module_subfolder = f"{self.module_id}_{export_timestamp}"
                export_folder = os.path.join(experiment_folder, module_subfolder)
                
                self.logger.info(f"Created experiment folder: {experiment_folder}")
                self.logger.info(f"Created module subfolder: {module_subfolder}")
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
                self.logger.info(f"Exported config file")
            else:
                self.logger.warning(f"Could not export config file")
            
            # Create export manifest
            if self.config.get("export.manifest_enabled", False):
                manifest_filename = self._create_export_manifest(exported_files, export_folder, experiment_name)
                if not manifest_filename:
                    self.logger.error("Failed to create export manifest")
                    return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"Export failed: {e}")
            return False

    def export_all_files(self, experiment_name: str = None) -> bool:
        """Export all files in the recording folder to the specified destination
        
        Args:
            destination: Where to export to (NAS or Controller)
            experiment_name: Optional experiment name to include in export directory
            
        Returns:
            bool: True if export successful
        """
        try:
            # Mount the destination if not already mounted
            if not self._mount_share():
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
                    self.logger.info(f"Set permissions on experiment folder: {experiment_folder}")
                except Exception as e:
                    self.logger.warning(f"Could not set permissions on experiment folder: {e}")
                
                # Create module-specific subfolder with timestamp
                module_subfolder = f"{self.module_id}_{export_timestamp}"
                export_folder = os.path.join(experiment_folder, module_subfolder)
                
                self.logger.info(f"Created experiment folder: {experiment_folder}")
                self.logger.info(f"Created module subfolder: {module_subfolder}")
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
                self.logger.info(f"Found file to export: {filename}")
            
            if not files_to_export:
                self.logger.warning(f"No files found in recording folder: {self.recording_folder}")
                return True  # Return True as this is not an error, just no files to export
            
            # Create manifest first
            if self.config.get("export.manifest_enabled", False):
                manifest_filename = self._create_export_manifest(files_to_export, export_folder, experiment_name)
                if not manifest_filename:
                    self.logger.error("Failed to create export manifest")
                    return False
            
            # Export the module's config file for traceability
            config_exported = self._export_config_file(export_folder)
            if config_exported:
                files_to_export.append("config_file")  # Add to manifest
                self.logger.info(f"Exported config file")
            else:
                self.logger.warning(f"Could not export config file")
            
            # Now export all files
            for filename in files_to_export:
                if filename == "config_file":  # Skip the config file entry we just added
                    continue
                source_path = os.path.join(self.recording_folder, filename)
                dest_path = os.path.join(export_folder, filename)
                try:
                    shutil.copy2(source_path, dest_path)
                    self.logger.info(f"Exported file: {filename}")
                except Exception as e:
                    self.logger.error(f"Failed to export file {filename}: {e}")
                    return False
            
            self.logger.info(f"Successfully exported {len(files_to_export)-1} files + config to {export_folder}")
            return True
            
        except Exception as e:
            self.logger.error(f"Export failed: {e}")
            return False


    def _update_samba_settings(self):
        """Check for updated samba settings from config"""
        self.samba_share_ip = self.config.get("export._share_ip", "192.168.1.1") # These are not pulling from config for now and I don't know why.
        self.samba_share_path = self.config.get("export._share_path", "controller_share")
        self.samba_share_username = self.config.get("export._share_username", "pi") # TODO: Make this more secure?
        self.samba_share_password = self.config.get("export._share_password", "saviour")


    def _mount_share(self) -> bool:
        """Mount Samba share using preconfigured options"""
        try:
            self._update_samba_settings()
            self.logger.info(f"Attempting to mount share: //{self.samba_share_ip}/{self.samba_share_path} as user {self.samba_share_username}")

            # Unmount if already mounted
            if os.path.ismount(self.mount_point):
                self.logger.info(f"Unmounting existing mount at {self.mount_point}")
                subprocess.run(['sudo', 'umount', self.mount_point], check=True)

            # Attempt to mount
            try: 
                mount_cmd = [
                    'sudo', 'mount', '-t', 'cifs',
                    f'//{self.samba_share_ip}/{self.samba_share_path}',
                    self.mount_point,
                    '-o', f'username={self.samba_share_username},password={self.samba_share_password}'
                ]

                result = subprocess.run(mount_cmd, capture_output=True, text=True)


                if result.returncode == 0:
                    self.logger.info(f"Successfully mounted controller share at {self.mount_point}")
                    return True
                else:
                    self.logger.warning(f"Failed to mount with SMB: {result.stderr}")
                    
            except subprocess.CalledProcessError as e:
                self.logger.warning(f"Mount command failed with SMB: {e}")
                return False
        
        except Exception as e:
            self.logger.warning(f"Error mounting share: {e}")
            return False

            
    def export_current_session_files(self, session_files: list, recording_folder: str, recording_session_id: str, experiment_name: str = None) -> bool:
        """Export only the files from the current recording session
        
        Args:
            recording_folder: Path to the recording folder
            recording_session_id: Session ID to filter files by
            experiment_name: Optional experiment name for folder structure
            
        Returns:
            bool: True if export successful
        """
        self.logger.info(f"Attempting to export files for session {recording_session_id}, experiment name {experiment_name}")
        try:
            # Mount the export destination
            if not self._mount_share():
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
                    self.logger.info(f"Set permissions on experiment folder: {experiment_folder}")
                except Exception as e:
                    self.logger.warning(f"Could not set permissions on experiment folder: {e}")
                
                # Create module-specific subfolder with timestamp
                module_subfolder = f"{self.module_id}"
                export_folder = os.path.join(experiment_folder, module_subfolder)
                
                self.logger.info(f"Created experiment folder: {experiment_folder}")
                self.logger.info(f"Created module subfolder: {module_subfolder}")
            else:
                # Fallback for experiments without names
                export_folder = os.path.join(self.mount_point, f"export_{export_timestamp}_{self.module_id}")
            
            os.makedirs(export_folder, exist_ok=True)
            self.logger.info(f"Created export folder: {export_folder}")
            
            session_files = session_files
            self.logger.info(f"Will attempt to export {session_files}")
            
            # Export each session file
            exported_count = 0
            for filename in session_files:
                try:
                    self.logger.info(f"Exporting {filename}")
                    # Determine absolute source path
                    if os.path.isabs(filename):
                        source_path = filename
                    else:
                        # If the file is relative to recording_folder, resolve properly
                        # Prevent double-prefix like 'rec/rec/...'
                        rel_path = os.path.relpath(filename, start=recording_folder)
                        source_path = os.path.join(recording_folder, rel_path)

                    # Destination: flat structure, just filename
                    dest_filename = os.path.basename(filename)
                    dest_path = os.path.join(export_folder, dest_filename)

                    shutil.copy2(source_path, dest_path)
                    self.logger.info(f"Exported: {dest_filename}")
                    exported_count += 1

                except Exception as e:
                    self.logger.error(f"Failed to export {filename}: {e}")
            
            # Export the module's config file for traceability
            config_exported = self._export_config_file(export_folder)
            if config_exported:
                exported_count += 1
                session_files.append("config_file")  # Add to manifest
                self.logger.info(f"Exported config file")
            else:
                self.logger.warning(f"Could not export config file")
            
            # Create export manifest
            if self.config.get("export.manifest_enabled", False):
                manifest_filename = self._create_export_manifest(session_files, export_folder, experiment_name)
                if not manifest_filename:
                    self.logger.error("Failed to create export manifest")
                    return False
        
            # This is a bit of a hack TECHNICAL DEBT - session_files is later used in clear_recordings so we should remove the "config_file" ref which is only used for export manifest
            # TODO: Copy config.json into rec folder (freeze its state at start of recording), add it to session files, export with other session files, clear with other session files i.e. stop treating it specially 
            session_files.remove("config_file")

            self.logger.info(f"Successfully exported {exported_count} session files to {export_folder}")
            return True
            
        except Exception as e:
            self.logger.error(f"Export error: {e}")
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
            self.logger.error(f"Unmount failed: {e}")
            return False