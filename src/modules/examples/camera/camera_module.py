#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - Camera Module Class

This class extends the base Module class to handle camera-specific functionality.

Picamera2 is used for interfacing camera hardware. This is a python wrapper for libcamera / rpicam.

For a good discussion of getting high framerates (via correct sensor mode), read this thread: https://github.com/raspberrypi/picamera2/discussions/111#discussioncomment-13518732
For a good discussion of getting frame timestamps and syncing with precallbacks, read this thread: https://forums.raspberrypi.com/viewtopic.php?t=377442

Author: Andrew SG
Created: 17/03/2025

# TODO: Consider using http.server instead of flask
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
from picamera2.outputs import PyavOutput, FfmpegOutput, SplittableOutput
import json
from flask import Flask, Response, request
import cv2
from typing import Any, Optional
import subprocess

# Import SAVIOUR dependencies
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command, check

class CameraModule(Module):
    def __init__(self, module_type="camera"):        
        # Call the parent class constructor
        super().__init__(module_type)

        # Update config 
        self.config.load_module_config("camera_config.json")
    
        # Initialize camera
        self.picam2 = Picamera2()
        self.height = None
        self.width = None
        self.fps = None
        self.mode = None

        # Get camera modes
        self.camera_modes = self.picam2.sensor_modes
        time.sleep(0.1)
    
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
        # self.frame_times = []  # For storing frame timestamps

        # Set up camera-specific callbacks for the command handler
        self.camera_commands = {
            'start_streaming': self.start_streaming,
            'stop_streaming': self.stop_streaming
        }
        self.command.set_commands(self.camera_commands) # Append new camera callbacks
        self.logger.info(f"Command handler callbacks: {self.command.commands}")

        # Segment based recording
        self.monitor_recording_segments_stop_flag = threading.Event()
        self.monitor_recording_segments_thread = None 
        self.segment_id = 0
        self.segment_start_time = None
        self.segment_files = []


        self.current_video_segment = None
        self.last_video_segment = None


        self.to_export = [] # Files to be exported

        self.module_checks = {
            self._check_picam
        }


    """Self Check"""
    def _perform_module_specific_checks(self) -> tuple[bool, str]:
        self.logger.info(f"Performing {self.module_type} specific checks")
        for check in self.module_checks:
            self.logger.info(f"Running {check.__name__}")
            result, message = check()
            if result == False:
                self.logger.info(f"A check failed: {check.__name__}, {message}")
                return False, message
                break # Exit loop on first failed check
        if result == False:
            return result, message
        else:
            return True, "No implementation yet..."


    @check()
    def _check_picam(self) -> tuple[bool, str]:
        if not self.picam2:
            return False, "No picam2 object"
        else:
            return True, "Picam2 object instantiated"


    def configure_module(self, updated_keys: Optional[list[str]]):
        """Override parent method configure module in event that module config changes"""
        if self.is_streaming:
            self.logger.info("Camera settings changed, restarting stream to apply new configuration")
            # Configure anything that doesn't require stream to restart
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
            self.picam2.pre_callback = self._get_and_apply_frame_timestamp
            
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


    """Segment Oriented Recording (to manage long recordings)"""
    def _create_new_recording_segment(self):
        """Create new video segment and corresponding timestamp."""
        self.segment_id += 1
        self.segment_start_time = time.time()
        self._start_new_video_segment() # Start new video segment
        self._export_staged() # Export files that have been marked for export


    def _create_initial_recording_segment(self) -> None:
        self.segment_id = 0
        self.segment_start_time = time.time()

        # Start video
        filename = self._get_video_filename() # should look like rec/wistar_103045_20250526_(1)_110045_20250526.mp4
        self.current_video_segment = filename
        self.add_session_file(filename)

        # Start the camera 
        if not self.picam2.started:
            self.picam2.start()
            time.sleep(0.1)  # Give camera time to start
        
        # Create file output
        self.file_output = SplittableOutput(PyavOutput(filename, format="mp4")) # 7.2.4 and 7.2.6 in docs
        self.main_encoder.output = self.file_output # Binding an output to an encoders output is discussed in 9.3. in the docs - originally for using multiple outputs, but i have used it for single output
        
        # Start recording
        self.picam2.start_encoder(self.main_encoder, name="main") # 
        self.recording_start_time = time.time()


    def _get_video_filename(self) -> str:
        """Shorthand way to create a filename"""
        filename = f"{self.current_filename_prefix}_({self.segment_id})_({time.strftime('%Y%m%d_%H%M%S')}).{self.config.get('recording.recording_filetype')}" # Consider adding segment start time 
        return filename


    def _start_new_video_segment(self):
        """
        Start recording a new splittable output video segment. 
        """
        # Stage current recording for export
        self.last_video_segment = self.current_video_segment
        self.to_export.append(self.last_video_segment)

        # Create new segment name
        filename = self._get_video_filename() # should look like rec/wistar_103045_20250526_(1)_110045_20250526.mp4
        self.current_video_segment = filename
        self.add_session_file(filename)

        # Start recording to new segment
        self.file_output.split_output(PyavOutput(filename, format="mp4"))
        self.logger.info(f"Switched to new segment {filename}")
        if not self._check_file_exists(filename):
            self.logger.warning(f"{filename} does not exist in recording folder!")

        # Reset positioning timestamps on recorded video prior to exporting it
        self._fix_positioning_timestamps(self.last_video_segment)


    def _fix_positioning_timestamps(self, filename: str) -> None:
        """Take an mp4 file produced by picamera2 SplittableOutput and reset positioning timestamps"""
        tmp_filename = f"{filename[:-4]}_formatted.mp4"
        subprocess.run([
            "ffmpeg",
            "-i", filename,
            "-map", "0",
            "-c", "copy",
            "-reset_timestamps", "1",
            tmp_filename
        ], check=True)
        os.replace(tmp_filename, filename) 

    """Segment Export"""
    def _export_staged(self):
        """Exports all files in the to_export list"""
        try:
            # Use the export manager's method for consistency
            if self.export.export_current_session_files(
                session_files=self.to_export,
                recording_folder=self.api.get_recording_folder(),
                recording_session_id=self.recording_session_id,
                experiment_name=self.current_experiment_name
            ):
                self.logger.info("Auto-export completed successfully")

                if self.config.get("delete_on_export", True):
                    self._clear_recordings(filenames=self.to_export)
                    self._clear_exported_files_from_session_files()
                    self.to_export = [] # empty the list of files to export
            else:
                self.logger.warning("Auto-export failed, but recording was successful")
        except Exception as e:
            self.logger.error(f"Auto-export error: {e}")

    
    def _clear_exported_files_from_session_files(self):
        for file in self.to_export:
            if file in self.session_files:
                self.session_files.pop(self.session_files.index(file))


    """Recording"""
    def _start_recording(self):
        """Implement camera-specific recording functionality"""
        self.logger.info("Executing camera specific recording functionality...")

        # New approach
        try:
            self.to_export = []
            self._create_initial_recording_segment()
            self._start_recording_segment_monitoring()
            # Send status response after successful recording start
            if hasattr(self, 'communication') and self.communication and self.communication.controller_ip:
                self.communication.send_status({
                    "type": "recording_started",
                    "filename": self.current_video_segment,
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


    def _stop_recording_video(self):
        """Stop recording current segment"""
        self.picam2.stop_encoder(self.main_encoder)
        self.last_video_segment = self.current_video_segment


    def _stop_recording(self) -> bool:
        """Camera Specific implementation of stop recording"""
        try:
            self.logger.info("Attempting to stop camera specific recording")

            self._stop_recording_video()
            
            # Stop recording and tidy up session files
            self._stop_recording_segment_monitoring()

            # Preprocess video file for export
            for file in self.session_files:
                if file.endswith(".mp4"):
                    self.logger.info(f"Fixing positioning timestamps for {file}")
                    self._fix_positioning_timestamps(file)

            return True
        
        except Exception as e:
            self.logger.error(f"Error stopping recording: {e}")
            return False


    def _monitor_recording_length(self):
        """
        Runs in a thread and monitors length of current recording.
        If it exceeds segment length limit, stops and starts a new recording.
        """
        segment_length = self.config.get("recording.segment_length_seconds", 30) # Default to 30 for debug for now 050126

        while not self.monitor_recording_segments_stop_flag.is_set():
            if (time.time() - self.segment_start_time > segment_length):
                self._create_new_recording_segment()
                self.logger.info(f"Segment duration elapsed - new segment {self.segment_id} started at {self.segment_start_time}")
            time.sleep(0.1) # Avoid busy waiting
            
                

    def _start_recording_segment_monitoring(self):
        self.monitor_recording_segments_stop_flag.clear()
        self.segment_start_time = self.recording_start_time 
        self.segment_id = 0
        self.monitor_recording_segments_thread = threading.Thread(target=self._monitor_recording_length, daemon=True)
        self.monitor_recording_segments_thread.start()


    def _stop_recording_segment_monitoring(self): 
        self.monitor_recording_segments_stop_flag.set()
        self.monitor_recording_segments_thread.join(timeout=5)


    """Timestamping frames"""
    def _get_frame_timestamp(self, req) -> bool:
        try:
            metadata = req.get_metadata()
            frame_wall_clock = metadata.get('FrameWallClock', 'No data')
            if frame_wall_clock != 'No data':
                return frame_wall_clock
            else:
                return False
        except Exception as e:
            self.logger.error(f"Error capturing frame metadata: {e}")


    def _get_and_apply_frame_timestamp(self, req) -> None:
        try:
            # Get and format timestamp
            timestamp = self._get_frame_timestamp(req)
            if not timestamp:
                self.logger.warning("No data returned by frame wall clock")
                return
            timestamp = str(datetime.datetime.fromtimestamp(timestamp / 1e9, tz=datetime.timezone.utc)) # Format timestamp. Example: 2026-01-08 15:25:01.125786+00:00

            # Modify main stream - used for recording.
            with MappedArray(req, 'main') as m:
                if self.config.get("camera.monochrome") is True:
                    # Convert BGR to grayscale
                    gray = cv2.cvtColor(m.array, cv2.COLOR_BGR2GRAY)
                    # Convert back to BGR for consistency with other processing
                    m.array[:] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                self._apply_timestamp(m, timestamp)

            # Modify lores stream - used for streaming.
            with MappedArray(req, "lores") as m:
                if self.config.get("camera.monochrome") is True:
                    # Convert BGR to grayscale
                    gray = cv2.cvtColor(m.array, cv2.COLOR_BGR2GRAY)
                    # Convert back to BGR for consistency with other processing
                    m.array[:] = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                self._apply_timestamp(m, timestamp)

        except Exception as e:
            self.logger.error(f"Error capturing frame metadata: {e}")


    def _apply_timestamp(self, m: MappedArray, timestamp: str) -> None:
        """Apply the frame timestamp to the image."""
        x = 0
        y = self.height - int(self.height * 0.01) # TODO: Make origin reference lores dimensions
        cv2.putText(
            img=m.array, 
            text=timestamp, 
            org=(x, y), 
            fontFace=cv2.FONT_HERSHEY_SIMPLEX, 
            fontScale=self.config.get("camera.text_scale", 2), 
            color=(50,255,50), 
            thickness=self.config.get("camera.text_thickness", 1)
            ) 


    def _apply_frame_count(self, m: MappedArray, frame_count: int) -> None:
        """Apply the frame count to the image."""
        x = 0
        y = 0 + int(self.height*0.025) # Top but not offscreen
        cv2.putText(
            img=m.array, 
            text=str(frame_count), 
            org=(x,y), 
            fontFace=cv2.FONT_HERSHEY_SIMPLEX, 
            fontScale=1, 
            color=(50,255,50), 
            thickness=1
            )


    """Video streaming"""
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


def main():
    camera = CameraModule()
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

