#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Habitat System - Camera Module Class

This class extends the base Module class to handle camera-specific functionality.

Author: Andrew SG
Created: 17/03/2025
License: GPLv3
"""

import datetime
import subprocess
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import time
from src.modules.module import Module
from src.modules.module_command_handler import ModuleCommandHandler
import logging
import numpy as np
import base64
import signal
import shutil
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
import threading

class CameraCommandHandler(ModuleCommandHandler):
    """Command handler specific to camera functionality"""
    
    def handle_command(self, command: str, **kwargs):
        """Handle camera-specific commands while preserving base functionality"""
        
        # Handle camera-specific commands
        match command.split()[0]:  # Split and take first word to match command
            case "start_recording":
                if 'start_recording' in self.callbacks:
                    result = self.callbacks['start_recording']()
                    self.communication_manager.send_status({"recording_started": result})
                else:
                    self.logger.error("(COMMAND HANDLER) No start_recording callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for recording"})
                return

            case "stop_recording":
                if 'stop_recording' in self.callbacks:
                    result = self.callbacks['stop_recording']()
                    self.communication_manager.send_status({"recording_stopped": result})
                else:
                    self.logger.error("(COMMAND HANDLER) No stop_recording callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for recording"})
                return

            case "record_video":
                if 'record_video' in self.callbacks:
                    try:
                        # Extract duration from command string
                        duration_str = command.split(' ', 1)[1]  # Get everything after the command name
                        duration = int(duration_str)
                        filename = self.callbacks['record_video'](duration)
                        if filename:
                            self.communication_manager.send_status({
                                "type": "video_recording_complete",
                                "filename": filename,
                                "length": duration
                            })
                        else:
                            self.communication_manager.send_status({
                                "type": "video_recording_failed",
                                "error": "Recording failed"
                            })
                    except (ValueError, IndexError) as e:
                        self.logger.error(f"(COMMAND HANDLER) Error parsing record duration: {e}")
                        self.communication_manager.send_status({
                            "type": "video_recording_failed",
                            "error": "Invalid duration format"
                        })
                else:
                    self.logger.error("(COMMAND HANDLER) No record_video callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for video recording"})
                return

            case "export_video":
                if 'export_video' in self.callbacks:
                    try:
                        # Extract parameters from command string
                        params_str = command.split(' ', 1)[1] if len(command.split()) > 1 else "{}"
                        
                        # If it's just a filename without JSON format, wrap it in a dict
                        if not params_str.startswith('{'):
                            params_str = f'{{"filename": "{params_str}"}}'
                            
                        params = eval(params_str)  # Convert string representation back to dict
                        
                        filename = params.get('filename')
                        if not filename:
                            self.communication_manager.send_status({
                                "type": "video_export_failed",
                                "error": "No filename provided for export"
                            })
                            return
                            
                        # Optional parameters
                        length = params.get('length', 0)
                        destination = params.get('destination', 'controller')
                        
                        success = self.callbacks['export_video'](filename, length, destination)
                        if success:
                            self.communication_manager.send_status({
                                "type": "video_export_complete",
                                "filename": filename,
                                "length": length,
                                "destination": destination
                            })
                        else:
                            self.communication_manager.send_status({
                                "type": "video_export_failed",
                                "error": "Failed to export video"
                            })
                    except Exception as e:
                        self.logger.error(f"(COMMAND HANDLER) Error parsing export parameters: {e}")
                        self.communication_manager.send_status({
                            "type": "video_export_failed",
                            "error": str(e)
                        })
                else:
                    self.logger.error("(COMMAND HANDLER) No export_video callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for video export"})
                return

            case "update_camera_settings":
                try:
                    # Extract parameters from command string
                    params_str = command.split(' ', 1)[1]  # Get everything after the command name
                    params = eval(params_str)  # Convert string representation back to dict
                    if 'handle_update_camera_settings' in self.callbacks:
                        success = self.callbacks['handle_update_camera_settings'](params)
                        if success:
                            self.communication_manager.send_status({
                                "type": "camera_settings_updated",
                                "settings": params,
                                "success": True
                            })
                        else:
                            self.communication_manager.send_status({
                                "type": "camera_settings_update_failed",
                                "error": "Failed to update settings"
                            })
                    else:
                        self.logger.error("(COMMAND HANDLER) No handle_update_camera_settings callback provided")
                        self.communication_manager.send_status({
                            "type": "camera_settings_update_failed",
                            "error": "Module not configured for camera settings"
                        })
                except Exception as e:
                    self.logger.error(f"(COMMAND HANDLER) Error parsing camera settings: {e}")
                    self.communication_manager.send_status({
                        "type": "camera_settings_update_failed",
                        "error": str(e)
                    })
                return

            case "list_recordings":
                if 'list_recordings' in self.callbacks:
                    try:
                        recordings = self.callbacks['list_recordings']()
                        self.communication_manager.send_status({
                            "type": "recordings_list",
                            "recordings": recordings
                        })
                    except Exception as e:
                        self.logger.error(f"(COMMAND HANDLER) Error listing recordings: {e}")
                        self.communication_manager.send_status({
                            "type": "recordings_list_failed",
                            "error": str(e)
                        })
                else:
                    self.logger.error("(COMMAND HANDLER) No list_recordings callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for listing recordings"})
                return

            case "clear_recordings":
                if 'clear_recordings' in self.callbacks:
                    try:
                        # Extract parameters from command string
                        params_str = command.split(' ', 1)[1] if len(command.split()) > 1 else "{}"
                        params = eval(params_str)  # Convert string representation back to dict
                        
                        # Get optional parameters
                        older_than = params.get('older_than')  # Optional timestamp
                        keep_latest = params.get('keep_latest', 0)  # Optional number of latest to keep
                        
                        result = self.callbacks['clear_recordings'](older_than, keep_latest)
                        self.communication_manager.send_status({
                            "type": "recordings_cleared",
                            "deleted_count": result.get('deleted_count', 0),
                            "kept_count": result.get('kept_count', 0)
                        })
                    except Exception as e:
                        self.logger.error(f"(COMMAND HANDLER) Error clearing recordings: {e}")
                        self.communication_manager.send_status({
                            "type": "recordings_clear_failed",
                            "error": str(e)
                        })
                else:
                    self.logger.error("(COMMAND HANDLER) No clear_recordings callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for clearing recordings"})
                return

            case "export_to_nas":
                if 'export_to_nas' in self.callbacks:
                    try:
                        # Extract filename from command string if provided
                        filename = command.split(' ', 1)[1] if len(command.split()) > 1 else None
                        
                        success = self.callbacks['export_to_nas'](filename)
                        if success:
                            self.communication_manager.send_status({
                                "type": "nas_export_complete",
                                "filename": filename if filename else "all"
                            })
                        else:
                            self.communication_manager.send_status({
                                "type": "nas_export_failed",
                                "error": "Failed to export to NAS"
                            })
                    except Exception as e:
                        self.logger.error(f"(COMMAND HANDLER) Error exporting to NAS: {e}")
                        self.communication_manager.send_status({
                            "type": "nas_export_failed",
                            "error": str(e)
                        })
                else:
                    self.logger.error("(COMMAND HANDLER) No export_to_nas callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for NAS export"})
                return

            case "mount_nas":
                if 'mount_nas' in self.callbacks:
                    try:
                        success = self.callbacks['mount_nas']()
                        if success:
                            self.communication_manager.send_status({
                                "type": "nas_mounted",
                                "success": True
                            })
                        else:
                            self.communication_manager.send_status({
                                "type": "nas_mount_failed",
                                "error": "Failed to mount NAS"
                            })
                    except Exception as e:
                        self.logger.error(f"(COMMAND HANDLER) Error mounting NAS: {e}")
                        self.communication_manager.send_status({
                            "type": "nas_mount_failed",
                            "error": str(e)
                        })
                else:
                    self.logger.error("(COMMAND HANDLER) No mount_nas callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for NAS operations"})
                return

            case "unmount_nas":
                if 'unmount_nas' in self.callbacks:
                    try:
                        success = self.callbacks['unmount_nas']()
                        if success:
                            self.communication_manager.send_status({
                                "type": "nas_unmounted",
                                "success": True
                            })
                        else:
                            self.communication_manager.send_status({
                                "type": "nas_unmount_failed",
                                "error": "Failed to unmount NAS"
                            })
                    except Exception as e:
                        self.logger.error(f"(COMMAND HANDLER) Error unmounting NAS: {e}")
                        self.communication_manager.send_status({
                            "type": "nas_unmount_failed",
                            "error": str(e)
                        })
                else:
                    self.logger.error("(COMMAND HANDLER) No unmount_nas callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for NAS operations"})
                return

        # If not a camera-specific command, pass to parent class
        super().handle_command(command, **kwargs)

class CameraModule(Module):
    def __init__(self, module_type="camera", config=None, config_file_path=None):
        # Initialize command handler first
        self.command_handler = CameraCommandHandler(
            logging.getLogger(f"{module_type}"),
            None,  # module_id will be set after super().__init__
            module_type,
            None,  # communication_manager will be set after super().__init__
            None,  # health_manager will be set after super().__init__
            None,  # config_manager will be set after super().__init__
            None,  # ptp_manager will be set after super().__init__
            None   # start_time will be set after super().__init__
        )
        
        # Call the parent class constructor
        super().__init__(module_type, config, config_file_path)
        
        # Update command handler with proper references
        self.command_handler.module_id = self.module_id
        self.command_handler.communication_manager = self.communication_manager
        self.command_handler.health_manager = self.health_manager
        self.command_handler.config_manager = self.config_manager
        self.command_handler.ptp_manager = self.ptp_manager
        self.command_handler.start_time = self.start_time
        
        # Camera specific variables
        self.video_folder = "rec"
        self.video_filetype = self.config_manager.get("camera.file_format", "h264")
        
        # Initialize camera
        self.picam2 = Picamera2()

        # Get camera modes
        self.camera_modes = self.picam2.sensor_modes
        self.logger.info(f"(MODULE) Camera modes: {self.camera_modes}")
        
        # Default camera config if not in config manager
        if not self.config_manager.get("camera"):
            self.config_manager.set("camera", {
                "fps": 100,
                "width": 1280,
                "height": 720,
                "codec": "h264",
                "profile": "high",
                "level": 4.2,
                "intra": 30,
                "file_format": "h264"
            })
            
        # Configure camera
        self.configure_camera()

        # State flags
        self.is_recording = False
        self.nas_mounted = False
        self.frame_times = []  # For storing frame timestamps

        # Set up camera-specific callbacks for the command handler
        self.command_handler.set_callbacks({
            'read_data': super().read_fake_data,  # Use base module's read_fake_data
            'stream_data': self.stream_data,
            'generate_session_id': lambda module_id: self.session_manager.generate_session_id(module_id),
            'samplerate': self.config_manager.get("module.samplerate", 200),
            'ptp_status': self.ptp_manager.get_status,
            'start_recording': self.start_recording,
            'stop_recording': self.stop_recording,
            'record_video': self.record_video,
            'export_video': self.export_video,
            'handle_update_camera_settings': self.handle_update_camera_settings,
            'list_recordings': self.list_recordings,
            'clear_recordings': self.clear_recordings,
            'export_to_nas': self.export_to_nas,
            'mount_nas': self.mount_nas,
            'unmount_nas': self.unmount_nas
        })

    def configure_camera(self):
        """Configure the camera with current settings"""
        try:
            # Get camera settings from config
            fps = self.config_manager.get("camera.fps", 100)
            width = self.config_manager.get("camera.width", 1280)
            height = self.config_manager.get("camera.height", 720)
            
            # Create video configuration
            config = self.picam2.create_video_configuration(
                main={"size": (width, height)} # From camera_timestamp_demo.py
                # main={"size": (width, height), "format": "RGB888"},
                # lores={"size": (640, 480), "format": "YUV420"},
                # controls={"FrameDurationLimits": (int(1000000/fps), int(1000000/fps))}  # Convert fps to microseconds
            )
            
            # Apply configuration
            self.picam2.configure(config)
            
            # Create encoder with current settings
            bitrate = self.config_manager.get("camera.bitrate", 10000000)
            self.encoder = H264Encoder(
                bitrate=bitrate,
                # repeat=True,  # Repeat SPS/PPS headers
                # iperiod=30,   # Key frame interval
                # framerate=fps
            )
            
            self.logger.info("(MODULE) Camera configured successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error configuring camera: {e}")
            return False
                
    def record_video(self, length: int = 10):
        """Record a video for a specific duration"""
        if length == 0:
            # If length is 0, start continuous recording
            filename = self.start_recording()
            if filename:
                # Don't send completion message for continuous recording
                return filename
            return None
        
        # Otherwise, record for specified duration
        self.logger.info(f"(MODULE) Starting video recording for {length} seconds")
        
        # Generate new session ID for this recording
        self.stream_session_id = self.session_manager.generate_session_id(self.module_id)
        
        # Create filename using just the session ID
        filename = f"{self.video_folder}/{self.stream_session_id}.{self.video_filetype}"
        
        # Ensure recording directory exists
        os.makedirs(self.video_folder, exist_ok=True)
        
        try:
            # Start recording
            self.picam2.start_recording(self.encoder, filename)
            self.logger.info(f"(MODULE) Recording started to {filename}")
            
            # Record and capture FrameWallClock during recording
            start_time = time.time()
            self.frame_times = []
            
            while time.time() - start_time < length:
                metadata = self.picam2.capture_metadata()
                frame_wall_clock = metadata.get('FrameWallClock', 'No data')
                if frame_wall_clock != 'No data':
                    self.frame_times.append(frame_wall_clock)
            
            # Stop recording
            self.picam2.stop_recording()
            
            # Save timestamps
            timestamps_file = f"{self.video_folder}/{self.stream_session_id}_timestamps.txt"
            np.savetxt(timestamps_file, self.frame_times)
            
            self.logger.info(f"(MODULE) Video recording completed successfully: {filename}")
            self.logger.info(f"(MODULE) Captured {len(self.frame_times)} frames")
            
            # Send status update
            self.communication_manager.send_status({
                "type": "video_recording_complete",
                "filename": filename,
                "length": length,
                "session_id": self.stream_session_id,
                "frame_count": len(self.frame_times)
            })
            
            return filename
                
        except Exception as e:
            self.logger.error(f"(MODULE) Error during video recording: {e}")
            self.communication_manager.send_status({
                "type": "video_recording_failed",
                "error": str(e)
            })
            return None
    
    def export_video(self, filename: str, length: int = 0, destination: str = "controller"):
        """Export a video to the specified destination
        
        Args:
            filename: Name of the file to export
            length: Optional length of the video
            destination: Where to export to - "controller" or "nas"
            
        Returns:
            bool: True if export successful
        """
        try:
            # Ensure the video file exists
            filepath = os.path.join(self.video_folder, filename)
            if not os.path.exists(filepath):
                self.logger.error(f"(MODULE) Video file not found: {filepath}")
                self.communication_manager.send_status({
                    "type": "video_export_failed",
                    "error": f"File not found: {filename}"
                })
                return False

            # Get the timestamp file path
            session_id = filename.split('.')[0]  # Remove extension
            timestamp_filename = f"{session_id}_timestamps.txt"
            timestamp_filepath = os.path.join(self.video_folder, timestamp_filename)

            if destination.lower() == "nas":
                # Export to NAS
                success = self.export_to_nas(filename)
                if success:
                    # Also export timestamp file if it exists
                    if os.path.exists(timestamp_filepath):
                        self.export_to_nas(timestamp_filename)
                    
                    self.logger.info(f"(MODULE) Video file and timestamps exported successfully to NAS")
                    self.communication_manager.send_status({
                        "type": "video_export_complete",
                        "filename": filename,
                        "session_id": self.stream_session_id,
                        "length": length,
                        "destination": "nas",
                        "has_timestamps": os.path.exists(timestamp_filepath)
                    })
                    return True
                else:
                    self.logger.error("(MODULE) Failed to export video file to NAS")
                    self.communication_manager.send_status({
                        "type": "video_export_failed",
                        "error": "Failed to export to NAS"
                    })
                    return False
            else:
                # Export to controller
                controller_ip = self.get_controller_ip()
            if not controller_ip:
                    self.logger.error("(MODULE) Could not find controller IP")
                    self.communication_manager.send_status({
                        "type": "video_export_failed",
                        "error": "Could not find controller IP"
                    })
                    return False
                    
                # Send the video file
            success = self.send_file(filepath, f"videos/{filename}")
            if success:
                    # Also send timestamp file if it exists
                    if os.path.exists(timestamp_filepath):
                        self.send_file(timestamp_filepath, f"videos/{timestamp_filename}")
                    
                    self.logger.info(f"(MODULE) Video file and timestamps sent successfully to controller")
                    self.communication_manager.send_status({
                        "type": "video_export_complete",
                        "filename": filename,
                        "session_id": self.stream_session_id,
                        "length": length,
                        "destination": "controller",
                        "has_timestamps": os.path.exists(timestamp_filepath)
                    })
                    return True
            else:
                    self.logger.error("(MODULE) Failed to send video file to controller")
                    self.communication_manager.send_status({
                        "type": "video_export_failed",
                        "error": "Failed to send video file"
                    })
                    return False
                
        except Exception as e:
            self.logger.error(f"(MODULE) Error exporting video file: {e}")
            self.communication_manager.send_status({
                "type": "video_export_failed",
                "error": str(e)
            })
            return False

    def start_recording(self):
        """Start continuous video recording"""
        if self.is_recording:
            self.logger.info("(MODULE) Already recording")
            return False
        
        # Generate new session ID for this recording
        self.stream_session_id = self.session_manager.generate_session_id(self.module_id)
        
        # Create filename using just the session ID
        filename = f"{self.video_folder}/{self.stream_session_id}.{self.video_filetype}"
        
        # Ensure recording directory exists
        os.makedirs(self.video_folder, exist_ok=True)
        
        try:
            # Start recording
            self.picam2.start_recording(self.encoder, filename)
            self.is_recording = True
            self.current_filename = filename
            self.recording_start_time = time.time()
            self.frame_times = []
            
            # Start frame capture thread
            self.capture_thread = threading.Thread(target=self._capture_frames)
            self.capture_thread.daemon = True
            self.capture_thread.start()
            
            # Send status update
            self.communication_manager.send_status({
                "type": "recording_started",
                "filename": filename,
                "session_id": self.stream_session_id
            })
            
            return filename
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error starting recording: {e}")
            self.communication_manager.send_status({
                "type": "recording_start_failed",
                "error": str(e)
            })
            return None

    def _capture_frames(self):
        """Background thread to capture frame timestamps"""
        while self.is_recording:
            try:
                metadata = self.picam2.capture_metadata()
                frame_wall_clock = metadata.get('FrameWallClock', 'No data')
                if frame_wall_clock != 'No data':
                    self.frame_times.append(frame_wall_clock)
            except Exception as e:
                self.logger.error(f"Error capturing frame metadata: {e}")
                time.sleep(0.001)  # Small delay to prevent CPU spinning
    
    def stop_recording(self):
        """Stop continuous video recording"""
        if not self.is_recording:
            self.logger.info("(MODULE) Not recording")
            return False
        
        try:
            # Stop recording
            self.picam2.stop_recording()
            
            # Stop frame capture thread
            self.is_recording = False
            if hasattr(self, 'capture_thread'):
                self.capture_thread.join(timeout=1.0)
            
            # Calculate duration
            duration = time.time() - self.recording_start_time
            
            # Save timestamps
            timestamps_file = f"{self.video_folder}/{self.stream_session_id}_timestamps.txt"
            np.savetxt(timestamps_file, self.frame_times)
            
            self.logger.info(f"(MODULE) Recording stopped. Captured {len(self.frame_times)} frames")
            
            self.communication_manager.send_status({
                "type": "recording_stopped",
                "filename": self.current_filename,
                "session_id": self.stream_session_id,
                "duration": duration,
                "frame_count": len(self.frame_times)
            })
            
            return True
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error stopping recording: {e}")
            self.communication_manager.send_status({
                "type": "recording_stop_failed",
                "error": str(e)
            })
            return False
        
    def get_controller_ip(self) -> str:
        """Get the controller's IP address"""
        if not hasattr(self, 'service_manager'):
            self.logger.error("(MODULE) Service manager not initialized")
            return None
        return self.service_manager.controller_ip
        
    def set_camera_parameters(self, params: dict) -> bool:
        """
        Set camera parameters and update config
        
        Args:
            params: Dictionary of camera parameters to update
            
        Returns:
            bool: True if successful
        """
        try:
            for key, value in params.items():
                config_key = f"camera.{key}"
                self.config_manager.set(config_key, value)
                
            # Update file format if it's in the params
            if 'file_format' in params:
                self.video_filetype = params['file_format']
                
            self.logger.info(f"(MODULE) Camera parameters updated: {params}")
            return True
        except Exception as e:
            self.logger.error(f"(MODULE) Error setting camera parameters: {e}")
            return False
        
    def read_fake_camera_frame(self):
        """Generate fake camera frame data"""
        # Create random 640x480 RGB frame
        frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        # Convert to base64 string for safe transmission
        return base64.b64encode(frame.tobytes()).decode('utf-8')
        
    def handle_update_camera_settings(self, params: dict) -> bool:
        """Handle update_camera_settings command"""
        try:
            # Update camera parameters
            success = self.set_camera_parameters(params)
            
            # Send status update
            self.communication_manager.send_status({
                "type": "camera_settings_updated",
                "settings": params,
                "success": success
            })
            
            return success
        except Exception as e:
            self.logger.error(f"(MODULE) Error updating camera settings: {e}")
            self.communication_manager.send_status({
                "type": "camera_settings_update_failed",
                "error": str(e)
            })
            return False
        
    def list_recordings(self):
        """List all recorded videos with metadata"""
        try:
            recordings = []
            if not os.path.exists(self.video_folder):
                return recordings
                
            for filename in os.listdir(self.video_folder):
                if filename.endswith(f".{self.video_filetype}"):
                    filepath = os.path.join(self.video_folder, filename)
                    stat = os.stat(filepath)
                    recordings.append({
                        "filename": filename,
                        # "path": filepath,
                        "size": stat.st_size,
                        "created": datetime.datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                        # "modified": stat.st_mtime,
                        # "session_id": filename.split('.')[0]  # Extract session ID from filename
                    })
            
            # Sort by creation time, newest first
            recordings.sort(key=lambda x: x["created"], reverse=True)
            return recordings
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error listing recordings: {e}")
            raise

    def clear_recordings(self, older_than: int = None, keep_latest: int = 0):
        """Clear old recordings
        
        Args:
            older_than: Optional timestamp - delete recordings older than this
            keep_latest: Optional number of latest recordings to keep
            
        Returns:
            dict with deleted_count and kept_count
        """
        try:
            if not os.path.exists(self.video_folder):
                return {"deleted_count": 0, "kept_count": 0}
                
            # Get list of recordings
            recordings = self.list_recordings()
            if not recordings:
                return {"deleted_count": 0, "kept_count": 0}
                
            # Sort by creation time, newest first
            recordings.sort(key=lambda x: x["created"], reverse=True)
            
            deleted_count = 0
            kept_count = 0
            
            # Keep the latest N recordings if specified
            if keep_latest > 0:
                kept_recordings = recordings[:keep_latest]
                recordings = recordings[keep_latest:]
                kept_count = len(kept_recordings)
            
            # Delete recordings older than timestamp if specified
            for recording in recordings:
                if older_than and recording["created"] >= older_than:
                    continue
                    
                try:
                    os.remove(recording["path"])
                    deleted_count += 1
                except Exception as e:
                    self.logger.error(f"(MODULE) Error deleting recording {recording['filename']}: {e}")
            
            return {
                "deleted_count": deleted_count,
                "kept_count": kept_count
            }
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error clearing recordings: {e}")
            raise

    def mount_nas(self) -> bool:
        """Mount the NAS share"""
        if not self.config_manager.get("nas.enabled", False):
            self.logger.error("(MODULE) NAS export is not enabled in config")
            return False
            
        try:
            # Get NAS config
            host = self.config_manager.get("nas.host")
            share = self.config_manager.get("nas.share")
            mount_point = self.config_manager.get("nas.mount_point")
            
            # Get credentials from environment
            username = os.getenv('NAS_USERNAME')
            password = os.getenv('NAS_PASSWORD')
            
            if not username or not password:
                self.logger.error("(MODULE) NAS credentials not found in environment variables")
                return False
            
            # Create mount point if it doesn't exist
            os.makedirs(mount_point, exist_ok=True)
            
            # Build mount command
            mount_cmd = ["sudo", "mount", "-t", "cifs"]
            mount_cmd.extend(["-o", f"username={username},password={password}"])
            mount_cmd.extend([f"//{host}/{share}", mount_point])
            
            # Execute mount command
            result = subprocess.run(mount_cmd, capture_output=True, text=True)
            if result.returncode == 0:
                self.nas_mounted = True
                self.logger.info(f"(MODULE) NAS mounted successfully at {mount_point}")
                return True
            else:
                self.logger.error(f"(MODULE) Failed to mount NAS: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"(MODULE) Error mounting NAS: {e}")
            return False

    def unmount_nas(self) -> bool:
        """Unmount the NAS share"""
        if not self.nas_mounted:
            return True
            
        try:
            mount_point = self.config_manager.get("nas.mount_point")
            result = subprocess.run(["sudo", "umount", mount_point], capture_output=True, text=True)
            if result.returncode == 0:
                self.nas_mounted = False
                self.logger.info("(MODULE) NAS unmounted successfully")
                return True
            else:
                self.logger.error(f"(MODULE) Failed to unmount NAS: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"(MODULE) Error unmounting NAS: {e}")
            return False

    def export_to_nas(self, filename: str = None) -> bool:
        """Export video(s) to NAS
        
        Args:
            filename: Optional specific file to export. If None, exports all recordings.
            
        Returns:
            bool: True if export successful
        """
        if not self.config_manager.get("nas.enabled", False):
            self.logger.error("(MODULE) NAS export is not enabled in config")
            return False
            
        try:
            # Mount NAS if not already mounted
            if not self.nas_mounted:
                if not self.mount_nas():
                    return False
                    
            # Get NAS config
            mount_point = self.config_manager.get("nas.mount_point")
            export_folder = self.config_manager.get("nas.export_folder")
            export_path = os.path.join(mount_point, export_folder)
            
            # Create export folder if it doesn't exist
            os.makedirs(export_path, exist_ok=True)
            
            if filename:
                # Export specific file
                source = os.path.join(self.video_folder, filename)
                if not os.path.exists(source):
                    self.logger.error(f"(MODULE) File not found: {source}")
                    return False
                    
                dest = os.path.join(export_path, filename)
                shutil.copy2(source, dest)
                self.logger.info(f"(MODULE) Exported {filename} to NAS")
                
            else:
                # Export all recordings
                exported = 0
                for file in os.listdir(self.video_folder):
                    if file.endswith(f".{self.video_filetype}"):
                        source = os.path.join(self.video_folder, file)
                        dest = os.path.join(export_path, file)
                        shutil.copy2(source, dest)
                        exported += 1
                        
                self.logger.info(f"(MODULE) Exported {exported} files to NAS")
                
            return True
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error exporting to NAS: {e}")
            return False
        finally:
            # Unmount NAS if we mounted it
            if not self.nas_mounted:
                self.unmount_nas()

    def stop(self) -> bool:
        """Stop the module and cleanup"""
        try:
            # Stop recording if active
            if self.is_recording:
                self.stop_recording()
                
            # Unmount NAS if mounted
            if self.nas_mounted:
                self.unmount_nas()
                
            # Call parent stop
            return super().stop()
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error stopping module: {e}")
            return False
        
        
    