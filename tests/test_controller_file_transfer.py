import sys
import os
import time
import logging
import pytest
import asyncio
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_controller_file_transfer_import():
    """Test that the controller file transfer can be imported"""
    from src.controller.controller_file_transfer_manager import ControllerFileTransfer
    assert ControllerFileTransfer is not None

def test_controller_file_transfer_init():
    """Test that the controller file transfer can be initialized"""
    from src.controller.controller_file_transfer_manager import ControllerFileTransfer
    assert ControllerFileTransfer is not None
    controller_file_transfer = ControllerFileTransfer(logging.getLogger("ControllerTestLogger"))
    assert controller_file_transfer is not None


