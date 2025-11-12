#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - APA Camera Module Class

This class extends the base Module class to handle camera-specific functionality for the APA test rig.

Picamera2 is used for interfacing camera hardware. This is a python wrapper for libcamera / rpicam.

The software expects an IMX500 Pi AI camera module. This is required to efficiently deploy a rat tracking neural net.

Author: Andrew SG
Created: 11/11/2025
"""

import datetime
import sys
import os
import time
import logging
import numpy as np
import threading
from picamera2 import Picamera2, MappedArray
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput, FfmpegOutput
from picamera2.devices import IMX500
from picamera2.devices.imx500 import (NetworkIntrinsics, postprocess_nanodet_detection)
from functools import lru_cache
import json
from flask import Flask, Response, request
import cv2
from typing import Optional, Dict
from collections import deque

# Import SAVIOUR dependencies
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module

class Detection:
    def __init__(self, category, conf, box):
        """Create a Detection object, recording the bounding box, category and confidence."""
        self.category = category
        self.conf = conf
        self.box = box

class APACameraModule(Module):
    def __init__(self, module_type="apa_camera"):        
        # Call the parent class constructor
        super().__init__(module_type)

        # Update config 
        self.config.load_module_config("apa_camera_config.json")
    
        # IMX500 AI Camera Setup
        # self.imx500 = IMX500("/usr/share/imx500-models/imx500_network_ssd_mobilenetv2_fpnlite_320x320_pp.rpk")
        self.imx500 = IMX500("/usr/share/imx500-models/imx500_network_nanodet_plus_416x416_pp.rpk")
        
        self.intrinsics = self.imx500.network_intrinsics
        if not self.intrinsics:
            self.intrinsics = NetworkIntrinsics()
            self.intrinsics.task = "object detection"
        elif self.intrinsics.task != "object detection":
            self.logger.warning("Network is not an object detection task")
        self._detection_buffer = deque(maxlen=3) # Last 3 frames
        self._detections = None
        self._last_detections = None
        self._last_known_det = None  # store last detected object

        # Initialize camera
        self.picam2 = Picamera2(self.imx500.camera_num)
        self.height = None
        self.width = None
        self.fps = None
        self.mode = None

        # Get camera modes
        self.camera_modes = self.picam2.sensor_modes
        time.sleep(0.1)

        # Shock zone
        self.inner_offset = None
        self.outer_radius = None
        self.start_angle = None
        self.end_angle = None

        # Rat Tracking
        self.last_cx = None
        self.last_cy = None
    
        # Streaming variables
        self.streaming_app = Flask(__name__)
        self.streaming_server_thread = None
        self.streaming_server = None
        self.streaming_server_process = None
        self.should_stop_streaming = False  # Add flag for graceful shutdown
        self.register_routes()
            
        # Configure camera
        time.sleep(0.1)
        self._configure_camera()
        time.sleep(0.1)

        # State flags
        self.is_recording = False
        self.is_streaming = False
        self.frame_times = []  # For storing frame timestamps

        # Set up camera-specific callbacks for the command handler
        self.camera_callbacks = {
            'start_streaming': self.start_streaming,
            'stop_streaming': self.stop_streaming
        }
        self.command.set_callbacks(self.camera_callbacks) # Append new camera callbacks
        self.logger.info(f"Command handler callbacks: {self.command.callbacks}")

        self._configure_mask_and_shock_zone()
        self._configure_object_detection()

    def configure_module(self, updated_keys: Optional[list[str]]):
        """Override parent method configure module in event that module config changes"""
        if self.is_streaming:
            self.logger.info("Camera settings changed, restarting stream to apply new configuration")
            self._configure_mask_and_shock_zone()
            self._configure_object_detection()
            restart_keys = [
                "camera.fps",
                "camera.width",
                "camera.height"
            ]
            self._restarting_stream = False
            for key in updated_keys:
                if key in restart_keys:
                    self._restarting_stream = True
            
            if self._restarting_stream == True:
                self.stop_streaming()
                time.sleep(1)
                try:
                    self._configure_camera()
                    self.logger.info("Camera reconfigured successfully")
                except Exception as e:
                    self.logger.error(f"Error restarting streaming: {e}")
                
                # Restart stream
                try:
                    self.logger.info("Restarting stream with new settings")
                    self.start_streaming()
                    self.logger.info("Streaming restarted")
                except Exception as e:
                    self.logger.error(f"Error restarting streaming: {e}")
            
            self._restarting_stream = False # Reset the "restarting stream" flag
        elif not self.is_streaming:
            self.logger.info("Camera settings changed but not streaming, going straight to applying new configuration")
            try:
                self._configure_camera()
                self.logger.info("Camera reconfigured successfully (not streaming)")
            except Exception as e:
                self.logger.error(f"Error reconfiguring camera: {e}")

    def _configure_object_detection(self):
        """Reconfigure object detection settings"""
        self.threshold = self.config.get("object_detection.threshold") # Confidence to signify a detection TODO: Take from config
        self.iou = self.config.get("object_detection.iou_threshold") # IOU threshold
        self.max_detections = self.config.get("object_detection.max_detections")

    def _configure_camera(self):
        """Configure the camera with current settings"""
        try:
            self.logger.info("Configure camera called")

            if self.picam2.started:
                self.picam2.stop()

            # Get camera settings from config
            self.fps = self.config.get("camera.fps", 25)  # Default to 25fps
            self.width = self.config.get("camera.width", 1280)
            self.height = self.config.get("camera.height", 720)
            
            # Pick appropriate sensor mode - we will use mode 0 by default
            self.mode = self.camera_modes[0]

            sensor = {"output_size": self.mode["size"], "bit_depth":self.mode["bit_depth"]} # Here we specify the correct camera mode for our application, I use mode 0 because it is capable of the highest framerates.
            main = {"size": (self.width, self.height), "format": "RGB888"} # The main stream - we will use this for recordings. YUV420 is good for higher framerates.
            lores = {"size": (self.width, self.height), "format":"RGB888"} # A lores stream for network streaming. RGB888 requires less processing.
            controls = {"FrameRate": self.fps} # target framerate, in reality it might be lower.
            
            if self.config.get("camera.monochrome") is True:
                self.logger.info("Camera configured for grayscale - applying grayscale conversion in pre-callback.")

            self.logger.info(f"Sensor stream set to size {self.width},{self.height} and bit depth {self.mode['bit_depth']} to target {self.fps}fps.")

            # Create video configuration with explicit framerate
            config = self.picam2.create_video_configuration(main=main,
                        lores=lores,
                        sensor=sensor,
                        controls=controls,
                        buffer_count=16) # Buffer size of 16 increases potential framerate.
            
            # Apply configuration
            self.picam2.configure(config)

            # Apply callback
            self.picam2.pre_callback = self._frame_precallback
            
            # Create encoders with current settings
            bitrate = self.config.get("camera.bitrate", 10000000)
            self.main_encoder = H264Encoder(bitrate=bitrate) # The main enocder that will be used for recording video
            self.lores_encoder = H264Encoder(bitrate=bitrate/10) # Lower bitrate for streaming

            self.logger.info(f"Camera configured successfully at {self.fps}fps")
            return True
            
        except Exception as e:
            self.logger.error(f"Error configuring camera: {e}")
            # Initialize encoders even if configuration fails
            bitrate = self.config.get("camera.bitrate", 10000000)
            self.main_encoder = H264Encoder(bitrate=bitrate)
            self.lores_encoder = H264Encoder(bitrate=bitrate/10)
            return False

    def _start_recording(self):
        """Implement camera-specific recording functionality"""
        self.logger.info("Executing camera specific recording functionality...")
        filename = f"{self.recording_folder}/{self.current_experiment_name}.{self.config.get('recording.recording_filetype')}"
        self.add_session_file(filename)
        try:
            # Start the camera if not already running
            if not self.picam2.started:
                self.picam2.start()
                time.sleep(0.1)  # Give camera time to start
            
            # Create file output
            self.file_output = PyavOutput(filename, format="mp4") # 7.2.4 in docs
            self.main_encoder.output = self.file_output # Binding an output to an encoders output is discussed in 9.3. in the docs - originally for using multiple outputs, but i have used it for single output
            
            # Start recording
            self.picam2.start_encoder(self.main_encoder, name="main") # 
            self.recording_start_time = time.time()
            self.frame_times = []  # Reset frame times

            # Send status response after successful recording start
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_started",
                    "filename": filename,
                    "recording": True,
                    "session_id": self.recording_session_id
                })
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error starting recording: {e}")
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_start_failed",
                    "error": str(e)
                })
            return False

    def _get_frame_timestamp(self, req):
        try:
            metadata = req.get_metadata()
            frame_wall_clock = metadata.get('FrameWallClock', 'No data')
            if frame_wall_clock != 'No data':
                self.frame_times.append(frame_wall_clock)
        except Exception as e:
            self.logger.error(f"Error capturing frame metadata: {e}")

    def _get_and_apply_frame_timestamp(self, req):
        try:
            metadata = req.get_metadata()
            frame_wall_clock = metadata.get('FrameWallClock', 'No data')
            if frame_wall_clock != 'No data':
                self.frame_times.append(frame_wall_clock)
                timestamp = time.strftime("%Y-%m-%d %X")

                # Apply mask to main stream
                with MappedArray(req, 'main') as m:
                    if self.config.get("camera.monochrome") is True:
                        # Convert BGR to grayscale
                        gray = cv2.cvtColor(m.array, cv2.COLOR_BGR2GRAY)
                        # Convert back to BGR for consistency with other processing
                        m.array[:] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

                with MappedArray(req, "lores") as m:
                    if self.config.get("camera.monochrome") is True:
                        # Convert BGR to grayscale
                        gray = cv2.cvtColor(m.array, cv2.COLOR_BGR2GRAY)
                        # Convert back to BGR for consistency with other processing
                        m.array[:] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                    cv2.putText(m.array, timestamp, (0, self.height - int(self.height * 0.01)), cv2.FONT_HERSHEY_SIMPLEX, self.config.get("camera.text_scale"), (50,255,50), self.config.get("camera.text_thickness")) # TODO: Make origin reference lores dimensions.
        except Exception as e:
            self.logger.error(f"Error capturing frame metadata: {e}")

    def _stop_recording(self):
        """Camera Specific implementation of stop recording"""
        try:
            self.logger.info("Attempting to stop camera specific recording")
            # Stop recording with camera-specific code
            self.picam2.stop_encoder(self.main_encoder)
            
            # Calculate duration
            if self.recording_start_time is not None:
                duration = time.time() - self.recording_start_time
                
                # Save timestamps with experiment name if available
                if hasattr(self, 'current_experiment_name') and self.current_experiment_name:
                    # Sanitize experiment name for filename (remove special characters)
                    safe_experiment_name = "".join(c for c in self.current_experiment_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                    safe_experiment_name = safe_experiment_name.replace(' ', '_')
                    timestamps_file = f"{self.recording_folder}/{self.current_experiment_name}_timestamps.txt"
                else:
                    timestamps_file = f"{self.recording_folder}/{self.recording_session_id}_timestamps.txt"
                
                self.add_session_file(timestamps_file)
                np.savetxt(timestamps_file, self.frame_times)
                
                # Send status response after successful recording stop
                if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                    self.communication.send_status({
                        "type": "recording_stopped",
                        "session_id": self.recording_session_id,
                        "duration": duration,
                        "frame_count": len(self.frame_times),
                        "status": "success",
                        "recording": False,
                        "message": f"Recording completed successfully with {len(self.frame_times)} frames"
                    })

                self.logger.info("Concluded camera stop_recording, waiting to exit")

            else:
                self.logger.error("Error: recording_start_time was None")
                if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                    self.communication.send_status({
                        "type": "recording_stopped",
                        "status": "error",
                        "error": "Recording start time was not set, so could not create timestamps."
                    })
                return False
        
        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_stopped",
                    "status": "error",
                    "error": str(e)
                })
            return False

    def start_streaming(self, receiver_ip=None, port=None) -> bool:
        """Start streaming video to the specified receiver using Flask to send MJPEG"""
        try:
            # Check if already streaming
            if self.is_streaming:
                self.logger.warning("Already streaming")
                return False

            # Always use port 8080 for Flask server
            port = 8080
            
            self.logger.info(f"Starting streaming from {self.network.ip}:{port}")

            # Start the camera if not already running
            if not self.picam2.started:
                self.picam2.start()
                time.sleep(0.1)  # Give camera time to start
            
            # Reset streaming state
            self.should_stop_streaming = False
            
            # Start the thread with the correct port
            self.streaming_server_thread = threading.Thread(target=self.run_streaming_server, args=(port,))
            self.streaming_server_thread.daemon = True
            self.streaming_server_thread.start()

            # Set flag to true
            self.is_streaming = True
            
            # Send streaming status
            self.communication.send_status({
                'type': 'streaming_started',
                'port': port,
                'status': 'success',
                'message': f'Streaming started from {self.network.ip}:{port}'
            })
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error starting streaming: {str(e)}")
            self.communication.send_status({
                'type': 'streaming_start_failed',
                'status': 'error',
                'error': f"Failed to start streaming: {str(e)}"
            })
            return False

    def run_streaming_server(self, port=8080):
        """Run the flask server to stream upon"""
        try:
            from werkzeug.serving import make_server
            self.streaming_server = make_server('0.0.0.0', port, self.streaming_app)
            self.logger.info(f"Starting Flask server on port {port}")
            self.streaming_server.serve_forever()
        except Exception as e:
            self.logger.error(f"Error running streaming server: {e}")
            self.is_streaming = False
            self.streaming_server = None

    def generate_streaming_frames(self):
        """Generate streaming frames for MJPEG stream"""
        import time
        self.logger.info("Starting to generate streaming frames")

        while not self.should_stop_streaming:
            try:
                self.logger.debug("Capturing frame...")
                # Add a timeout for capture_array if possible, or break after N seconds
                start_time = time.time()
                frame = None
                while frame is None and (time.time() - start_time) < 2.0:
                    try:
                        frame = self.picam2.capture_array("lores")
                    except Exception as e:
                        self.logger.error(f"Error capturing frame: {e}")
                        time.sleep(0.1)
                if frame is None:
                    self.logger.error("Timeout waiting for frame")
                    break
                ret, jpeg = cv2.imencode('.jpg', frame)
                if not ret:
                    self.logger.warning("JPEG encoding failed")
                    continue
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            except Exception as e:
                self.logger.error(f"Error generating streaming frame: {e}")
                time.sleep(0.1)
        self.logger.info("Stopped generating streaming frames")

    def register_routes(self):
        """Register Flask routes"""
        @self.streaming_app.route('/')
        def index():
            return "Camera Streaming Server"
            
        @self.streaming_app.route('/video_feed')
        def video_feed():
            return Response(self.generate_streaming_frames(),
                          mimetype='multipart/x-mixed-replace; boundary=frame')

                          
        @self.streaming_app.route('/shutdown')
        def shutdown():
            func = request.environ.get('werkzeug.server.shutdown')
            if func is None:
                raise RuntimeError('Not running with the Werkzeug Server')
            func()
            return 'Server shutting down...'

    def stop_streaming(self) -> bool:
        """Stop streaming video"""
        try:
            if not self.is_streaming:
                self.logger.warning("Not currently streaming")
                return False
            
            # Set flag to stop frame generation
            self.should_stop_streaming = True
            
            # Stop the Flask server if it's running
            if self.streaming_server:
                self.streaming_server.shutdown()
                self.streaming_server = None
            
            # Stop the thread
            if self.streaming_server_thread and self.streaming_server_thread.is_alive():
                self.streaming_server_thread.join(timeout=1.0)
            
            # Force kill any remaining Flask processes
            import os
            try:
                os.system("pkill -f 'python.*flask'")
            except:
                pass
            
            self.is_streaming = False
            
            self.communication.send_status({
                "type": "streaming_stopped",
                "status": "success",
                "message": "Streaming stopped successfully"
            })
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping stream: {e}")
            self.communication.send_status({
                "type": "streaming_stopped",
                "status": "error",
                "error": f"Failed to stop streaming: {str(e)}"
            })
            return False
    
    def when_controller_discovered(self, controller_ip: str, controller_port: int):
        super().when_controller_discovered(controller_ip, controller_port)

    def start(self) -> bool:
        """Start the camera module - including streaming"""
        try:
            # Start the parent module first
            if not super().start():
                return False

            # Start streaming
            # TODO: add check for config parameter stream_on_start?
            self.start_streaming()

            return True

        except Exception as e:
            self.logger.error(f"Error starting module: {e}")
            return False

    def stop(self) -> bool:
        """Stop the module and cleanup"""
        try:
            # Stop streaming if active
            if self.is_streaming:
                self.stop_streaming()
                
            # Call parent stop
            return super().stop()
            
        except Exception as e:
            self.logger.error(f"Error stopping module: {e}")
            return False

    """APA Camera Methods - Mask, Shock Zone, Neural Net"""
    def _configure_mask_and_shock_zone(self):
        """Reconfigure mask and shock zone settings after config update"""
        try:
            # Update mask configuration
            self.mask_radius = self.config.get("mask.mask_radius")
            self.mask_center_x = None  # Will be set to image center
            self.mask_center_y = None  # Will be set to image center
            self.mask_center_x_offset = self.config.get("mask.mask_center_x_offset")
            if self.mask_center_x_offset is None:
                self.mask_center_x_offset = 0
            self.mask_center_y_offset = self.config.get("mask.mask_center_y_offset")
            if self.mask_center_y_offset is None:
                self.mask_center_y_offset = 0
            self.mask_enabled = self.config.get("mask.mask_enabled")
            
            # Update shock zone configuration
            self.shock_zone_enabled = self.config.get("shock_zone.shock_zone_enabled")
            self.shock_zone_display = self.config.get("shock_zone.shock_zone_display")
            self.shock_zone_angle_span = self.config.get("shock_zone.shock_zone_angle_span_deg")
            self.shock_zone_start_angle = self.config.get("shock_zone.shock_zone_start_angle_deg") - 90
            self.shock_zone_inner_offset = self.config.get("shock_zone.shock_zone_inner_offset")
            self.shock_zone_color = self.config.get("shock_zone.shock_zone_color")
            if isinstance(self.shock_zone_color, dict): 
                self.shock_zone_color = list(self.config.get("shock_zone.shock_zone_color").values())
            self.logger.info(f"Shock zone color: {self.shock_zone_color}")
            self.shock_zone_thickness = self.config.get("shock_zone.shock_zone_line_thickness")
            
            self.logger.info("Mask and shock zone configuration updated")
        except Exception as e:
            self.logger.error(f"Error updating mask and shock zone configuration: {e}")

    def _apply_mask(self, m: MappedArray) -> None:
        """
        Apply (circular) mask to stream
        
        Args:
            m: the image frame to apply the mask to 

        Returns:

        """
        # Get image dimensions
        image_shape = m.array.shape[:2]
    
        # Set mask center to image center if not specified
        if self.mask_center_x is None:
            x_offset = self.mask_center_x_offset if self.mask_center_x_offset is not None else 0
            self.mask_center_x = int(image_shape[1]/2) + x_offset
        if self.mask_center_y is None:
            y_offset = self.mask_center_y_offset if self.mask_center_y_offset is not None else 0
            self.mask_center_y = int(image_shape[0]/2) + y_offset
        
        # Step 1: Apply circular mask if enabled
        if self.mask_enabled and self.mask_radius is not None:
            # Calculate radius with safety checks
            calculated_radius = int(0.5 * self.mask_radius * image_shape[1])
            if calculated_radius > 0:  # Only apply mask if radius is valid
                # Create a circular mask (white circle on black background)
                mask = np.zeros(image_shape, dtype="uint8")
                cv2.circle(mask, center=(self.mask_center_x, self.mask_center_y), 
                            radius=calculated_radius, color=255, thickness=-1)
                
                # Apply mask to original image to show only content within the circle
                masked_image = cv2.bitwise_and(m.array, m.array, mask=mask)
                
                # Replace the original image with the masked version
                m.array[:] = masked_image

    def _apply_grayscale(self, m: MappedArray) -> None:
        """
        Convert an image to grayscale.

        Args:
            m: The image to apply the filter to.
        """
        # Convert BGR to grayscale
        gray = cv2.cvtColor(m.array, cv2.COLOR_BGR2GRAY)
        # Convert back to BGR for consistency with other processing
        m.array[:] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    
    def _apply_timestamp(self, m: MappedArray) -> None:
        """
        Apply timestmap to image.

        Args:
            m: The image to overlay the timestamp on.
        """
        timestamp = time.strftime("%Y-%m-%d %X")
        cv2.putText(m.array, timestamp, (0, self.height - int(self.height * 0.01)), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (50, 255, 50), 2)

    
    def _apply_shock_zone(self, m: MappedArray) -> None:
        """
        Overlay shock zone on image.

        Args:
            m: The image to overlay the shock zone on.
        """
        # Get image dimensions
        image_shape = m.array.shape[:2]

        if self.shock_zone_display and self.mask_radius is not None:
            # Shock zone parameters with safety checks
            self.outer_radius = int(0.5 * self.mask_radius * image_shape[1])
            if self.outer_radius <= 0:
                return  # Skip shock zone if radius is invalid
                
            self.inner_offset = int(self.shock_zone_inner_offset * self.outer_radius)
            if self.inner_offset < 0:
                self.inner_offset = 0
            
            # Calculate angles (in degrees)
            # Start angle should be middle 
            self.start_angle = self.shock_zone_start_angle - (self.shock_zone_angle_span * 0.5) 
            self.end_angle = self.start_angle + self.shock_zone_angle_span
            
            # Convert angles to radians for calculations
            start_rad = np.radians(self.start_angle)
            end_rad = np.radians(self.end_angle)
            
            # Calculate points for the outer arc with bounds checking
            start_x = int(self.mask_center_x + self.outer_radius * np.cos(start_rad))
            start_y = int(self.mask_center_y + self.outer_radius * np.sin(start_rad))
            end_x = int(self.mask_center_x + self.outer_radius * np.cos(end_rad))
            end_y = int(self.mask_center_y + self.outer_radius * np.sin(end_rad))
            
            # Calculate points for the inner arc with bounds checking
            inner_start_x = int(self.mask_center_x + self.inner_offset * np.cos(start_rad))
            inner_start_y = int(self.mask_center_y + self.inner_offset * np.sin(start_rad))
            inner_end_x = int(self.mask_center_x + self.inner_offset * np.cos(end_rad))
            inner_end_y = int(self.mask_center_y + self.inner_offset * np.sin(end_rad))
            
            # Draw the shock zone shape
            color = self.shock_zone_color
            thickness = max(1, self.shock_zone_thickness)  # Ensure thickness is at least 1
            
            # 1. Draw the outer arc
            cv2.ellipse(m.array, 
                        center=(self.mask_center_x, self.mask_center_y),
                        axes=(self.outer_radius, self.outer_radius),
                        angle=0,
                        startAngle=self.start_angle,
                        endAngle=self.end_angle,
                        color=color,
                        thickness=thickness)
            
            # 2. Draw the inner arc
            cv2.ellipse(m.array,
                        center=(self.mask_center_x, self.mask_center_y),
                        axes=(self.inner_offset, self.inner_offset),
                        angle=0,
                        startAngle=self.start_angle,
                        endAngle=self.end_angle,
                        color=color,
                        thickness=thickness)
            
            # 3. Draw the two diagonal connecting lines
            cv2.line(m.array, 
                    pt1=(start_x, start_y), 
                    pt2=(inner_start_x, inner_start_y), 
                    color=color, 
                    thickness=thickness)
            
            cv2.line(m.array, 
                    pt1=(end_x, end_y), 
                    pt2=(inner_end_x, inner_end_y), 
                    color=color, 
                    thickness=thickness)
        
    @lru_cache
    def _get_labels(self):
        """
        Get labels of detected objects.
        
        Returns:
            labels: List of labels for loaded neural net.
        """
        labels = self.intrinsics.labels
        if self.intrinsics.ignore_dash_labels:
            labels = [label for label in labels if label and label != "-"]
        return labels
 

    def _draw_detections(self, m: MappedArray) -> None:
        """Draw a single smoothed detection to reduce flicker."""
        if self._last_known_det is None:
            return  # nothing ever detected

        # Take the last non-None detection from buffer
        for det in reversed(self._detection_buffer):
            if det is not None:
                stable_det = det
                break
        else:
            stable_det = None

        if stable_det is None:
            return

        # det = stable_det
        det = self._last_known_det
        x, y, w, h = det.box
        cx = int(x + w / 2)
        cy = int(y + h / 2)

        # Simple smoothing using last frame
        alpha = 0.5
        if self.last_cx is None: self.last_cx = cx
        if self.last_cy is None: self.last_cy = cy
        if self.config.get("object_detection.coordinate_smoothing"):
            cx = int(alpha * cx + (1 - alpha) * self.last_cx)
            cy = int(alpha * cy + (1 - alpha) * self.last_cy)
        self.last_cx = cx
        self.last_cy = cy

        # Draw center dot
        center_in_zone = self._is_in_shock_zone(cx, cy)
        color = (0, 0, 255) if center_in_zone else (0, 255, 0)
        cv2.circle(m.array, (cx, cy), 5, color, -1)

        # Shock zone warning text
        if center_in_zone:
            cv2.putText(
                m.array,
                "OBJECT IN SHOCK ZONE",
                (50, 100),
                cv2.FONT_HERSHEY_SIMPLEX,
                2.0,
                (0, 0, 255),
                4,
                cv2.LINE_AA
            )

        # Draw label
        labels = self._get_labels()
        label = f"{labels[int(det.category)]} ({det.conf:.2f})"
        cv2.putText(m.array, label, (cx + 10, cy - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
    
    def _parse_detections(self, metadata: dict):
        """Parse the output tensor into a number of detected objects, scaled to the ISP output."""
        last_detections = []
        bbox_normalization = self.intrinsics.bbox_normalization
        bbox_order = self.intrinsics.bbox_order
        np_outputs = self.imx500.get_outputs(metadata, add_batch=True)

        input_w, input_h = self.imx500.get_input_size()
        if np_outputs is None:
            return last_detections
        if self.intrinsics.postprocess == "nanodet":
            boxes, scores, classes = \
                postprocess_nanodet_detection(outputs=np_outputs[0], conf=self.threshold, iou_thres=self.iou,
                                              max_out_dets=self.max_detections)[0]
            from picamera2.devices.imx500.postprocess import scale_boxes
            boxes = scale_boxes(boxes, 1, 1, input_h, input_w, False, False)
        else:
            boxes, scores, classes = np_outputs[0][0], np_outputs[1][0], np_outputs[2][0]
            if bbox_normalization:
                boxes = boxes / input_h

            if bbox_order == "xy":
                boxes = boxes[:, [1, 0, 3, 2]]
            boxes = np.array_split(boxes, 4, axis=1)
            boxes = zip(*boxes)

        last_detections = [
            Detection(category, score, self.imx500.convert_inference_coords(box, metadata, self.picam2))
            for box, score, category in zip(boxes, scores, classes)
            if score > self.threshold
        ]
        return last_detections

    def _is_in_shock_zone(self, cx, cy) -> bool:
        """
        Args:

        """
        if self.inner_offset is None or self.outer_radius is None:
            return False  # skip shock zone check until initialized
        # Compute distance from center
        dx = cx - self.mask_center_x
        dy = cy - self.mask_center_y
        r = np.hypot(dx, dy)
        
        # Compute angle (convert to degrees, ensure [0,360))
        theta = (np.degrees(np.arctan2(dy, dx)) + 360) % 360

        # Check radial and angular bounds
        within_radius = self.inner_offset <= r <= self.outer_radius
        if self.start_angle < self.end_angle:
            within_angle = self.start_angle <= theta <= self.end_angle
        else:
            # Handles wrap-around (e.g., start=350°, end=10°)
            within_angle = theta >= self.start_angle or theta <= self.end_angle

        return within_radius and within_angle

    def _frame_precallback(self, req):
        """Combined callback that applies mask, shock zone overlay, timestamps, and grayscale conversion"""
        try:
            # First, capture frame metadata for timestamps
            metadata = req.get_metadata()
            frame_wall_clock = metadata.get('FrameWallClock', 'No data')
            if frame_wall_clock != 'No data':
                self.frame_times.append(frame_wall_clock)

            # Detect objects
            if self.config.get("object_detection.enabled"):
                try:
                    current_detections = self._parse_detections(metadata)
                except Exception as e:
                    self.logger.error(f"Error executing _parse_detections: {e}")
                    current_detections = []

                # Pick the detection with highest confidence
                if current_detections:
                    labels = self._get_labels()
                    filtered_detections = [
                        det for det in current_detections
                        if labels[int(det.category)] == "person"
                    ]
                    if filtered_detections:
                        best_det = max(filtered_detections, key=lambda d: d.conf)
                        self._last_known_det = best_det
                    self._detection_buffer.append(best_det)
                else:
                    self._detection_buffer.append(None)  # no detection this frame

            # Apply mask to main stream
            with MappedArray(req, 'main') as m:
                if self.config.get("camera.monochrome") is True:
                    self._apply_grayscale(m)

                self._apply_mask(m)

                if self.config.get("object_detection.enabled"):
                    self._draw_detections(m)
            
            # Apply mask and shock zone to lores stream
            with MappedArray(req, 'lores') as m:
                # Step 0: Convert to grayscale if monochrome is enabled
                if self.config.get("camera.monochrome") is True:
                    self._apply_grayscale(m)

                # Step 1: Apply circular mask if enabled
                self._apply_mask(m)
                
                # Step 2: Apply shock zone overlay if enabled
                self._apply_shock_zone(m)

                # Add timestamp
                self._apply_timestamp(m)

                # Detect objects
                if self.config.get("object_detection.enabled"):
                    self._draw_detections(m)
                
        except Exception as e:
            # Log the error but don't crash the stream
            self.logger.error(f"Error in _frame_precallback: {e}")
            # Continue without applying mask/shock zone for this frame

def main():
    camera = APACameraModule()
    camera.start()

    # Keep running until interrupted
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        camera.stop()

if __name__ == '__main__':
    main()

