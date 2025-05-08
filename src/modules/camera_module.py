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
                "format": "h264",
            }
        
        # Call the parent class constructor
        super().__init__(module_type, config)
        
        # Camera specific variables
        self.video_folder = "rec"
        self.video_filetype = "mp4"


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
        
        # TODO: Implement video recording
            
        
    