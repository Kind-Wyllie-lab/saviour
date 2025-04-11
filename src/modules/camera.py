import cv2
import numpy as np
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from src.modules.module import Module

class CameraModule(Module):
    """Class to represent a camera module"""

    def __init__(self, config: dict):
        """Initialize the camera module"""
        super().__init__(module_type="camera", config=config) # call the parent class constructor
        self.camera = cv2.VideoCapture(0)
        self.frame_shape = (100, 100, 3) # default frame shape
        self.frame = np.zeros(self.frame_shape, dtype=np.uint8) # default frame

        # Camera specific initialization

    def get_frame(self):
        """Get a frame from the camera"""
        ret, frame = self.camera.read()
        return frame

    def get_fake_frame(self):
        """Get a fake frame from the camera"""
        self.frame = np.zeros((100, 100, 3), dtype=np.uint8)
    
    def show_frame(self):
        """Show the frame"""
        cv2.imshow("Camera", self.frame)
        cv2.waitKey(1)

    def get_frame_size(self):
        """Get the size of the frame"""
        return self.frame.shape
