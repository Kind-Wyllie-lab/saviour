from module import Module
import datetime

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
        self.video_folder = "rec/"
        self.video_filetype = ".mp4"


    def record_video(self, length: int = 10):
        """Record a short video"""
        self.logger.info("Recording video")
        
        # TODO: Implement video recording
        filename = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_test")

        cmd = [
            "rpicam-vid",
            "--framerate", "120",
            "--width", "1280",
            "--height", "720",
            "-t", f"{length}s",
            "-o", f"{self.video_folder}/{filename}.{self.video_filetype}",
            "--nopreview",
            "--level", "4.2", # H.264 level
            "--codec", "h264",
        ]

    def start_recording(self):
        """Start recording a video stream"""
        self.logger.info("Starting video recording")
        
        # TODO: Implement video recording
            
        
    