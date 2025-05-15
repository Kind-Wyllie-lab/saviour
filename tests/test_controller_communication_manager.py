import sys
import os
import time
import logging
import pytest
import zmq
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



def test_controller_communication_manager_send_command():
    """Test the controller communication manager"""
    import src.controller.controller_communication_manager as communication_manager
    assert communication_manager is not None

    c = communication_manager.ControllerCommunicationManager(logging.getLogger("ControllerTestLogger"))
    assert c is not None

    # Start the communication manager
    c.is_running = True

    # Make a fake module to communicate with
    try:
        module_context = zmq.Context()
        module_id = "test_module"

        # Receiving commands
        module_command_socket = module_context.socket(zmq.SUB)
        module_command_socket.connect("tcp://localhost:5555")
        module_command_socket.subscribe(f"cmd/{module_id}")

        # Sending status updates
        module_status_socket = module_context.socket(zmq.PUB)
        module_status_socket.connect("tcp://localhost:5556")

        # Wait for the sockets to connect
        time.sleep(0.5)

        # Test sending a command
        c.send_command(module_id=module_id, command="test_command")

        # Give some time for it to arrive
        time.sleep(0.1)
        
        # Check if received
        try:
            message = module_command_socket.recv_string(flags=zmq.NOBLOCK)
            time.sleep(0.1)
            assert message == "cmd/test_module test_command"
        except Exception as e:
            print(f"Error receiving command: {e}")
            assert False

        # Send a command with a different module id
        c.send_command(module_id="other_module", command="test_command")
        time.sleep(0.1)
        
        # Test that the module DOESN'T receive other modules messages
        try:
            message = module_command_socket.recv_string(flags=zmq.NOBLOCK)
            assert False, f"Should not have received message: {message}"
        except zmq.error.Again:
            # This is what we want! No message available means the module didn't receive the message for other_module
            pass

        # cleanup module
        module_command_socket.close()
        module_status_socket.close()
        module_context.term()
    
    finally:
        c.cleanup() 
        