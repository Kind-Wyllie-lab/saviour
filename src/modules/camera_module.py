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

class CameraModule(Module):
    def __init__(self, module_type="camera", config=None, config_file_path=None):
        # Call the parent class constructor
        super().__init__(module_type, config, config_file_path)
        
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


    def handle_command(self, command, **kwargs):
        """Handle camera-specific commands while preserving base module functionality"""

        # Handle camera-specific commands
        match command:
            case "start_recording":
                output_video = kwargs.get('output_video', 'recording.h264')
                output_timestamps = kwargs.get('output_timestamps', 'timestamps.txt')
                fps = kwargs.get('fps', self.config_manager.get('camera.fps', 30))
                return self.start_recording(output_video, output_timestamps, fps)
                
            case "stop_recording":
                return self.stop_recording()

            case "record_video":
                # Get recording parameters from kwargs or use defaults
                length = kwargs.get('length', 3)  # Default 10 seconds
                self.logger.info(f"Received record_video command with length={length}s")
                
                # Start recording
                filename = self.record_video(length)
                
                if filename:
                    self.logger.info(f"Video recording completed: {filename}")
                    return True
                else:
                    self.logger.error("Video recording failed")
                    return False
                
            # If not a camera-specific command, pass to parent class
            case _:
                return super().handle_command(command, **kwargs)

    def record_video(self, length: int = 10, send_to_controller: bool = True):
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
                
                # Send the video file to the controller
                if send_to_controller:
                    self.send_video_file(filename, length)

            else:
                self.logger.error(f"Video recording failed with return code {process.returncode}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error during video recording: {e}")
            return None
    
        return filename

    def send_video_file(self, filename: str, length: int):
        # Send the video file to the controller
        try:
            # Get controller IP from zeroconf
            controller_ip = self.get_controller_ip()
            if not controller_ip:
                self.logger.error("Could not find controller IP")
                return None
                
            # Send the file
            success = self.send_file(filename, f"videos/{os.path.basename(filename)}")
            if success:
                self.logger.info(f"Video file sent successfully to controller")
            else:
                self.logger.error("Failed to send video file to controller")
                return None
                
        except Exception as e:
            self.logger.error(f"Error sending video file: {e}")
            return None
        
        # Send status update to controller
        self.send_status({
            "type": "video_recording_complete",
            "timestamp": time.time(),
            "filename": filename,
            "session_id": self.stream_session_id,
            "duration": length
        })
        return filename

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
        
        
    