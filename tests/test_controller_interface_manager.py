import sys
import os
import time
import logging
import pytest
import zmq
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_import():
    """Test the controller interface manager"""
    import src.controller.controller_interface_manager as interface_manager
    assert interface_manager is not None

def test_init():
    """Test the controller interface manager initialization"""
    from src.controller.controller_interface_manager import ControllerInterfaceManager
    from src.controller.controller import Controller
    controller = Controller()
    interface_manager = ControllerInterfaceManager(controller)
    assert interface_manager is not None
