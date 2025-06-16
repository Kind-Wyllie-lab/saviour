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
import threading
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput, FfmpegOutput
import json
from flask import Flask, Response
import cv2

class CameraCommandHandler(ModuleCommandHandler):
    """Command handler specific to camera functionality"""
    
    def __init__(self, logger, module_id, module_type, config_manager=None, start_time=None):
        super().__init__(logger, module_id, module_type, config_manager, start_time)
        self.logger.info("(CAMERA COMMAND HANDLER) Initialised")

    def handle_command(self, command: str):
        """Handle camera-specific commands while preserving base functionality"""
        self.logger.info("(CAMERA COMMAND HANDLER) Checking for camera specific commands.")
        
        try:
            # Parse command and parameters
            parts = command.split()
            cmd = parts[0]
            params = parts[1:] if len(parts) > 1 else []
            
            # Handle camera-specific commands
            match cmd:
                case "update_camera_settings":
                    self._handle_update_camera_settings(params)
                case "start_streaming":
                    self._handle_start_streaming(params)
                case "stop_streaming":
                    self._handle_stop_streaming()
                case _:
                    # If not a camera-specific command, pass to parent class
                    super().handle_command(command)
                    
        except Exception as e:
            self._handle_error(e)

    def _handle_update_camera_settings(self, params: list):
        """Handle update_camera_settings command"""
        self.logger.info("(CAMERA COMMAND HANDLER) Command identified as update_camera_settings")
        try:
            if not params:
                raise ValueError("No settings provided for update_camera_settings")
            
            settings = json.loads(params[0])
            if 'handle_update_camera_settings' in self.callbacks:
                success = self.callbacks['handle_update_camera_settings'](settings)
                if success:
                    self.callbacks["send_status"]({
                        "type": "camera_settings_updated",
                        "settings": settings,
                        "success": True
                    })
                else:
                    self.callbacks["send_status"]({
                        "type": "camera_settings_update_failed",
                        "error": "Failed to update settings"
                    })
            else:
                self.logger.error("(CAMERA COMMAND HANDLER) No handle_update_camera_settings callback provided")
                self.callbacks["send_status"]({
                    "type": "camera_settings_update_failed",
                    "error": "Module not configured for camera settings"
                })
        except json.JSONDecodeError:
            self.logger.error("(COMMAND HANDLER) Invalid JSON in update_camera_settings command")
            self.callbacks["send_status"]({
                "type": "camera_settings_update_failed",
                "error": "Invalid JSON format"
            })
        except Exception as e:
            self.logger.error(f"(CAMERA COMMAND HANDLER) Error updating camera settings: {str(e)}")
            self.callbacks["send_status"]({
                "type": "camera_settings_update_failed",
                "error": str(e)
            })

    def _handle_start_streaming(self, params: list):
        """Handle start_streaming command"""
        self.logger.info("(CAMERA COMMAND HANDLER) Command identified as start_streaming")
        try:
            # Default to localhost if no IP provided
            receiver_ip = params[0] if params else None # TODO: No longer required
            port = params[1] if len(params) > 1 else "10001" # TODO: No longer required with flask approach?
            
            if 'start_streaming' in self.callbacks:
                self.callbacks['start_streaming'](receiver_ip, port)
            else:
                self.logger.error("(COMMAND HANDLER) No start_streaming callback provided")
                self.callbacks["send_status"]({
                    "type": "streaming_start_failed",
                    "error": "Module not configured for streaming"
                })
        except Exception as e:
            self.logger.error(f"(COMMAND HANDLER) Error starting stream: {str(e)}")
            self.callbacks["send_status"]({
                "type": "streaming_start_failed",
                "error": str(e)
            })

    def _handle_stop_streaming(self):
        """Handle stop_streaming command"""
        self.logger.info("(CAMERA COMMAND HANDLER) Command identified as stop_streaming")
        if 'stop_streaming' in self.callbacks:
            success = self.callbacks['stop_streaming']()
            if success:
                self.callbacks["send_status"]({
                    "type": "streaming_stopped"
                })
            else:
                self.callbacks["send_status"]({
                    "type": "streaming_stop_failed",
                    "error": "Failed to stop streaming"
                })
        else:
            self.logger.error("(COMMAND HANDLER) No stop_streaming callback provided")
            self.callbacks["send_status"]({
                "type": "streaming_stop_failed",
                "error": "Module not configured for streaming"
            })

