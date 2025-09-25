#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Proposal for a ModuleState class which holds module state within the program?

It's an Observer, I think?

It runs
network.get_discovered_modules()
For a top level overview of what modules have been _discovered_, and their IP addresses etc.
Then
health.get_module_state() or get_module_health() or get_module_status()
This should tell us whether a module is "online" or "offline"
- Online/offline state is based on whether it is sending heartbeats.
- Maybe it should also check the health of the communication socket? Health class could send something and see if it gets an ACK.
This should also report module status - NOT READY, READY, RECORDING, FAULT etc.
Controller can then run get_modules() here for a neatly packaged dict of modules which can be passed to frontend.
"""

import logging
import time
import threading

class Modules:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

        self.modules = {} # A dict of modules. module_id as primary key

        self.ready_timeout = 120 # Seconds to wait before a module is considered NOT_READY again
        self.ready_timeout_thread = threading.Thread(target=self._ready_timeout_checker)

        self.example_modules = {
            "module_d67a": {
                "online": True,
                "status": "NOT_READY",
            },
            "camera_1567": {
                "online": True,
                "status": "RECORDING"
            }
        }

        self.push_module_update_to_frontend = None # A callback to be registered by controller.py

    def network_notify_module_update(self, discovered_modules: dict):
        self.logger.info(f"Received discovered modules from Network: {discovered_modules}")
        for module in discovered_modules:
            if module.id not in self.modules.keys():
                self.logger.info(f"New module {module.id}, adding")
                self.modules[module.id] = {
                    "ip": module.ip,
                    "type": module.type,
                    "online": True, # Assume it's online at time of discovery # TODO: Consider asking health monitor if it's online?
                    "status": "NOT_READY" # Assume it's not yet ready at time of discovery # TODO: Consider calling check ready once?
                }
            else:
                self.logger.info(f"Existing module {module.id}, updating")
                self.modules[module.id] = {
                    "ip": module.ip,
                    "type": module.type,
                    "online": True,  # Assume it's online at time of discovery # TODO: Consider asking health monitor if it's online?
                    "status": "NOT_READY" # Assume it's not yet ready at time of discovery # TODO: Consider calling check ready once?
                }

        self.broadcast_updated_modules()

    def _ready_timeout_checker(self):
        while True:
            current_time = time.time()
            for module_id, module_data in self.modules.items():
                if module_data.get("status") == "READY":
                    ready_time = module_data.get("ready_time", 0)
                    if current_time - ready_time > self.ready_timeout:
                        self.logger.info(f"Module {module_id} has timed out from READY to NOT_READY")
                        self.modules[module_id]["status"] = "NOT_READY"
                        self.broadcast_updated_modules()
            time.sleep(5)

    def network_notify_module_id_change(self, old_module_id, new_module_id):
        # Move the module data to the new key
        self.modules[new_module_id] = self.modules.pop(old_module_id)
        self.broadcast_updated_modules()

    def network_notify_module_ip_change(self, module_id, module_ip):
        # IP changed for module
        self.modules["module_id"]["ip"] = module_ip
        self.broadcast_updated_modules()

    def communication_notify_module_update(self, module_id: str, module_data: dict):
        self.logger.info(f"Received update from Communication object concerning module {module_id}, with data {module_data}")
        self.logger.info("No implementation yet.")

    def notify_module_readiness_update(self, module_id: str, ready: bool):
        self.logger.info(f"Received update concerning module {module_id}, with ready status {ready}")
        if ready == True:
            status = "READY"
            self.modules[module_id]["status"] = status
            self.modules[module_id]["ready_time"] = time.time()
        else:
            status = "NOT_READY"
        self.modules[module_id]["status"] = status
        self.broadcast_updated_modules()

    def notify_module_online_update(self, module_id: str, online: bool):
        self.logger.info(f"Received update concerning module {module_id}, online status {online}")
        has_changed = self.modules[module_id]["online"] != online # Check if it changed

        self.modules[module_id]["online"] = online # Set the boolean value of online in the modules rcecord
        if not online:
            self.modules[module_id]["status"] = "OFFLINE" # If it's offline, also set it's status to OFFLINE.
        else:
            if has_changed:
                self.modules[module_id]["status"] = "NOT_READY" # Module has just been marked back online, so flip it's status to "NOT READY"
                
        self.broadcast_updated_modules()

    def notify_recording_started(self, module_id:str, module_data: dict):
        self.logger.info(f"{module_id} started recording {module_data}")
        if module_data["recording"] == True:
            self.logger.info("Recording was true")
            self.modules[module_id]["status"] = "RECORDING"
        else:
            self.logger.warning("Status start_recording received but recording param was False, some kind of error.") 
        self.broadcast_updated_modules()

    def notify_recording_stopped(self, module_id:str, module_data: dict):
        self.logger.info(f"{module_id} stopped recording {module_data}")
        if module_data["recording"] == False:
            self.logger.info("Recording was false")
            self.modules[module_id]["status"] = "NOT READY"
        else:
            self.logger.warning("Status stop_recording received but recording param was still True, some kind of error.") 
        self.broadcast_updated_modules()

    def remove_module(self, module_id: str):
        if module_id in self.modules.keys():
            self.modules.pop(module_id)
            self.broadcast_updated_modules()
            

    def broadcast_updated_modules(self):
        self.logger.info(f"Updated module list: {self.modules}")
        self.push_module_update_to_frontend(self.modules)

    def get_modules(self):
        return self.modules
    
    def start(self):
        self.logger.info("Starting Modules manager")
        self.ready_timeout_thread.start()   