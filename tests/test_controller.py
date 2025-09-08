import sys
import os
import time
import logging
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_controller_import():
    """Test that the controller can be imported"""
    from src.controller.controller import Controller
    assert Controller is not None

def test_controller_init():
    """Test that the controller can be initialized"""
    from src.controller.controller import Controller