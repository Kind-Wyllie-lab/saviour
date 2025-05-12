from dataclasses import dataclass # to define a dataclass
from typing import List, Dict, Any # for type hinting
import datetime # for timestamp
import session

s = session.SessionManager()

@dataclass
class Module:
    """Dataclass to represent a module in the habitat system - used by zeroconf to discover modules"""
    id: str
    name: str
    type: str
    ip: str
    port: int
    properties: Dict[str, Any]

@dataclass
class ModuleData:
    """Dataclass to represent data from a module"""
    timestamp: float # timestamp of a given data point
    data: any # the data itself
    session_id: str | None # the session ID of the data (this contains module_id, is it necessary to include both?). it can be None if we gather data outside of a session e.g. for debugging
    module_id: str # the module ID of the data

modules: List[Module] = []
module_data: Dict[str, List[ModuleData]] = {} # Module data is a dict of module IDs, each with a list of ModuleData objects
active_sessions = {} # a dict of session IDs, each with a list of module IDs

# Pretend we have discovered some modules
module1 = Module(id="camera_uj27", name="camera_uj27", type="camera", ip="192.168.1.63", port=8080, properties={})
module2 = Module(id="microphone_ae95", name="microphone_ae95", type="microphone", ip="192.168.1.64", port=8080, properties={})

modules.append(module1)
modules.append(module2)

# Generate a session ID for each module
for module in modules:
    active_sessions[s.generate_session_id(module.id)] = module.id

# Generate some test data
# print(modules["id"=="camera_uj27"])

print(active_sessions)
print(active_sessions.keys())
# @todo: set active_sessions key to module id?
