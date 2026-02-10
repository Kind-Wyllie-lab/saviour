#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Module State Manager

It runs
network.get_discovered_modules()
For a top level overview of what modules have been _discovered_, and their IP addresses etc.
Then
health.get_module_state() or get_module_health() or get_module_status()
This should tell us whether a module is "online" or ModuleStatus.OFFLINE
- Online/offline state is based on whether it is sending heartbeats.
- Maybe it should also check the health of the communication socket? Health class could send something and see if it gets an ACK.
This should also report module status - NOT READY, READY, RECORDING, FAULT etc.
Controller can then run get_modules() here for a neatly packaged dict of modules which can be passed to frontend.

Author: Andrew SG
Created: ?
"""


import logging
import time
import threading
from typing import Dict, Any
from dataclasses import asdict
from src.controller.models import Module, ModuleStatus


class Modules:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

        self.ready_timeout = 120 # Seconds to wait before a module is considered NOT_READY again
        self.ready_timeout_thread = threading.Thread(target=self._ready_timeout_checker)

        self.modules: dict[str, Module] = {} # A dict of modules. module_id as primary key


    def check_status(self, module_id: str, status_data: dict) -> None:
        """Handle a received heartbeat from a module - check recording status"""
        # self.logger.info(f"Checking status for {module_id}, recording status {status_data.get('recording', 'unknown')}")
        if 'recording' in status_data:
            if status_data['recording'] == False:
                # self.logger.info(f"{module_id} says it is not recording")
                # self.logger.info(f"Module status in our terms is {self.modules[module_id].status}")
                if self.modules[module_id].status == ModuleStatus.RECORDING:
                    # Module has switched from recording to not recording without emitting a stop_recording message
                    self.modules[module_id].status = ModuleStatus.DEFAULT
            if status_data['recording'] == True and self.modules[module_id].status != ModuleStatus.RECORDING:
                # Module says it's recording but our state doesn't reflect this - update our state
                self.modules[module_id].status = ModuleStatus.RECORDING
            # self.logger.info(f"Nothing to do on status check")


    def network_notify_module_update(self, discovered_modules: list[Module]):
        """
        When zeroconf adds a newly discovered module or updates an existing one, this gets called.

        args:
            discovered_modules - List of discovered Modules with up-to-date info e.g. ID, IP
        """
        self.logger.info(f"Received discovered modules from Network: {discovered_modules}")
        for module in discovered_modules:
            self.logger.info(f"Checking {module}")
            if module.id not in self.modules.keys():
                self.logger.info(f"New module {module.id}, adding")
            else:
                self.logger.info(f"Existing module {module.id}, updating")
            # self.modules[module.id] = module
            self.add_module(module)
        self.broadcast_updated_modules()


    def add_module(self, module: Module):
        self.modules[module.id] = module
        self.logger.info(f"Module {self.modules[module.id].name} added")
        
    
    def _convert_modules_to_dict(self) -> Dict[str, Dict[str, Any]]:
        """Convert dict of Modules to dict of dicts, using enum values for status."""
        module_dict = {}
        for module_id, module in self.modules.items():
            m = asdict(module)
            # Convert enum to string
            if isinstance(m.get("status"), ModuleStatus):
                m["status"] = m["status"].value
            module_dict[module_id] = m
        # self.logger.info(f"Converted modules to dict.")
        return module_dict


    def _ready_timeout_checker(self):
        while True:
            current_time = time.time()
            for module_id, module_data in self.modules.items():
                if module_data.status == ModuleStatus.READY or module_data.status == ModuleStatus.NOT_READY:
                    ready_time = module_data.ready_time
                    if current_time - ready_time > self.ready_timeout:
                        self.logger.info(f"Module {module_id} has timed out from READY/NOT_READY. Check again.")
                        self.modules[module_id].status = ModuleStatus.DEFAULT
                        self.broadcast_updated_modules()
            time.sleep(5)


    def network_notify_module_id_change(self, old_module_id, new_module_id):
        # Move the module data to the new key
        self.modules[new_module_id] = self.modules.pop(old_module_id)
        self.broadcast_updated_modules()


    def network_notify_module_ip_change(self, module_id, module_ip):
        # IP changed for module
        self.modules["module_id"].ip = module_ip
        self.broadcast_updated_modules()


    def communication_notify_module_update(self, module_id: str, module_data: dict):
        self.logger.info(f"Received update from Communication object concerning module {module_id}, with data {module_data}")
        self.logger.info("No implementation yet.")


    def notify_module_readiness_update(self, module_id: str, ready: bool, message: str):
        # self.logger.info(f"Received update concerning module {module_id}, with ready status {ready}")
        self.modules[module_id].ready_message = message
        if ready == True:
            self.modules[module_id].status = ModuleStatus.READY
            self.modules[module_id].ready_time = time.time() 
        else:
            self.modules[module_id].status = ModuleStatus.NOT_READY
        self.broadcast_updated_modules()


    def notify_module_online_update(self, module_id: str, online: bool):
        # self.logger.info(f"Received update concerning module {module_id}, online status {online}")
        has_changed = self.modules[module_id].online != online # Check if it changed

        self.modules[module_id].online = online # Set the boolean value of online in the modules rcecord
        if not online:
            self.modules[module_id].status = ModuleStatus.OFFLINE # If it's offline, also set it's status to OFFLINE.
        else:
            if has_changed:
                self.modules[module_id].status = ModuleStatus.DEFAULT # Module has just been marked back online, so flip it's status to "NOT READY"
                
        self.broadcast_updated_modules()


    def notify_recording_started(self, module_id:str, module_data: dict):
        self.logger.info(f"{module_id} started recording {module_data}")
        if module_data["recording"] == True:
            self.logger.info("Recording was true")
            self.modules[module_id].status = ModuleStatus.RECORDING
        else:
            self.logger.warning("Status start_recording received but recording param was False, some kind of error.") 
        self.broadcast_updated_modules()


    def notify_recording_stopped(self, module_id:str, module_data: dict):
        self.logger.info(f"{module_id} stopped recording {module_data}")
        self.modules[module_id].status = ModuleStatus.DEFAULT
        self.broadcast_updated_modules()


    def remove_module(self, module_id: str):
        if module_id in self.modules.keys():
            self.modules.pop(module_id)
            self.broadcast_updated_modules()


    def update_module_config(self, module_id: str, new_config: Dict):
        """
        Update configuration settings for existing modules. Called when a module returns response to get_config command.

        Args:
            module_id (str): The module_id which acts as key in self.modules
            configs (Dict): Current config values for the module.
        """
        if module_id in self.modules:
            module_entry = self.modules[module_id]
            old_config = module_entry.config
            module_entry.config = {**old_config, **new_config}
            self.logger.info(f"Updated config for {module_id}: {new_config}")
        else:
            # Add new module if it doesn't exist yet
            self.logger.warning(f"Received config update from unknown module {module_id} - attempting to add to module list")
            self.modules[module_id] = Module(
                online = True,
                status = ModuleStatus.DEFAULT,
                config = new_config
            )
        self._update_module_name(module_id)
        self.broadcast_updated_modules() # Broadcast that module configs have updated

    
    def _update_module_name(self, module_id: str):
        """Update module name based on config setting."""
        name = self.modules[module_id].config['module']['name']
        # Check if name is valid
        if name == "" or name is None:
            self.logger.info(f"Bad name received: {name}")
            self.modules[module_id].name = module_id
        else:
            self.modules[module_id].name = name
        
    
    def get_module_name(self, module_id: str):
        """Return module name for given module_id"""

        

    def update_module_configs(self, configs: Dict):
        """
        Update configuration settings for existing modules.

        Args:
            configs (Dict): A dictionary where keys are module IDs and
                            values are configuration dictionaries.
        """
        for module_id, new_config in configs.items():
            self.update_module_config(module_id, new_config)


    def broadcast_updated_modules(self) -> None:
        # self.logger.info(f"Updated module list: {self.modules.keys()}")
        module_dict = self._convert_modules_to_dict()
        self.facade.push_module_update_to_frontend(module_dict)


    def get_modules(self) -> Dict[str, Any]:
        """Getter function for self.modules - importantly, converts them to dict."""
        return self._convert_modules_to_dict()
    

    def get_modules_by_target(self, target: str) -> Dict[str, Any]:
        """Take a target (either "all", a group name, or a module_id) and return a dict of modules."""
        # Handle bad or empty target
        if target == "":
            self.logger.error("No targetsupplied to modules.get_modules_by_group()")
            return None

        # Handle all target
        if target.lower() == "all":
            return self.get_modules()
        
        modules_in_group = {}
        for module_id, module in self.modules.items():
            if module.group == target:
                self.logger.info(f"{module_id} is in {target}")
                modules_in_group[module_id] = module
        return modules_in_group

    
    def start(self):
        self.logger.info("Starting Modules manager")
        self.ready_timeout_thread.start()   