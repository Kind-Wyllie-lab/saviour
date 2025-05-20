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

class CameraCommandHandler(ModuleCommandHandler):
    """Command handler specific to camera functionality"""
    
    def handle_command(self, command: str, **kwargs):
        """Handle camera-specific commands while preserving base functionality"""
        
        # Handle camera-specific commands
        match command:
            case "start_recording":
                if 'start_recording' in self.callbacks:
                    result = self.callbacks['start_recording']()
                    self.communication_manager.send_status({"recording_started": result})
                else:
                    self.logger.error("No start_recording callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for recording"})
                return

            case "stop_recording":
                if 'stop_recording' in self.callbacks:
                    result = self.callbacks['stop_recording']()
                    self.communication_manager.send_status({"recording_stopped": result})
                else:
                    self.logger.error("No stop_recording callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for recording"})
                return

            case "record_video":
                if 'record_video' in self.callbacks:
                    length = kwargs.get('length', 10)  # Default 10 seconds
                    filename = self.callbacks['record_video'](length)
                    if filename:
                        self.communication_manager.send_status({ # Is this a duplicate with record_video?
                            "video_recorded": True,
                            "filename": filename,
                            "length": length
                        })
                    else:
                        self.communication_manager.send_status({
                            "video_recorded": False,
                            "error": "Recording failed"
                        })
                else:
                    self.logger.error("No record_video callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for video recording"})
                return

            case "export_video":
                if 'export_video' in self.callbacks:
                    filename = kwargs.get('filename')
                    length = kwargs.get('length', 10)
                    if not filename:
                        self.communication_manager.send_status({
                            "error": "No filename provided for export"
                        })
                        return
                    
                    success = self.callbacks['export_video'](filename, length)
                    self.communication_manager.send_status({
                        "video_exported": success,
                        "filename": filename
                    })
                else:
                    self.logger.error("No export_video callback provided")
                    self.communication_manager.send_status({"error": "Module not configured for video export"})
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
        self.video_filetype = self.config_manager.get("camera.file_format", "mp4")
        
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
                "file_format": "mp4"
            })

        # State flags
        self.is_recording = False

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
            'export_video': self.export_video
        })

    def record_video(self, length: int = 10):
        """Record a video with session management"""
        self.logger.info(f"Starting video recording for {length} seconds")
        
        # Generate session ID if not exists
        if not self.stream_session_id:
            self.stream_session_id = self.session_manager.generate_session_id(self.module_id)
        
        # Create filename using just the session ID
        filename = f"{self.video_folder}/{self.stream_session_id}.{self.video_filetype}"
        
        # Ensure recording directory exists
        os.makedirs(self.video_folder, exist_ok=True)
        
        # Get camera settings from config
        fps = self.config_manager.get("camera.fps", 100)
        width = self.config_manager.get("camera.width", 1280)
        height = self.config_manager.get("camera.height", 720)
        codec = self.config_manager.get("camera.codec", "h264")
        profile = self.config_manager.get("camera.profile", "high")
        level = self.config_manager.get("camera.level", 4.2)
        intra = self.config_manager.get("camera.intra", 30)
        
        # Build command with config settings
        cmd = [
            "rpicam-vid",
            "--framerate", str(fps),
            "--width", str(width),
            "--height", str(height),
            "-t", f"{length}s",
            "-o", filename,
            "--nopreview",
            "--level", str(level),
            "--codec", codec,
            "--profile", profile,
            "--intra", str(intra)
        ]

        self.logger.info(f"Recording video to {filename}")
        self.logger.info(f"Command: {' '.join(cmd)}")

        try:
            # Execute the command
            process = subprocess.Popen(cmd)
            process.wait()  # Wait for recording to complete
            
            if process.returncode == 0:
                self.logger.info(f"Video recording completed successfully: {filename}")
                # Send status update
                self.communication_manager.send_status({ # Is this a duplicate with 
                    "type": "video_recording_complete",
                    "filename": filename,
                    "length": length,
                    "session_id": self.stream_session_id
                })
                return filename
            else:
                self.logger.error(f"Video recording failed with return code {process.returncode}")
                self.communication_manager.send_status({
                    "type": "video_recording_failed",
                    "error": f"Recording failed with return code {process.returncode}"
                })
                return None
                
        except Exception as e:
            self.logger.error(f"Error during video recording: {e}")
            self.communication_manager.send_status({
                "type": "video_recording_failed",
                "error": str(e)
            })
            return None

    def export_video(self, filename: str, length: int):
        """Export a recorded video to the controller"""
        try:
            # Get controller IP from zeroconf
            controller_ip = self.get_controller_ip()
            if not controller_ip:
                self.logger.error("Could not find controller IP")
                self.communication_manager.send_status({
                    "type": "video_export_failed",
                    "error": "Could not find controller IP"
                })
                return False
                
            # Send the file
            success = self.send_file(filename, f"videos/{os.path.basename(filename)}")
            if success:
                self.logger.info(f"Video file sent successfully to controller")
                self.communication_manager.send_status({
                    "type": "video_export_complete",
                    "filename": filename,
                    "session_id": self.stream_session_id,
                    "length": length
                })
                return True
            else:
                self.logger.error("Failed to send video file to controller")
                self.communication_manager.send_status({
                    "type": "video_export_failed",
                    "error": "Failed to send video file"
                })
                return False
                
        except Exception as e:
            self.logger.error(f"Error exporting video file: {e}")
            self.communication_manager.send_status({
                "type": "video_export_failed",
                "error": str(e)
            })
            return False

    def start_recording(self):
        """Start recording a video stream"""
        self.logger.info("Starting video recording")
        
        filename = f"{self.video_folder}/{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_test')}.{self.video_filetype}"

        # TODO: Implement video recording
        if self.is_recording:
            self.logger.info("Video recording already in progress")
            return False
        else:
            self.is_recording = True
            return True

        # Get camera settings from config
        fps = self.config_manager.get("camera.fps", 100)
        width = self.config_manager.get("camera.width", 1280)
        height = self.config_manager.get("camera.height", 720)
        codec = self.config_manager.get("camera.codec", "h264")
        level = self.config_manager.get("camera.level", 4.2)

        cmd = [
            "rpicam-vid",
            "-t", "0",
            "--framerate", str(fps),
            "--width", str(width),
            "--height", str(height),
            "-o", f"{filename}",
            "--nopreview",
            "--level", str(level),
            "--codec", codec,
        ]

        subprocess.run(cmd)
    
    def stop_recording(self):
        """Stop recording a video stream"""
        self.logger.info("Stopping video recording")
        
        # TODO: Implement video recording
        if self.is_recording:
            self.is_recording = False
            return True
        else:
            self.logger.info("Video recording not in progress")
            return False
        
    def get_controller_ip(self) -> str:
        """Get the controller's IP address"""
        return self.controller_ip
        
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
                
            self.logger.info(f"Camera parameters updated: {params}")
            return True
        except Exception as e:
            self.logger.error(f"Error setting camera parameters: {e}")
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
            self.logger.error(f"Error updating camera settings: {e}")
            self.communication_manager.send_status({
                "type": "camera_settings_update_failed",
                "error": str(e)
            })
            return False
        
        
    