import sys
import os
import time
import logging

def test_controller_import():
    """Test that the controller can be imported"""
    from controller import Controller
    assert Controller is not None

def test_controller_init():
    """Test that the controller can be initialized"""
    from controller import Controller