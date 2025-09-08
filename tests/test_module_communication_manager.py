#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for Communication

Tests the ZMQ communication functionality between module and controller.
"""

import sys
import os
import pytest
import zmq
import time
import threading
import logging
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.modules.communication import Communication

# Setup logging for tests
logging.basicConfig(level=logging.INFO)

def test_module_communication_manager_import():
    """Test that the module communication manager can be imported"""
    assert Communication is not None

def test_init():
    """Test initialization of communication manager"""
    logger = logging.getLogger("test")
    manager = Communication(logger, "test_module")
    
    assert manager.module_id == "test_module"
    assert manager.logger == logger
    assert manager.command_callback is None
    assert manager.controller_ip is None
    assert manager.controller_port is None
    assert manager.command_listener_running is False
    assert manager.heartbeats_active is False
    assert manager.last_command is None
    
    # Should have created ZMQ context and sockets
    assert manager.context is not None
    assert manager.command_socket is not None
    assert manager.status_socket is not None

def test_connect():
    """Test connecting to controller"""
    logger = logging.getLogger("test")
    manager = Communication(logger, "test_module")
    
    # Connect to a fictional controller
    result = manager.connect("127.0.0.1", 5000)
    assert result is True
    
    # Check that connection state is updated
    assert manager.controller_ip == "127.0.0.1"
    assert manager.controller_port == 5000
    
    # Clean up
    manager.cleanup()

@pytest.mark.skipif(os.name == 'nt', reason="ZMQ connection test unreliable on Windows")
def test_communication():
    """Test full communication cycle"""
    # Create fake controller for testing
    context = zmq.Context()
    command_socket = context.socket(zmq.PUB)
    command_socket.bind("tcp://*:5555")
    
    status_socket = context.socket(zmq.SUB)
    status_socket.setsockopt_string(zmq.SUBSCRIBE, "status/test_module")
    status_socket.setsockopt_string(zmq.SUBSCRIBE, "data/test_module")
    status_socket.bind("tcp://*:5556")
    
    # Setup test data
    command_received = threading.Event()
    last_command = [None]  # Use list to allow modification from callback
    
    def command_callback(command):
        last_command[0] = command
        command_received.set()
    
    # Create manager
    logger = logging.getLogger("test")
    manager = Communication(
        logger, 
        "test_module",
        command_callback=command_callback
    )
    
    try:
        # Connect to our fake controller
        result = manager.connect("127.0.0.1", 5000)
        assert result is True
        
        # Start command listener
        manager.start_command_listener()
        time.sleep(0.5)  # Give time for connection to establish
        
        # Send a command from fake controller
        for i in range(3):  # Send a few messages to handle slow joiner syndrome
            command_socket.send_string("cmd/dummy warming_up")
            time.sleep(0.1)
            
        test_command = "test_command"
        command_socket.send_string(f"cmd/test_module {test_command}")
        
        # Wait for command to be received
        assert command_received.wait(timeout=5.0) is True
        assert last_command[0] == test_command
        assert manager.last_command == test_command
        
        # Test sending status
        test_status = {"status": "ok", "value": 123}
        manager.send_status(test_status)
        
        # Wait for status message
        start_time = time.time()
        status_received = False
        while time.time() - start_time < 5.0 and not status_received:
            try:
                message = status_socket.recv_string(zmq.NOBLOCK)
                topic, data = message.split(' ', 1)
                
                if topic == "status/test_module" and "ok" in data:
                    status_received = True
                    break
            except zmq.Again:
                time.sleep(0.1)
                
        assert status_received is True
        
        # Test sending data
        test_data = "test_data_value"
        manager.send_data(test_data)
        
        # Wait for data message
        start_time = time.time()
        data_received = False
        while time.time() - start_time < 5.0 and not data_received:
            try:
                message = status_socket.recv_string(zmq.NOBLOCK)
                topic, data = message.split(' ', 1)
                
                if topic == "data/test_module" and test_data in data:
                    data_received = True
                    break
            except zmq.Again:
                time.sleep(0.1)
                
        assert data_received is True
        
    finally:
        # Clean up
        manager.cleanup()
        
        # Clean up fake controller
        command_socket.close()
        status_socket.close()
        context.term()

def test_heartbeats():
    """Test heartbeat functionality"""
    # Create mock callback that returns test data
    def heartbeat_callback():
        return {"status": "ok", "cpu": 50}
    
    # Create manager with mock send_status
    logger = logging.getLogger("test")
    manager = Communication(logger, "test_module")
    manager.send_status = MagicMock()
    
    # Set controller IP to allow heartbeats
    manager.controller_ip = "127.0.0.1" 
    
    try:
        # Start heartbeats with very short interval
        result = manager.start_heartbeats(heartbeat_callback, interval=0.1)
        assert result is True
        assert manager.heartbeats_active is True
        
        # Wait for some heartbeats
        time.sleep(0.5)
        
        # Check that send_status was called with our data
        manager.send_status.assert_called()
        args = manager.send_status.call_args[0][0]
        assert args["status"] == "ok"
        assert args["cpu"] == 50
        
        # Stop heartbeats
        manager.stop_heartbeats()
        assert manager.heartbeats_active is False
        
        # Record current call count and make sure it doesn't increase
        call_count = manager.send_status.call_count
        time.sleep(0.5)
        assert manager.send_status.call_count == call_count
        
    finally:
        # Clean up
        manager.cleanup()

def test_cleanup():
    """Test cleanup of resources"""
    logger = logging.getLogger("test")
    manager = Communication(logger, "test_module")
    
    # Connect to controller
    manager.connect("127.0.0.1", 5000)
    
    # Start command listener (create thread)
    manager.start_command_listener()
    
    # Start heartbeats (create thread)
    manager.start_heartbeats(lambda: {"status": "ok"}, interval=0.1)
    
    # Verify threads are running
    assert manager.command_listener_running is True
    assert manager.heartbeats_active is True
    assert manager.command_thread is not None
    assert manager.command_thread.is_alive() is True
    
    # Clean up
    manager.cleanup()
    
    # Verify threads are stopped and flags reset
    assert manager.command_listener_running is False
    assert manager.heartbeats_active is False
    
    # Verify sockets and context are recreated
    assert manager.context is not None
    assert manager.command_socket is not None
    assert manager.status_socket is not None
    
    # Verify connection state is reset
    assert manager.controller_ip is None
    assert manager.controller_port is None 