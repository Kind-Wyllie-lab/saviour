#!/usr/bin/env python3
"""
SAVIOUR System - Habitat Camera Module Class

Starting point for the habitat rig's 24/7 activity-gated camera (see
CLAUDE.md's "habitat_camera" feature idea) -- currently a plain subclass of
CameraBase identical in behaviour to the basic `camera` module, with its own
config filename/module type so activity-gating, pre-roll (via Picamera2's
CircularOutput), and immediate-export-on-clip-close can be layered on here
without touching the other camera variants.

Author: Andrew SG
"""

import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.camera_base import CameraBase


class HabitatCameraModule(CameraBase):
    CONFIG_FILENAME = "habitat_camera_config.json"

    def __init__(self, module_type="habitat_camera"):
        super().__init__(module_type)


def main():
    camera = HabitatCameraModule()
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
