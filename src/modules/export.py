#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Export Manager

This class is used to send files to either a controller or NAS running a samba server.

Files are typically recorded to /var/lib/saviour/recordings/pending/
Upon completion they are moved to /var/lib/saviour/recordings/to_export/
Once exported they are moved to /var/lib/saviour/recordings/exported/
Old, exported files may be deleted from there when required.

Author: Andrew SG
Created: 12/06/2025
"""

import os
import shutil
import pathlib
import logging
import subprocess
import datetime
import time


from src.modules.config import Config


class Export:
    """Manages Samba based file exports"""
    def __init__(self, module_id: str, config: Config):
        self.module_id = module_id
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Local SD card context
        self.to_export_folder = f'{self.config.get("recording.recording_folder")}/to_export'
        self.exported_folder = f'{self.config.get("recording.recording_folder")}/exported'
        os.makedirs(self.to_export_folder, exist_ok=True)
        os.makedirs(self.exported_folder, exist_ok=True)
        self.mount_point = "/mnt/export"  # This is where the samba share gets mounted
        
        # Samba details (configurable)
        self.samba_share_ip = None 
        self.samba_share_path = None # The name of the top level folder on the samba share
        self.samba_share_username = None 
        self.samba_share_password = None
        self._update_samba_settings()

        self.exporting = False # Flag to indicate whether export in progress

        # Staged files for export
        self.session_files = [] # Record of all recorded files in the session
        self.session_name = None
        self.staged_for_export = []
        self.recording_name = None # Should be set by recording manager - expect something akin to habitat6_cohort3/140126/camera_dc71/
        self.export_path = None # Set here - the full export path e.g. /mnt/export/habitat6_cohort3/140126/camera_dc71/

        # Create mount point directory if it doesn't exist
        try:
            os.makedirs(self.mount_point, exist_ok=True)
            self.logger.info(f"Created mount point directory: {self.mount_point}")
        except Exception as e:
            self.logger.error(f"Failed to create mount point directory: {e}")


    def _setup_export(self, export_path: str) -> bool:
        """Set up an export
        - Mount samba share
        - Create the export folder on the mounted share
        - Ensure it has write permissions
        """
        # Mount the share
        if not self._mount_share():
            return False

        # Create export folder on mounted share 
        export_path = self._format_export_path(export_path)
        self._create_export_path(export_path)

        self.logger.info(f"Attempting to set permissions on {export_path}")

        # Set write permissions on export path
        try:
            os.chmod(export_path, 0o777)  # rwxrwxrwx - full permissions
            self.logger.info(f"Set permissions on experiment folder: {export_path}")
            return export_path
        except Exception as e:
            self.logger.warning(f"Could not set permissions on experiment folder: {e}")
            return False


    def export_staged(self, export_path: str = None) -> bool:
        """Export all files in self.staged_for_export

        Returns:
            bool: True if export successful
        """
        self.staged_for_export = os.listdir(self.to_export_folder)
        self.logger.info(f"Attempting to export {self.staged_for_export}")
        self.exporting = True
        try:

            export_path = self._setup_export(export_path)
                
            if not export_path:
                return False
            
            # Create hierarchical export folder structure with conflict prevention
            export_timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

            to_export = self.staged_for_export
            source_folder = self.to_export_folder
            self.logger.info(f"Will attempt to export {to_export}")
            
            # Export each session file
            exported_count = 0
            exported = []
            for filename in to_export:
                try:
                    filename = pathlib.Path(filename).name
                    self.logger.info(f"Exporting {filename}")

                    # Rename file "PENDING"
                    temp_filename = f"PENDING_{filename}"

                    source_path = f"{source_folder}/{filename}"
                    temp_source_path = f"{source_folder}/{temp_filename}"
                    temp_dest_path = f"{export_path}/{temp_filename}"
                    dest_path = f"{export_path}/{filename}"
                
                    os.rename(source_path, temp_source_path)

                    # Copy the file to samba share
                    shutil.copy2(temp_source_path, temp_dest_path)

                    # Remove "PENDING" from filename on local and remote copy
                    os.rename(temp_dest_path, dest_path)
                    os.rename(temp_source_path, source_path)

                    # Move local copy of file from to_export/ to exported/
                    shutil.move(source_path, f"{self.exported_folder}/{filename}") # Move it from to_export to exported

                    self.logger.info(f"Exported: {dest_path}")
                    exported_count += 1
                    exported.append(filename)

                except Exception as e:
                    self.logger.error(f"Failed to export {filename}: {e}")
        
            
            # Create export manifest
            if self.config.get("export.manifest_enabled", False):
                if not self.session_name:
                    session_name="NO_SESSION"
                manifest_filename = self._create_export_manifest(self.staged_for_export, export_path, session_name)
                if not manifest_filename:
                    self.logger.error("Failed to create export manifest")
                    return False
        
            # Delete exported files
            if self.config.get("export.delete_on_export", True):
                self._delete_local_files(exported)

            self.to_export = []

            self.logger.info(f"Successfully exported {exported_count} session files to {export_path}")
            self.exporting = False
            return True
            
        except Exception as e:
            self.logger.error(f"Export error: {e}")
            self.exporting = False
            return False


    def _delete_local_files(self, files: list) -> None:
        deleted_count = 0
        if len(files) == 0:
            self.logger.error("No files provided to delete")
            return
        for filename in files:
            try:
                if os.path.exists(f"{self.exported_folder}/{filename}"):
                    os.remove(f"{self.exported_folder}/{filename}")
                    deleted_count += 1
            except Exception as e:
                self.logger.error(f"Error exporting {filename}: {e}")
        self.logger.info(f"Deleted {deleted_count} files")


    def _create_export_path(self, export_path: str) -> bool:
        """Create the path for files to be exported to, which is the share mount point and the current export folder filename."""
        try:
            os.makedirs(export_path, exist_ok=True)
            return True
        except Exception as e:
            self.logger.error(f"Error creating export path: {e}")
            return False

    
    def stage_file_for_export(self, filename: str) -> bool:
        """Take a filename and stage it for export"""
        current_path = os.path.abspath(filename)
        path = pathlib.Path(current_path)
        filename = path.name
        destination_path = f"{self.to_export_folder}/{filename}"
        try:
            self.logger.info(f"Moving {filename}, from {current_path} to {destination_path}")
            shutil.move(current_path, destination_path)
            return True
        except Exception as e:
            self.logger.error(f"Error moving {filename} to {destination_path}: {e}")


    def add_session_file(self, filename: str) -> bool:
        """Take a filename (abspath) and stage for export."""
        self.logger.info(f"Adding {filename} to session files")
        if not os.path.isfile(filename): # Check file exists
            return False
        abspath = os.path.abspath(filename) # Make path absolute
        self.session_files.append(abspath) # Add to staged files
        self.logger.info(f"Session files: {self.session_files}")
        return True


    def set_session_name(self, session_name: str) -> bool:
        """Take a foldername e.g. habitat6_cohortA2/140126/, set it and create it."""
        safe_folder_name = "".join(c for c in session_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_folder_name = safe_folder_name.replace(' ', '_')
        self.session_name = safe_folder_name


    def clear_session_files(self) -> None:
        self.session_files = []
    

    def clear_staged_for_export(self) -> None:
        self.staged_for_export = []

    
    def when_recording_starts(self) -> bool:
        """To be called by recording object when a new recording session starts.

        The config file will be exported (as it is expected not to change during recording) and session/staged for export files will be cleared.
        """
        self.logger.info("export object received notification that new recording has started.")
        self.clear_session_files()
        self.clear_staged_for_export()
        self._export_config_file()


    def _ensure_export_folder_exists(self):
        if not self._create_export_path(): # Failed to create export path; maybe an issue mounting share?
            return False
        return True


    def _format_export_path(self, export_path: str):
        """Create nested"""
        # export_path = f"{export_path}/{self.facade.get_utc_date(time.time())}/{self.facade.get_module_name()}"
        export_path = os.path.join(self.mount_point, export_path)
        return export_path


    def _export_config_file(self) -> bool:
        """Export the module's config file for traceability
        
        Args:
            export_folder: Destination export folder
            
        Returns:
            bool: True if config file was exported successfully
        """
        try:
            export_path = self.facade.get_current_session_name()
            if not self._setup_export(export_path):
                return False

            # Look for config files in common locations
            config_locations = [
                f"{self.module_id}_config.json",  # Module-specific config
                "config.json",  # Generic config
                "apa_arduino_config.json",  # APA Arduino config
                "apa_camera_config.json",   # APA Camera config
                os.path.join(os.path.dirname(self.facade.get_recording_folder()), "config.json"),  # Parent directory
                os.path.join(os.path.dirname(self.facade.get_recording_folder()), f"{self.module_id}_config.json")  # Parent with module ID
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
                if "config.json" not in os.listdir(export_path):
                    # timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                    # timestamped_config = f"config_export_{timestamp}.json"
                    dest_path = os.path.join(export_path, "config.json")
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


    def _create_export_manifest(self, files_to_export: list, export_folder: str, session_name: str = None) -> str:
        """Create an export manifest file listing all files to be exported
        
        Args:
            files_to_export: List of filenames that will be exported
            destination: Where the files will be exported to (string or enum)
            export_folder: Path to the folder where files will be exported
            session_name: Optional session_name for the export
            
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
                if session_name:
                    f.write(f"session_name: {session_name}\n")
                f.write(f"Files to be exported:\n")
                for file in files_to_export:
                    f.write(f"- {file}\n")
                    # Add file size and modification time from source
                    file_path = os.path.join(self.facade.get_recording_folder(), file)
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


    """Samba Methods"""
    def _update_samba_settings(self):
        """Check for updated samba settings from config"""
        old_samba_ip = self.samba_share_ip

        self.samba_share_ip = self.config.get("export._share_ip", "10.0.0.1") 
        self.samba_share_path = self.config.get("export._share_path", "controller_share")
        self.samba_share_username = self.config.get("export._share_username", "pi") 
        self.samba_share_password = self.config.get("export._share_password", "saviour")

        # If IP has changed, reconfigure traffic control rules
        if self.samba_share_ip != old_samba_ip: 
            self._clear_traffic_control_filter()
            self._apply_traffic_control_filter()


    def _samba_settings_changed(self):
        self.samba_share_ip = self.config.get("export._share_ip", "10.0.0.1") 
        self.samba_share_path = self.config.get("export._share_path", "controller_share")
        self.samba_share_username = self.config.get("export._share_username", "pi") 
        self.samba_share_password = self.config.get("export._share_password", "saviour")
        self._clear_traffic_control_filter()
        self._apply_traffic_control_filter()
        

    def _clear_traffic_control_filter(self):
        self.logger.info("Clearing traffic control filters")
        try:
            # Check if the qdisc exists
            check_cmd = ["sudo", "tc", "qdisc", "show", "dev", "eth0"]
            check_result = subprocess.run(check_cmd, check=True, text=True, capture_output=True)

            if "htb" in check_result.stdout:
                # Proceed to delete the qdisc if it exists
                cmd = [
                    "sudo", "tc", "qdisc", "del", 
                    "dev", "eth0", 
                    "root"
                ]
                self._run_shell_command(cmd)
                self.logger.info("Traffic control filters cleared successfully")
            else:
                self.logger.info("No traffic control filters found; nothing to clear")

        except Exception as e:
            self.logger.error(f"Unexpected error: {e}")


    def _run_shell_command(self, cmd: list):
        try:
            result = subprocess.run(cmd, check=True, text=True, capture_output=True)
            self.logger.info(f"Command succeeded: {result.stdout}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Command failed: {e.stderr}")


    def _apply_traffic_control_filter(self):
        self.logger.info(f"Applying new traffic control filter for {self.samba_share_ip} on samba port 445")
        
        add_qdisc_cmd = [
            "sudo", "tc", "qdisc", "add", 
            "dev", "eth0", 
            "root", # Create the root level queueing discipling
            "handle", "1:0",  # Create new queueing discipline with handle 1:0
            "htb", # Hierarchical token bucket
            "default", "10" # Default class for packets that don't match any criteria
        ] 

        # result = subprocess.run(add_qdisc_cmd, shell=True, check=True, text=True, capture_output=True)
        self._run_shell_command(add_qdisc_cmd)

        max_bitrate_mb = self.config.get("export.max_bitrate_mb", 10)
        max_burst_kb = self.config.get("export.max_burst_kb", 30)
        add_class_cmd = [
            "sudo", "tc", "class", "add", 
            "dev", "eth0", 
            "parent", "1:0",  # Apply to parent queueing discipline with handle 1:0 (root)
            "classid", "1:1",  # Create new class with handle 1:1
            "htb", # Hierarchical token bucket
            "rate", f"{max_bitrate_mb}mbit", 
            "burst", f"{max_burst_kb}k"
        ]
        # result = subprocess.run(add_class_cmd, shell=True, check=True, text=True, capture_output=True)
        self._run_shell_command(add_class_cmd)

        add_filter_cmd = [
            "sudo", "tc", "filter", "add",
            "dev", "eth0",
            "protocol", "ip",
            "parent", "1:0", # Apply to parent queueing discipline with handle 1:0 (root)
            "u32", # Use u32 packet classifier
            "match", "ip", "dst", self.samba_share_ip,
            "match", "ip", "dport", "445",
            "0xffff", # Bitmask for header - match exactly on port 445
            "flowid", "1:1" # Direct matching traffic to the class with identifier 1:1 (the one that rate limits traffic)
        ]
        # result = subprocess.run(add_filter_cmd, shell=True, check=True, text=True, capture_output=True)
        self._run_shell_command(add_filter_cmd)

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
                    '-o', f'username={self.samba_share_username},password={self.samba_share_password},uid=pi,gid=pi,file_mode=0664,dir_mode=0775' # Allow for writing files and creating directories
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
