"""
Habitat System - Testing Camera Module

This file is used to test the camera module.

Author: Andrew SG
Created: 17/03/2025
License: GPLv3
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import modules.camera as camera
import cv2

c = camera.CameraModule({})

while True:
    c.get_frame()
    c.show_frame()
    
    # Break loop when 'q' is pressed
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cv2.destroyAllWindows()