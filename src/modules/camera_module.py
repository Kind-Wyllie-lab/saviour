from module import Module
import datetime
import subprocess
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
                # length = kwargs.get('length', 10)
                # return self.record_video(length)

                # Record 10s of video and return filename
                filename = self.record_video()

                # Send video to controller
                # TODO: Implement this
                self.send_data(f"Video created at {filename}")
                return True

                
            # If not a camera-specific command, pass to parent class
            case _:
                return super().handle_command(command, **kwargs)

    def record_video(self, length: int = 10):
        """Record a short video"""
        self.logger.info("Recording video")
        
        filename = f"{self.video_folder}/{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_test')}.{self.video_filetype}"

        cmd = [
            "rpicam-vid",
            "--framerate", "120",
            "--width", "1280",
            "--height", "720",
            "-t", f"{length}s",
            "-o", f"{filename}",
            "--nopreview",
            "--level", "4.2", # H.264 level
            "--codec", "h264",
        ]

        self.logger.info(f"Recording video to {filename}")
        self.logger.info(f"Command: {' '.join(cmd)}")

        # Execute the command
        subprocess.run(cmd)
        
        # return filename 
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
        
        
    