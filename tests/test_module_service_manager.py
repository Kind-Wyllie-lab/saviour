import sys
import os
import time
import logging
import pytest
import asyncio
import socket
from zeroconf import ServiceInfo, Zeroconf
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from src.modules.module import Module
module = Module(module_type="test",
                config=None,
                config_file_path=None)

def test_module_service_manager_import():
    """Test that the module service manager can be imported"""
    from src.modules.module_service_manager import ModuleServiceManager
    assert ModuleServiceManager is not None

def test_module_service_manager_init():
    """Test that the module service manager can be initialized"""
    from src.modules.module_service_manager import ModuleServiceManager

    module_service_manager = ModuleServiceManager(logging.getLogger("ModuleServiceManagerTestLogger"), 
                                                  module=module)
    
    assert module_service_manager is not None
    assert module_service_manager.module is not None
    assert module_service_manager.module.module_id == module.module_id
    assert module_service_manager.module.module_type == module.module_type
    assert module_service_manager.module.config is not None

    assert module_service_manager.ip is not None
    assert module_service_manager.zeroconf is not None
    assert module_service_manager.service_info is not None
    assert module_service_manager.browser is not None
