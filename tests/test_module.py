import zmq
import time
import json
import sys
import os
import uuid
import threading
from zeroconf import Zeroconf, ServiceInfo
import socket
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_module_import():
    """Test that module can be imported"""
    from src.modules.module import Module
    assert(Module)

def test_module_start_stop():
    """Test that module can start and stop"""
    from src.modules.module import Module
    module = Module(module_type="test", config=None)
    assert module.start()
    time.sleep(0.5)
    assert module.is_running == True
    assert module.stop()
    time.sleep(0.5)
    assert module.is_running == False


def test_module_zeroconf_discovery():
    """Test that module can discover a controller via zeroconf"""
    # Register a test controller service FIRST
    zeroconf = Zeroconf()
    service_info = ServiceInfo(
        "_controller._tcp.local.",
        "test_controller._controller._tcp.local.",
        addresses=[socket.inet_aton("127.0.0.1")],
        port=5000,
        properties={'type': 'controller', 'id': 'test_controller'}  # Make sure type is 'controller'
    )
    zeroconf.register_service(service_info)

    try:
        # Create module AFTER controller is registered
        from src.modules.module import Module
        module = Module(module_type="test", config=None)
        
        # Start module (it will discover our test controller)
        assert module.start()
        
        # Give it time to discover the controller
        time.sleep(1)
        
        # Check that module discovered the controller via service manager
        assert module.service_manager.controller_ip == "127.0.0.1"
        assert module.service_manager.controller_port == 5000

    finally:
        # Clean up
        module.stop()
        zeroconf.unregister_service(service_info)
        zeroconf.close()

def test_module_zmq_command_receiving():
    """Test the module's ability to receive commands from the controller"""
    # Register a test controller service FIRST
    zeroconf = Zeroconf()
    service_info = ServiceInfo(
        "_controller._tcp.local.",
        "test_controller._controller._tcp.local.",
        addresses=[socket.inet_aton("127.0.0.1")],
        port=5000,
        properties={'type': 'controller', 'id': 'test_controller'}
    )
    zeroconf.register_service(service_info)

    module = None
    context = None
    cmd_socket = None
    try:
        # Create module AFTER controller is registered
        from src.modules.module import Module
        module = Module(module_type="test", config=None)

        # Start module (it will discover our test controller)
        assert module.start()

        # Create a fake controller command socket FIRST (before waiting)
        # so connections have time to establish
        context = zmq.Context()
        cmd_socket = context.socket(zmq.PUB)
        print(f"Binding PUB socket to tcp://*:5555")
        cmd_socket.bind("tcp://*:5555") # Bind the socket to port 5555
        
        # Give it time to discover the controller and start command thread
        # This delay is crucial for ZMQ PUB/SUB to establish connection
        print("Waiting for ZMQ connection to establish...")
        time.sleep(2)  
        
        # IMPORTANT: To avoid the "slow joiner syndrome" in ZMQ PUB/SUB,
        # send a few messages that will be intentionally missed, then
        # send the real test command
        for i in range(3):
            cmd_socket.send_string("cmd/dummy warming_up")
            time.sleep(0.1)

        # Send a test command
        test_command = "get_status"
        message = f"cmd/{module.module_id} {test_command}"
        print(f"Sending message: {message}")
        cmd_socket.send_string(message)
        print("Message sent")

        # Wait for command to be received with timeout
        start_time = time.time()
        timeout = 5  # 5 second timeout
        while time.time() - start_time < timeout:
            if hasattr(module, 'last_command') and module.last_command == test_command:
                break
            time.sleep(0.1)
        
        # Now check the command was received
        assert module.last_command == test_command, f"Expected command '{test_command}', got '{module.last_command}'"

    finally:
        # Cleanup
        if module:
            module.stop()
        if 'zeroconf' in locals():
            zeroconf.unregister_service(service_info)
            zeroconf.close()
        if cmd_socket:
            cmd_socket.setsockopt(zmq.LINGER, 0)
            cmd_socket.close()
        if context:
            context.term()
