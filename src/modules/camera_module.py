from module import Module
import datetime
import subprocess
import os

class CameraModule(Module):
    def __init__(self, module_type="camera", config=None):

        if config is None:
            config = {
                "fps": 100,
                "width": 1280,
                "height": 720,
                "codec": "h264",
                "profile": "high",
                "level": 4.2,
                "intra": 30,
                "file_format": "mp4",
            }
        
        # Call the parent class constructor
        super().__init__(module_type, config)
        
        # Camera specific variables
        self.video_folder = "rec"
        self.video_filetype = self.config.get("file_format")
        self.config = config

        # State flags
        self.is_recording = False


    def handle_command(self, command, **kwargs):
        """Handle camera-specific commands while preserving base module functionality"""

        # Handle camera-specific commands
        match command:
            case "start_recording":
                output_video = kwargs.get('output_video', 'recording.h264')
                output_timestamps = kwargs.get('output_timestamps', 'timestamps.txt')
                fps = kwargs.get('fps', self.config.get('fps', 30))
                return self.start_recording(output_video, output_timestamps, fps)
                
            case "stop_recording":
                return self.stop_recording()

            case "record_video":
                # Get recording parameters from kwargs or use defaults
                length = kwargs.get('length', 10)  # Default 10 seconds
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
        
        # Build command with high-quality settings
        cmd = [
            "rpicam-vid",
            "--framerate", str(self.config.get("fps", 120)),
            "--width", str(self.config.get("width", 1280)),
            "--height", str(self.config.get("height", 720)),
            "-t", f"{length}s",
            "-o", filename,
            "--nopreview",
            "--level", str(self.config.get("level", "4.2")),
            "--codec", self.config.get("codec", "h264"),
            "--profile", self.config.get("profile", "high"),
            "--intra", str(self.config.get("intra", 30))
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
                    "filename": filename,
                    "session_id": self.stream_session_id,
                    "duration": length
                })
                return filename
            else:
                self.logger.error(f"Video recording failed with return code {process.returncode}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error during video recording: {e}")
            return None

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

        cmd = [
            "rpicam-vid",
            "-t", "0",
            "--framerate", "100",
            "--width", "1280",
            "--height", "720",
            "-o", f"{filename}",
            "--nopreview",
            "--level", "4.2", # H.264 level
            "--codec", "h264",
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
        
        
    