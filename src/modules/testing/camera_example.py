import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camera_module import CameraModule

camera = CameraModule()
camera.record_video(3)