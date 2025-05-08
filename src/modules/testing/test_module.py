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
    from module import Module
    assert(Module)

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
        from module import Module
        module = Module(module_type="test", config=None)
        
        # Start module (it will discover our test controller)
        assert module.start()
        
        # Give it time to discover the controller
        time.sleep(1)
        
        # Check that module discovered the controller
        assert module.controller_ip == "127.0.0.1"
        assert module.controller_port == 5000

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

    try:
        # Create module AFTER controller is registered
        from module import Module
        module = Module(module_type="test", config=None)
        
        # Start module (it will discover our test controller)
        assert module.start()
        
        # Give it time to discover the controller and start command thread
        time.sleep(2)  # Increased sleep time to ensure thread starts

        # Create a fake controller command socket
        context = zmq.Context()
        cmd_socket = context.socket(zmq.PUB)
        print(f"Binding PUB socket to tcp://*:5555")
        cmd_socket.bind("tcp://*:5555") # Bind the socket to port 5555 with a wildcard address - this means the socket will listen for connections on all interfaces
        time.sleep(0.1)  # Give time for binding to establish

        # Send a test command
        test_command = "get_status"
        message = f"cmd/{module.module_id} {test_command}"
        print(f"Sending message: {message}")
        cmd_socket.send_string(message)
        print("Message sent")

        # Check that the module received the command    
        time.sleep(1)
        assert module.last_command == test_command

    finally:
        # Clean up
        cmd_socket.close()
        context.term()
        module.stop()
        zeroconf.unregister_service(service_info)
        zeroconf.close()