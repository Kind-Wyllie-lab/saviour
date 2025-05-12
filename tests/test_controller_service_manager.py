import sys
import os
import time
import logging
import pytest
import asyncio
import socket
from zeroconf import ServiceInfo, Zeroconf
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_controller_service_manager_import():
    """Test that the controller file transfer can be imported"""
    from src.controller.controller_service_manager import ControllerServiceManager
    assert ControllerServiceManager is not None

def test_controller_service_manager_init():
    """Test that the controller service manager can be initialized"""
    from src.controller.controller_service_manager import ControllerServiceManager
    controller_service_manager = ControllerServiceManager(logging.getLogger("ControllerServiceManagerTestLogger"))
    assert controller_service_manager is not None
    assert controller_service_manager.logger is not None
    controller_service_manager.cleanup()

def test_controller_service_manager_general():
    """Test that the controller service manager can handle general tasks"""
    from src.controller.controller_service_manager import ControllerServiceManager
    controller_service_manager = ControllerServiceManager(logging.getLogger("ControllerServiceManagerTestLogger"))
    assert controller_service_manager is not None

    # Create a fake module service
    fake_module_zc = Zeroconf()
    fake_module_info = ServiceInfo(
        "_module._tcp.local.",
        "test_module._module._tcp.local.",
        addresses=[socket.inet_aton('127.0.0.1')],
        port=5000,
        properties={
            b'id': b'test_id',
            b'type': b'test_type'
        }
    )

    try:
        # Test IP address
        if os.name == 'nt': # Windows
            ip = socket.gethostbyname(socket.gethostname())
        else: # Linux/Unix
            ip = os.popen('hostname -I').read().split()[0]
        assert controller_service_manager.ip == ip

        # Test service info
        assert controller_service_manager.service_info is not None
        assert controller_service_manager.service_info.type == "_controller._tcp.local."
        assert controller_service_manager.service_info.name == "controller._controller._tcp.local."
        assert controller_service_manager.service_info.properties == {b'type': b'controller'}
        assert controller_service_manager.service_info.addresses == [socket.inet_aton(ip)]
        assert controller_service_manager.service_info.port == 5000

        # Test service browser
        assert controller_service_manager.browser is not None
        assert isinstance(controller_service_manager.modules, list)
        assert hasattr(controller_service_manager, 'add_service')
        assert hasattr(controller_service_manager, 'remove_service')
        assert hasattr(controller_service_manager, 'update_service')

        # Test module discovery
        assert len(controller_service_manager.modules) == 0
        
        # Register the fake module and wait for discovery
        fake_module_zc.register_service(fake_module_info)
        time.sleep(1)  # Give some time for discovery
        
        # Verify module was discovered
        assert len(controller_service_manager.modules) == 1
        discovered_module = controller_service_manager.modules[0]
        assert discovered_module.name == "test_module._module._tcp.local."
        assert discovered_module.type == "test_type"
        assert discovered_module.id == "test_id"
        assert discovered_module.ip == "127.0.0.1"
        assert discovered_module.port == 5000

        # Test module removal by unregistering the fake service
        fake_module_zc.unregister_service(fake_module_info)
        time.sleep(1)  # Give some time for removal
        assert len(controller_service_manager.modules) == 0

    finally:
        # Ensure cleanup happens even if assertions fail
        fake_module_zc.close()
        controller_service_manager.cleanup()


    
    