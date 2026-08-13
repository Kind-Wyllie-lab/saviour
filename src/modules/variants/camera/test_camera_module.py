#!/usr/bin/env python3
"""
Test script for camera module

"""

from src.modules.variants.camera.camera_module import CameraModule


def test_camera_module():
    c = CameraModule()
    assert c
