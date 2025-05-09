import sys
import os
import time
import logging
import pytest
import asyncio
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_module_file_transfer_import():
    """Test that the module file transfer can be imported"""
    from module_file_transfer import ModuleFileTransfer
    assert ModuleFileTransfer is not None

@pytest.mark.asyncio
async def test_module_file_transfer_init():
    """Test that the module file transfer can be initialized"""
    from module_file_transfer import ModuleFileTransfer
    test_file_transfer = ModuleFileTransfer("192.168.0.14", logging.getLogger("ModuleTestLogger"))
    assert test_file_transfer is not None
    assert test_file_transfer.controller_ip == "192.168.0.14"

# @pytest.mark.asyncio

