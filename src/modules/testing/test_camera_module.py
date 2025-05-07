import sys
import os
import uuid
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_camera_module_import():
    from camera_module import CameraModule
    assert(CameraModule)

def test_camera_module_type():
    from camera_module import CameraModule
    camera = CameraModule()
    assert(camera.module_type == "camera")

def test_camera_module_id():
    from camera_module import CameraModule
    camera = CameraModule()
    mac = hex(uuid.getnode())[2:]  # Gets MAC address as hex, removes '0x' prefix
    short_id = mac[-4:]  # Takes last 4 characters
    assert(camera.module_id == f"camera_{short_id}")

def test_camera_module_start_recording():
    from camera_module import CameraModule
    camera = CameraModule()
    camera.start_recording()
    assert(camera)


