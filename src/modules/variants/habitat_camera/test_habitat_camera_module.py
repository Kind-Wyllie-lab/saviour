#!/usr/bin/env python3
"""
Test script for habitat_camera module

"""

from src.modules.variants.habitat_camera.habitat_camera_module import (
    HabitatCameraModule,
)


def test_habitat_camera_module():
    c = HabitatCameraModule()
    assert c
