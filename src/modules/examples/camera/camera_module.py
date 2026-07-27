#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - Camera Module Class

Plain camera module — all Picamera2/streaming/recording infrastructure lives
in CameraBase (src/modules/camera_base.py); this file has no unique logic of
its own beyond its own config filename.

Picamera2 is used for interfacing camera hardware. This is a python wrapper for libcamera / rpicam.

For a good discussion of getting high framerates (via correct sensor mode), read this thread: https://github.com/raspberrypi/picamera2/discussions/111#discussioncomment-13518732
For a good discussion of getting frame timestamps and syncing with precallbacks, read this thread: https://forums.raspberrypi.com/viewtopic.php?t=377442

Author: Andrew SG
Created: 17/03/2025
"""

import sys
import os
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.camera_base import CameraBase


class CameraModule(CameraBase):
    CONFIG_FILENAME = "camera_config.json"

    def __init__(self, module_type="camera"):
        super().__init__(module_type)


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
