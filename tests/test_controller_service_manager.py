import sys
import os
import time
import logging
import pytest
import asyncio
import socket
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_controller_service_manager_import():
    """Test that the controller file transfer can be imported"""
    from src.controller.controller_service_manager import ControllerServiceManager
    assert ControllerServiceManager is not None

def test_controller_service_manager_init():
    """Test that the controller service manager can be initialized"""
    from src.controller.controller_service_manager import ControllerServiceManager
    controller_service_manager = ControllerServiceManager()
    assert controller_service_manager is not None
    controller_service_manager.cleanup()

def test_controller_service_manager_general():
    """Test that the controller service manager can handle general tasks"""
    from src.controller.controller_service_manager import ControllerServiceManager
    controller_service_manager = ControllerServiceManager()
    assert controller_service_manager is not None

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

    finally:
        # Ensure cleanup happens even if assertions fail
        controller_service_manager.cleanup()


    
    