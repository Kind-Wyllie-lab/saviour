

import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from src.controller.facade import ControllerFacade

MockController = {}

def test_facade():
    facade = ControllerFacade(MockController)
    assert facade

