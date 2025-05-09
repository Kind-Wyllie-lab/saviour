import sys
import os
import uuid
import time
import pytest
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_camera_module_import():
    from camera_module import CameraModule
    assert(CameraModule)

def test_camera_module_type():
    from camera_module import CameraModule
    camera = CameraModule()
    assert(camera.module_type == "camera")
    # cleanup
    camera.zeroconf.unregister_service(camera.service_info)
    camera.zeroconf.close()

def test_camera_module_start_stop():
    """Test that module can start and stop"""
    from camera_module import CameraModule
    camera = CameraModule()
    assert camera.start()
    time.sleep(0.5)
    assert camera.is_running == True
    assert camera.stop()
    time.sleep(0.5)
    assert camera.is_running == False
    # cleanup

def test_camera_module_id():
    from camera_module import CameraModule
    camera = CameraModule()
    mac = hex(uuid.getnode())[2:]  # Gets MAC address as hex, removes '0x' prefix
    short_id = mac[-4:]  # Takes last 4 characters
    assert(camera.module_id == f"camera_{short_id}")
    # cleanup
    camera.zeroconf.unregister_service(camera.service_info)
    camera.zeroconf.close()

def test_camera_module_record_video():
    from camera_module import CameraModule
    camera = CameraModule()
    filename = camera.record_video(3)
    time.sleep(1) # Give time for file to be created
    assert(os.path.exists(f"{filename}"))
    # cleanup
    camera.zeroconf.unregister_service(camera.service_info)
    camera.zeroconf.close()




