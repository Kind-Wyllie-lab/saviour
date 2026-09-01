#!/usr/bin/env python3
"""
Test script for basler_camera module

SKETCH: instantiating BaslerCameraModule needs a real Basler device (pypylon
enumerates hardware in _open_camera()), so this only smoke-tests what's
possible without one: pypylon import + skip cleanly on a dev box / CI runner
with neither pypylon nor a camera attached, same shape as the hailo_camera
graceful-fallback tests.
"""

import pytest

pytest.importorskip("pypylon")

from src.modules.variants.basler_camera.basler_camera_module import (
    BaslerCameraModule,
)


def test_basler_camera_module_requires_hardware():
    try:
        c = BaslerCameraModule()
    except RuntimeError as e:
        pytest.skip(f"No Basler device available: {e}")
    else:
        assert c
