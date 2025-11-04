#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Data Models

This file provides a single source for representations of data important to the Controller.

Author: Andrew SG
Created: 31/10/25
"""
from enum import StrEnum
from dataclasses import dataclass, field
from typing import Dict, Any

class ModuleStatus(StrEnum):
    """Enum class which holds all possible status codes a Module can be in"""
    NOT_READY = "NOT_READY"
    READY = "READY"
    RECORDING = "RECORDING"
    FAULT = "FAULT"
    OFFLINE = "OFFLINE"

@dataclass 
class Module:
    """Dataclass to represent a connected SAVIOUR Module"""
    id: str # ID of connected module which is combination of type and last 4 digits of MAC e.g. camera_dc67
    name: str # Current name of the module - probably defaults to ID but can be renamed e.g. camera_dc67 -> Top_Camera
    type: str # Module type e.g. camera, TTL
    ip: str # Ip of connected module
    zeroconf_name: str = "" # Zeroconf service name of connected module
    port: int = 5353 # The port that zeroconf is operating on?
    groups: list = field(default_factory=list) # Groups the module belongs to
    online: bool = True # Default to assuming it's online
    status: ModuleStatus = ModuleStatus.NOT_READY # Default to NOT_READY
    config: Dict[str, Any] = field(default_factory=dict)
    ready_time: float = 0.0 # Time at which a module went ready, so as to flip it back to NOT_READY if time elapsed.