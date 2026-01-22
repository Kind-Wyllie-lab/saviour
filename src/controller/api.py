#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller API

This class is used to glue the various other classes that comprise a controller together.

It provides an interface for controller objects to interact with one another.

Note this is an internal API for use between parts of the controller program. An External API for the controller-module relationship would be a separate concern and does not yet exist.

Author: Andrew SG
Created: 20/01/2026
"""

import logging
import os
from typing import Dict, Any, Optional

class ControllerAPI():
    def __init__(self, controller):
        self.logger = logging.getLogger(__name__)
        self.logger.info("Instantiating ControllerAPI...")
        self.controller = controller

    
    """Getter Methods"""
    def get_controller_config(self) -> dict:
        return self.controller.config.get_all()

    """Set config"""
    def set_config(self, new_config: dict) -> bool:
        self.controller.config.set_all(new_config)
        updated_config = self.controller.config.get_all()
        if new_config != updated_config:
            return False
        else: 
            return True