class CameraModule(Module):
    def __init__(self, module_type="camera", config=None, config_file_path=None):
        # Initialize command handler before parent class
        self.command_handler = CameraCommandHandler(
            logger=logging.getLogger(f"{module_type}.{self.generate_module_id(module_type)}"),
            module_id=self.generate_module_id(module_type),
            module_type=module_type,
            config_manager=None,  # Will be set by parent class
            start_time=None  # Will be set during start()
        )
        
        # Call the parent class constructor
        super().__init__(module_type, config, config_file_path)
        
        # Set up callbacks
        self.callbacks = {}
        
        # Set up export manager callbacks
        self.export_manager.set_callbacks({
            'get_controller_ip': lambda: self.service_manager.controller_ip
        })
    
        # Initialize camera
        self.picam2 = Picamera2()

        # Get camera modes
        self.camera_modes = self.picam2.sensor_modes
        self.logger.info(f"(MODULE) Camera modes: {self.camera_modes}")
        time.sleep(0.1)
    
        # Streaming variables
        self.streaming_app = Flask(__name__)
        self.streaming_server_thread = None
        self.register_routes()

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
        time.sleep(0.1)
        self.configure_camera()
        time.sleep(0.1)

        # State flags
        self.is_recording = False
        self.is_streaming = False
        self.latest_recording = None
        self.frame_times = []  # For storing frame timestamps

        # Set up camera-specific callbacks for the command handler
        self.command_handler.set_callbacks({
            'generate_session_id': lambda module_id: self.session_manager.generate_session_id(module_id),
            'get_samplerate': lambda: self.config_manager.get("module.samplerate", 200),
            'get_ptp_status': self.ptp_manager.get_status,
            'get_streaming_status': lambda: self.is_streaming,
            'get_recording_status': lambda: self.is_recording,
            'send_status': lambda status: self.communication_manager.send_status(status),
            'get_health': self.health_manager.get_health,
            'start_recording': self.start_recording,
            'stop_recording': self.stop_recording,
            'list_recordings': self.list_recordings,
            'clear_recordings': self.clear_recordings,
            'export_recordings': self.export_recordings,
            'handle_update_camera_settings': self.handle_update_camera_settings,  # Camera specific
            'get_latest_recording': self.get_latest_recording,  # Camera specific
            'start_streaming': self.start_streaming,
            'stop_streaming': self.stop_streaming,
            'get_controller_ip': self.service_manager.controller_ip
        })

        self.logger.info(f"(CAMERA MODULE) Command handler callbacks: {self.command_handler.callbacks}")

    def configure_camera(self):
        """Configure the camera with current settings"""
        try:
            # Get camera settings from config
            fps = self.config_manager.get("camera.fps", 100)
            width = self.config_manager.get("camera.width", 1280)
            height = self.config_manager.get("camera.height", 720)
            
            # Create video configuration
            config = self.picam2.create_video_configuration(
                main={"size": (width, height)},
                lores={"size": (640, 360)} # Lower, #TODO: take this from config
            )
            
            # Apply configuration
            self.picam2.configure(config)
            
            # Create encoders with current settings
            bitrate = self.config_manager.get("camera.bitrate", 10000000)
            self.main_encoder = H264Encoder(bitrate=bitrate)
            self.lores_encoder = H264Encoder(bitrate=bitrate/10) # Lower bitrate for streaming

            self.logger.info("(MODULE) Camera configured successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error configuring camera: {e}")
            return False

    def start_recording(self) -> bool:
        """Start continuous video recording"""
        # First call parent class to handle common recording setup
        filename = super().start_recording()
        if not filename:
            return False
        
        try:
            # Start the camera if not already running
            if not self.picam2.started:
                self.picam2.start()
                time.sleep(0.1)  # Give camera time to start
            
            # Create file output
            self.file_output = FileOutput(filename)
            self.main_encoder.output = self.file_output
            
            # Start recording
            self.picam2.start_encoder(self.main_encoder, name="main")
            self.is_recording = True
            self.recording_start_time = time.time()
            self.frame_times = []  # Reset frame times
            
            # Start frame capture thread
            self.capture_thread = threading.Thread(target=self._capture_frames)
            self.capture_thread.daemon = True
            self.capture_thread.start()
            
            # Send status response after successful recording start
            self.communication_manager.send_status({
                "type": "recording_started",
                "filename": filename,
                "session_id": self.recording_session_id
            })
            
            return True
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error starting recording: {e}")
            self.communication_manager.send_status({
                "type": "recording_start_failed",
                "error": str(e)
            })
            return False

    def _capture_frames(self):
        """Background thread to capture frame timestamps"""
        while self.is_recording:
            try:
                # Request a frame to ensure we get metadata
                self.picam2.capture_metadata()
                metadata = self.picam2.capture_metadata()
                frame_wall_clock = metadata.get('FrameWallClock', 'No data')
                if frame_wall_clock != 'No data':
                    self.frame_times.append(frame_wall_clock)
            except Exception as e:
                self.logger.error(f"Error capturing frame metadata: {e}")
                time.sleep(0.001)  # Small delay to prevent CPU spinning
    
    def stop_recording(self) -> bool:
        """Stop continuous video recording"""
        # First check if recording using parent class
        if not super().stop_recording():
            return False
        
        try:
            # Stop recording with camera-specific code
            self.picam2.stop_encoder(self.main_encoder)
            
            # Stop frame capture thread
            self.is_recording = False
            if hasattr(self, 'capture_thread'):
                self.capture_thread.join(timeout=1.0)
            
            # Calculate duration
            if self.recording_start_time is not None:
                duration = time.time() - self.recording_start_time
                
                # Save timestamps
                timestamps_file = f"{self.recording_folder}/{self.recording_session_id}_timestamps.txt"
                np.savetxt(timestamps_file, self.frame_times)
                
                # Send status response after successful recording stop
                self.communication_manager.send_status({
                    "type": "recording_stopped",
                    "filename": self.current_filename,
                    "session_id": self.recording_session_id,
                    "duration": duration,
                    "frame_count": len(self.frame_times),
                    "status": "success",
                    "message": f"Recording completed successfully with {len(self.frame_times)} frames"
                })
                
                return True
            else:
                self.logger.error("(MODULE) Error: recording_start_time was None")
                self.communication_manager.send_status({
                    "type": "recording_stopped",
                    "status": "error",
                    "error": "Recording start time was not set, so could not create timestamps."
                })
                return False
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error stopping recording: {e}")
            self.communication_manager.send_status({
                "type": "recording_stopped",
                "status": "error",
                "error": str(e)
            })
            return False
        
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
                self.recording_filetype = params['file_format']
                
            self.logger.info(f"(MODULE) Camera parameters updated: {params}")
            return True
        except Exception as e:
            self.logger.error(f"(MODULE) Error setting camera parameters: {e}")
            return False
        
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

    def get_latest_recording(self):
        """Get the latest recording"""
        return self.latest_recording

    def start_streaming(self, receiver_ip=None, port=None) -> bool:
        """Start streaming video to the specified receiver using Flask to send MJPEG"""
        try:
            # Check if already streaming
            if self.is_streaming:
                self.logger.warning("(MODULE) Already streaming")
                return False

            # Check for missing ip / port, default to controller ip
            if not receiver_ip:
                receiver_ip = self.service_manager.controller_ip # TODO: No longer required
            if not port:
                port = self.config_manager.get('streaming.port', 8080)
            
            # Convert port to integer
            try:
                port = int(port)
            except (ValueError, TypeError):
                self.logger.error(f"(MODULE) Invalid port number: {port}")
                self.communication_manager.send_status({
                    'type': 'streaming_start_failed',
                    'error': f"Invalid port number: {port}"
                })
                return False

            self.logger.info(f"(MODULE) Starting streaming from {self.service_manager.ip}:{port}")

            # Start the camera if not already running
            if not self.picam2.started:
                self.picam2.start()
                time.sleep(0.1)  # Give camera time to start
            
            # Start the thread
            self.streaming_server_thread = threading.Thread(target=self.run_streaming_server)
            self.streaming_server_thread.start()

            # Set flag to true
            self.is_streaming = True
            
            # Send streaming status
            self.communication_manager.send_status({
                'type': 'streaming_started',
                'receiver_ip': receiver_ip,
                'port': port,
                'status': 'success',
                'message': f'Streaming started to {receiver_ip}:{port}'
            })
            
            return True
            
        except Exception as e:
            self.logger.error(f"(CAMERA MODULE) Error starting streaming: {str(e)}")
            self.communication_manager.send_status({
                'type': 'streaming_start_failed',
                'status': 'error',
                'error': f"Failed to start streaming: {str(e)}"
            })
            return False

    def generate_streaming_frames(self):
        """Generate streaming frames for MJPEG stream"""
        while True:
            try:
                frame_yuv = self.picam2.capture_array("lores")  # YUV420 format
                # Convert YUV420 (I420) to BGR (OpenCV default)
                frame_bgr = cv2.cvtColor(frame_yuv, cv2.COLOR_YUV2BGR_I420)
                ret, jpeg = cv2.imencode('.jpg', frame_bgr)
                if not ret:
                    continue
                yield (b'--frame\r\n'
                    b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            except Exception as e:
                self.logger.error(f"Error generating streaming frame: {e}")
                time.sleep(0.1)  # Small delay to prevent CPU spinning

    def register_routes(self):
        """Register Flask routes for streaming"""
        @self.streaming_app.route('/')
        def index():
            return '<h1>Live Camera</h1><img src="/video_feed">'

        @self.streaming_app.route('/video_feed')
        def video_feed():
            return Response(self.generate_streaming_frames(),
                            mimetype='multipart/x-mixed-replace; boundary=frame')

    def run_streaming_server(self, port=8080):
        """Run the flask server to stream upon"""
        try:
            self.streaming_app.run(host='0.0.0.0', port=port)
        except Exception as e:
            self.logger.error(f"Error running streaming server: {e}")
            self.is_streaming = False

    def stop_streaming(self) -> bool:
        """Stop streaming video"""
        try:
            if not self.is_streaming:
                self.logger.warning("(MODULE) Not currently streaming")
                return False
            
            # Stop the encoder
            self.streaming_server_thread.join(timeout=1.0)
            self.is_streaming = False
            
            self.communication_manager.send_status({
                "type": "streaming_stopped",
                "status": "success",
                "message": "Streaming stopped successfully"
            })
            
            return True
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error stopping stream: {e}")
            self.communication_manager.send_status({
                "type": "streaming_stopped",
                "status": "error",
                "error": f"Failed to stop streaming: {str(e)}"
            })
            return False

    def stop(self) -> bool:
        """Stop the module and cleanup"""
        try:
            # Stop recording if active
            if self.is_recording:
                self.stop_recording()
                
            # Call parent stop
            return super().stop()
            
        except Exception as e:
            self.logger.error(f"(MODULE) Error stopping module: {e}")
            return False