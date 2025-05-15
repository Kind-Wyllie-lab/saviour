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

