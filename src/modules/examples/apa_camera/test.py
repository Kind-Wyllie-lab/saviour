"""
Program for a specialised camera module that will be used with the APA test rig.


"""

import logging
import sys
import os
import time
import threading
from picamera2 import Picamera2, MappedArray
from picamera2.encoders import H264Encoder
from picamera2.outputs import PyavOutput, FfmpegOutput
from flask import Flask, Response, request
import cv2
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from habitat.src.modules.camera_module import CameraModule

class APACamera(CameraModule):
    def __init__(self, module_type="apa_camera", config_file_path="apa_camera_config.json"):
        super().__init__(module_type=module_type, config_file_path=config_file_path)

        # Override the set_config callback to persist by default
        # TODO: Apply this to base class?
        self.command.callbacks['set_config'] = lambda new_config: self.set_config(new_config, persist=True)

        # Override streaming status callback to handle restart state
        self.command.callbacks['get_streaming_status'] = lambda: self.is_streaming and not self._restarting_stream

        # Override health manager streaming status callback as well
        self.health.callbacks['get_streaming_status'] = lambda: self.is_streaming and not self._restarting_stream

        # Add flag to track stream restart state
        self._restarting_stream = False

        # Fix file format path for APA camera
        self.recording_filetype = self.config.get("editable.camera.file_format", "h264")

        self.configure_mask_and_shock_zone()
    
    def stop_recording(self) -> bool:
        """Override stop_recording to use the new export method"""
        try:
            # Call parent's stop_recording first to handle the recording logic
            success = super().stop_recording()
            
            if success and self.config.get("auto_export", True) and self.current_filename:
                self.logger.info("Auto-export enabled, exporting recording using export manager")
                try:
                    # Use the export manager's method for consistency
                    if self.export.export_current_session_files(
                        recording_folder=self.recording_folder,
                        recording_session_id=self.recording_session_id,
                        experiment_name=self.current_experiment_name
                    ):
                        self.logger.info("Auto-export completed successfully")
                    else:
                        self.logger.warning("Auto-export failed, but recording was successful")
                except Exception as e:
                    self.logger.error(f"Auto-export error: {e}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error in stop_recording: {e}")
            return False
    
    def configure_camera(self):
        try:
            self.mode = self.camera_modes[0]
            self.fps = self.config.get("camera.fps")
            self.width = self.config.get("camera.width")
            self.height = self.config.get("camera.height")
            sensor = {"output_size": self.mode["size"], "bit_depth": self.mode["bit_depth"]} # Specify correct camera mode for our application
            main = {"size": (self.width, self.height), "format": "RGB888"} # Main stream
            lores = {"size": (self.width, self.height), "format":"RGB888"} # A lores stream for network streaming. RGB888 requires less processing.
            controls = {"FrameRate": self.fps}

            if self.config.get("camera.monochrome") is True:
                self.logger.info("Camera configured for grayscale - applying grayscale conversion in pre-callback.")

            # Create video configuration with explicit framerate
            config = self.picam2.create_video_configuration(main=main,
                        lores=lores,
                        sensor=sensor,
                        controls=controls,
                        buffer_count=16) # Buffer size of 16 increases potential framerate.

            # Apply configuration
            self.picam2.configure(config)

            # Apply combined callback for mask, shock zone, and timestamps
            self.picam2.pre_callback = self._apply_mask_shock_zone_and_timestamp

            # Create encoders with current settings
            bitrate = 10000000
            self.main_encoder = H264Encoder(bitrate=bitrate) # The main enocder that will be used for recording video
            self.lores_encoder = H264Encoder(bitrate=bitrate/10) # Lower bitrate for streaming

            return True

        except Exception as e:
            self.logger.error(f"Error configuring camera: {e}")
            # Initialize encoders even if configuration fails
            bitrate = 10000000
            self.main_encoder = H264Encoder(bitrate=bitrate)
            self.lores_encoder = H264Encoder(bitrate=bitrate/10)
            return False
    
    def set_config(self, new_config: dict, persist: bool = False):
        # Check if camera settings are being changed
        camera_config_changed = False
        if 'editable' in new_config and 'camera' in new_config['editable']:
            current_camera_config = self.config.get("camera")
            new_camera_config = new_config['editable']['camera']
            
            # Check if any camera settings are different
            for key in ['fps', 'width', 'height', 'file_format']:
                if key in new_camera_config and current_camera_config.get(key) != new_camera_config[key]:
                    camera_config_changed = True
                    break
        
        # Apply the config first
        success = super().set_config(new_config, persist)
        if success:
            # Always update mask and shock zone settings
            self.configure_mask_and_shock_zone()
            
            # If camera settings changed and we're streaming, restart the stream
            # TODO: Should this also restart recording? Not really, as we never want to change settings while recording, right? So maybe prevent this happening.
            if camera_config_changed and self.is_streaming:
                self.logger.info("Camera settings changed, restarting stream to apply new configuration")
                
                # Set restart flag to prevent incorrect status reports
                self._restarting_stream = True
                
                # Stop streaming
                self.stop_streaming()
                
                # Wait a moment for the stream to fully stop
                time.sleep(0.5)
                
                # Configure camera with new settings
                try:
                    self.configure_camera()
                    self.logger.info("Camera reconfigured successfully")
                except Exception as e:
                    self.logger.error(f"Error reconfiguring camera: {e}")
                
                # Restart streaming
                try:
                    self.start_streaming()
                    self.logger.info("Streaming restarted with new camera settings")
                except Exception as e:
                    self.logger.error(f"Error restarting streaming: {e}")
                
                # Clear restart flag after restart is complete
                self._restarting_stream = False
            elif camera_config_changed and not self.is_streaming:
                # Camera settings changed but we're not streaming, just reconfigure
                try:
                    self.configure_camera()
                    self.logger.info("Camera reconfigured successfully (not streaming)")
                except Exception as e:
                    self.logger.error(f"Error reconfiguring camera: {e}")
        
        return success
    
    def configure_mask_and_shock_zone(self):
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
            self.shock_zone_thickness = self.config.get("shock_zone.shock_zone_line_thickness")
            
            self.logger.info("Mask and shock zone configuration updated")
        except Exception as e:
            self.logger.error(f"Error updating mask and shock zone configuration: {e}")
    
    def _apply_mask_shock_zone_and_timestamp(self, req):
        """Combined callback that applies mask, shock zone overlay, timestamps, and grayscale conversion"""
        try:
            # First, capture frame metadata for timestamps
            metadata = req.get_metadata()
            frame_wall_clock = metadata.get('FrameWallClock', 'No data')
            if frame_wall_clock != 'No data':
                self.frame_times.append(frame_wall_clock)

            # Apply mask to main stream
            with MappedArray(req, 'main') as m:
                if self.config.get("camera.monochrome") is True:
                    # Convert BGR to grayscale
                    gray = cv2.cvtColor(m.array, cv2.COLOR_BGR2GRAY)
                    # Convert back to BGR for consistency with other processing
                    m.array[:] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                
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
            
            # Apply mask and shock zone to lores stream
            with MappedArray(req, 'lores') as m:
                # Step 0: Convert to grayscale if monochrome is enabled
                if self.config.get("camera.monochrome") is True:
                    # Convert BGR to grayscale
                    gray = cv2.cvtColor(m.array, cv2.COLOR_BGR2GRAY)
                    # Convert back to BGR for consistency with other processing
                    m.array[:] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
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
                
                # Step 2: Apply shock zone overlay if enabled
                if self.shock_zone_display and self.mask_radius is not None:
                    # Shock zone parameters with safety checks
                    outer_radius = int(0.5 * self.mask_radius * image_shape[1])
                    if outer_radius <= 0:
                        return  # Skip shock zone if radius is invalid
                        
                    inner_offset = int(self.shock_zone_inner_offset * outer_radius)
                    if inner_offset < 0:
                        inner_offset = 0
                    
                    # Calculate angles (in degrees)
                    # Start angle should be middle 
                    start_angle = self.shock_zone_start_angle - (self.shock_zone_angle_span * 0.5) 
                    end_angle = start_angle + self.shock_zone_angle_span
                    
                    # Convert angles to radians for calculations
                    start_rad = np.radians(start_angle)
                    end_rad = np.radians(end_angle)
                    
                    # Calculate points for the outer arc with bounds checking
                    start_x = int(self.mask_center_x + outer_radius * np.cos(start_rad))
                    start_y = int(self.mask_center_y + outer_radius * np.sin(start_rad))
                    end_x = int(self.mask_center_x + outer_radius * np.cos(end_rad))
                    end_y = int(self.mask_center_y + outer_radius * np.sin(end_rad))
                    
                    # Calculate points for the inner arc with bounds checking
                    inner_start_x = int(self.mask_center_x + inner_offset * np.cos(start_rad))
                    inner_start_y = int(self.mask_center_y + inner_offset * np.sin(start_rad))
                    inner_end_x = int(self.mask_center_x + inner_offset * np.cos(end_rad))
                    inner_end_y = int(self.mask_center_y + inner_offset * np.sin(end_rad))
                    
                    # Draw the shock zone shape
                    color = self.shock_zone_color
                    thickness = max(1, self.shock_zone_thickness)  # Ensure thickness is at least 1
                    
                    # 1. Draw the outer arc
                    cv2.ellipse(m.array, 
                               center=(self.mask_center_x, self.mask_center_y),
                               axes=(outer_radius, outer_radius),
                               angle=0,
                               startAngle=start_angle,
                               endAngle=end_angle,
                               color=color,
                               thickness=thickness)
                    
                    # 2. Draw the inner arc
                    cv2.ellipse(m.array,
                               center=(self.mask_center_x, self.mask_center_y),
                               axes=(inner_offset, inner_offset),
                               angle=0,
                               startAngle=start_angle,
                               endAngle=end_angle,
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

                # Add timestamp to lores stream
                timestamp = time.strftime("%Y-%m-%d %X")
                cv2.putText(m.array, timestamp, (0, self.height - int(self.height * 0.01)), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (50, 255, 50), 2)
            
                
        except Exception as e:
            # Log the error but don't crash the stream
            self.logger.error(f"Error in _apply_mask_shock_zone_and_timestamp: {e}")
            # Continue without applying mask/shock zone for this frame
                    
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


if __name__ == "__main__":
    apa_camera = APACamera(config_file_path = "apa_camera_config.json")
    apa_camera.start()
    # Keep running until interrupted
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        apa_camera.stop()