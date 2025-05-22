import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from web_interface_manager import WebInterfaceManager
import logging

test_logger = logging.getLogger(__name__)

web_interface_manager = WebInterfaceManager(logger=test_logger)
web_interface_manager.test = True
web_interface_manager.start_web_interface()