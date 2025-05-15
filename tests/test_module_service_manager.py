import sys
import os
import time
import logging
import pytest
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import ModuleServiceManager first
from src.modules.module_service_manager import ModuleServiceManager
# Then import Module
from src.modules.module import Module

def test_module_service_manager_import():
    """Test that the module service manager can be imported"""
    # We already imported it at the top, no need to reimport
    assert ModuleServiceManager is not None
    
    # Also test that Module can be imported
    assert Module is not None
    
    # Create a simple logger for testing
    logger = logging.getLogger("test")
    
    # This is the crucial test - we can create a Module without circular import errors
    module = Module(module_type="test")
    assert module is not None
    assert hasattr(module, 'service_manager')
    assert module.service_manager is not None

    # Cleanup
    module.stop()
    module.service_manager.cleanup()
    
# Skip the complex initialization test that requires too much mocking
@pytest.mark.skip("Requires extensive mocking of Zeroconf")
def test_module_service_manager_init():
    pass
