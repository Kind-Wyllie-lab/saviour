#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR Controller

This script serves as the main controller for the habitat system, providing:
- Precise time synchronisation (PTP master) for all connected modules
- Module discovery, monitoring, and health checks
- Recording session management and control
- Data collection and packaging in NWB format

Author: Andrew SG
Created: 11/11/2025
"""

import sys
import os
from dotenv import load_dotenv
load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import time
import datetime
import logging # for logging and debugging
from dataclasses import dataclass # to define Module dataclass
from typing import List, Dict, Any, Optional # for type hinting
import asyncio # for asyncio
from abc import ABC, abstractmethod

# Check if running under systemd
is_systemd = os.environ.get('INVOCATION_ID') is not None

# Setup logging ONCE for all modules
if is_systemd:
    # Under systemd, let it handle timestamps
    format_string = '%(levelname)s - %(name)s - %(message)s'
else:
    # When running directly, include timestamps
    format_string = '%(asctime)s - %(levelname)s - %(name)s - %(message)s'

# Setup logging ONCE for all additional classes that log
logging.basicConfig(
    level=logging.INFO,
    format=format_string
)   

# Networking and synchronization
import threading # for concurrent operations

# Local managers
from src.controller.network import Network
from src.controller.communication import Communication
from src.controller.health import Health
from src.controller.config import Config
from src.controller.ptp import PTP, PTPRole
from src.controller.web import Web
from src.controller.modules import Modules
from src.controller.facade import ControllerFacade
from src.controller.recording import Recording
from src.controller.export_queue import ExportQueue

# Habitat Controller Class
class Controller(ABC):
    """
    Base class for SAVIOUR controller devices.
    """
    def __init__(self):
        """Initialize the controller with default values

        Instantiates the following:
        - A logger
        - A config manager, which initially loads the config file and sets up the config object which the controller can use to get parameters
        - A network manager, which initially registers a zeroconf network and (passively, as part of zeroconf object) starts a thread to browse for module networks
        - A communication manager, which initially starts a thread to listen for status and data updates from modules
        - A PTP manager, which initally defines the ptp4l and phc2sys arguments based on the role of the controller and will later start a thread
        - A web manager, which hosts a flask based webapp GUI
        - A health monitor, which then has it's start monitoring method called to start a thread to monitor the health of the modules
        """

        # Setup logging
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Initializing managers")

        # Initialize config manager
        self.config = Config()

        self._setup_logging()
        
        # Control flags 
        self.is_running = True  # Add flag for listener thread

        # Managers
        self.network = Network(self.config) 
        self.network.on_module_discovered = self.on_module_discovered 
        self.network.on_module_removed = self.on_module_removed              
        self.communication = Communication(status_callback=self.handle_status_update)
        self.ptp = PTP(role=PTPRole.MASTER, config=self.config)
        self.web = Web(self.config)
        self.health = Health(self.config)
        self.modules = Modules()
        self.recording = Recording()
        self.export_queue = ExportQueue(self.config)
        self.facade = ControllerFacade(self)

        # Register facade/callbacks
        self.network.facade = self.facade
        self.health.facade = self.facade
        self.communication.facade = self.facade
        self.web.facade = self.facade
        self.modules.facade = self.facade
        self.recording.facade = self.facade
        self.export_queue.facade = self.facade
        self.config.on_controller_config_change = self.on_controller_config_change

        # Controller state
        self.start_time = None

        # Start health monitoring
        self.logger.info("Starting health monitoring thread")
        self.health.start_monitoring()


    def on_controller_config_change(self, updated_keys: Optional[list[str]]) -> None:
        self.logger.info(f"Received notification that controller config changed, calling configure_controller() with keys {updated_keys}")
        self.configure_controller(updated_keys)


    @abstractmethod
    def configure_controller(self, updated_keys: Optional[list[str]]):
        """Gets called when controller specific configuration changes - allows controllers to update their specific settings when they change"""
        self.logger.warning("No implementation provided for abstract method configure_controller")
    

    def _register_special_socket_events(self, socketio):
        """
        Use this in __init__() to add new socketio event handlers.

        Example:
        @socketio.on("special_event")
        def handle_special_event(data):
            print("Received special event:", data)
            socketio.emit("special_response", {"status": "ok"})
        """
        raise NotImplementedError


    def _remove_module(self, module_id: str):
        self.logger.info(f"Removing {module_id}")
        self.modules.remove_module(module_id)
        self.health.remove_module(module_id)
        self.logger.info(f"New list: {self.modules.get_modules().keys()}")
        self.communication.send_command(module_id, "shutdown", {})


    def handle_status_update(self, topic: str, data: str):
        """Handle a status update from a module"""
        module_id = topic.split('/')[1] # get module id from topic
        try:
            import json
            status_data = json.loads(data)
            status_type = status_data.get('type', 'unknown')
            self.web.handle_module_status(module_id, status_data) # Whatever web related functionality related to status update, process it # TODO: remove this
            # Any message from a module proves it is reachable — refresh the
            # heartbeat timer so busy modules (e.g. mid-recording) are not
            # incorrectly declared offline just because they missed a periodic send.
            self.health.touch_heartbeat(module_id)
            match status_type:
                case 'heartbeat':
                    self.modules.check_status(module_id, status_data)
                    self.health.update_module_health(module_id, status_data)
                    # If we have no config for this module yet (e.g. it restarted and
                    # the initial get_config was sent before its ZMQ socket was ready),
                    # request it now that we know ZMQ comms are working.
                    if not self.modules.has_config(module_id):
                        self.logger.info(f"No config stored for {module_id}, requesting via heartbeat trigger")
                        self.communication.send_command(module_id, "get_config", {})

                case 'recordings_list':
                    self.logger.info(f"Recordings list received from {module_id}")

                case 'status':
                    self.logger.info(f"{module_id} sent status type message likely response to get status command")
                    self.modules.check_status(module_id, status_data)
                    self.health.update_module_health(module_id, status_data)

                case 'export_ready':
                    export_path = status_data.get('export_path', '')
                    file_count = status_data.get('file_count', 0)
                    self.logger.info(f"{module_id} has {file_count} file(s) ready to export → {export_path}")
                    self.facade.enqueue_export(module_id, export_path)

                case 'export_complete':
                    export_path = status_data.get('export_path', '')
                    self.logger.info(f"{module_id} completed export of {export_path}")
                    self.facade.export_complete(module_id, export_path)

                case 'export_failed':
                    export_path = status_data.get('export_path', '')
                    self.logger.warning(f"{module_id} failed to export {export_path}")
                    self.facade.export_failed(module_id, export_path)

                case 'recording_started':
                    self.logger.info(f"{module_id} has started recording")
                    self.modules.notify_recording_started(module_id, status_data)

                case 'recording_stopped':
                    self.logger.info(f"{module_id} has stopped recording")
                    self.modules.notify_recording_stopped(module_id, status_data)
                    self.facade.module_stopped(module_id)

                case 'cmd_ack':
                    command = status_data.get('command', 'unknown')
                    result  = status_data.get('result', 'unknown')
                    self.logger.debug(f"{module_id} ack'd '{command}': {result}")

                    if command in ('get_config', 'reset_config'):
                        config_data = status_data.get('config', {})
                        if config_data:
                            self.logger.info(f"Config received from {module_id} via {command}")
                            self.modules.received_module_config(module_id, config_data)

                    elif command == 'set_config':
                        if result == 'success':
                            config_data = status_data.get('config')
                            if config_data:
                                self.modules.received_module_config(module_id, config_data)
                            else:
                                self.communication.send_command(module_id, "get_config", {})
                        else:
                            self.logger.error(f"set_config failed for {module_id}: {result}")
                            self.modules.handle_set_config_failed(module_id, result)

                    elif command == 'validate_readiness':
                        ready = status_data.get('ready', False)
                        message = status_data.get('message', 'No message...')
                        self.logger.info(f"Readiness validation response from {module_id}: {'ready' if ready else 'not ready'}")
                        if not ready:
                            self.logger.info(f"Full message from non-ready module: {message}")
                        self.modules.notify_module_readiness_update(module_id, ready, message)

                    elif command == 'get_health':
                        self.logger.info(f"Health response received from {module_id}")
                        self.modules.check_status(module_id, status_data)
                        self.health.update_module_health(module_id, status_data)
                        self.web.broadcast_module_health()

                case 'recording_start_failed':
                    error = status_data.get('error', 'unknown')
                    if error == "Already recording":
                        # module_back_online re-issued start_recording to a module
                        # that was already mid-recording — treat it as a confirmation
                        # rather than a failure so the session stays ACTIVE.
                        self.logger.info(
                            f"{module_id} was already recording — treating as recording_started"
                        )
                        self.modules.notify_recording_started(module_id, {"recording": True})
                    else:
                        self.logger.warning(f"{module_id} failed to start recording: {error}")
                        self.modules.notify_recording_stopped(module_id, status_data)

                case 'recording_stop_failed':
                    if status_data.get("error") == "Not recording":
                        self.modules.notify_recording_stopped(module_id, status_data)

                case "error":
                    message = status_data.get('message', 'No message...')
                    self.logger.warning(f"Received error from {module_id}: {message}")

                case _:
                    self.logger.info(f"Unknown status type from {module_id}: {status_type}")
        except Exception as e:
            self.logger.error(f"Error parsing status data for module {module_id}: {e}")


    def on_module_status_change(self, module_id: str, status: str):
        """Callback for when module status changes (online/offline)

        Args:
            module_id: String representing the module
            status: may be "online" or "offline"
        """
        self.logger.info("On module status change called")
        if status == "online":
            online = True
            self.facade.module_back_online(module_id)
            # Module came back from offline — its cached config may be from a
            # previous run. Invalidate it so the frontend shows fresh data.
            self.modules.invalidate_config(module_id)
            self.logger.info(f"Requesting fresh config from {module_id} after coming back online")
            self.communication.send_command(module_id, "get_config", {})
        elif status == "offline":
            online = False
            self.facade.module_offline(module_id)

        self.modules.notify_module_online_update(module_id, online)


    def _setup_logging(self): 
        # Add logging file handler if none exists
        if not self.config.get("logging.file_logging", False):
            return
            
        if not self.logger.handlers:
            # Add file handler for persistent logging (useful when running as systemd network)
            try:
                # Create logs directory if it doesn't exist
                log_dir = self.config.get("logging.directory", "/var/log/habitat")
                os.makedirs(log_dir, exist_ok=True)
                
                # Generate log filename with module info
                log_filename = f"controller.log"
                log_filepath = os.path.join(log_dir, log_filename)
                
                # Get config values for rotation
                max_bytes = self.config.get("logging.max_file_size_mb", 10) * 1024 * 1024
                backup_count = self.config.get("logging.backup_count", 5)
                
                # Add file handler with rotation
                from logging.handlers import RotatingFileHandler
                file_handler = RotatingFileHandler(
                    log_filepath,
                    maxBytes=max_bytes,
                    backupCount=backup_count
                )
                file_handler.setLevel(logging.INFO)
                self.logger.addHandler(file_handler)
                
                self.logger.info(f"File logging enabled: {log_filepath}")
                self.logger.info(f"Log rotation: max {max_bytes//(1024*1024)}MB, keep {backup_count} backups")

            except Exception as e:
                # If file logging fails, log the error but don't crash
                self.logger.warning(f"Failed to setup file logging: {e}")
                self.logger.info("Continuing with console logging only")


    def stop(self) -> bool:
        """Stop the controller and clean up resources"""
        self.logger.info("Stopping controller...")
        
        try:
            # Stop PTP
            self.logger.info("Stopping PTP manager")
            self.ptp.stop()

            # Stop all threads by setting flags
            self.is_running = False
            
            # Stop health monitoring
            self.logger.info("Stopping health monitoring")
            self.health.stop_monitoring()
            
            # Clean up health monitoring
            self.logger.info("Cleaning up module health tracking")
            self.health.clear_all_health()
            
            # Clean up network manager
            self.logger.info("Cleaning up network manager")
            self.network.cleanup()
            
            # Clean up communication manager
            self.logger.info("Cleaning up communication manager")
            self.communication.cleanup()
            
            # Clean up database manager
            self.logger.info("Cleaning up database manager")
            # self.database.cleanup()

            # Give modules time to detect the controller is gone
            time.sleep(1)
            
            self.logger.info("Controller stopped successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping controller: {e}")
            return False


    # Main methods
    def start(self) -> bool:
        """
        Start the controller.
        
        Returns:
            bool: True if the controller started successfully, False otherwise.
            
        Starts the following:
        - A PTP manager, which starts a thread to run ptp4l and phc2sys
        - A interface manager, which receives input from CLI or web interface and handles it (and may also be used to send commands to modules)
        - A forever loop to keep the main controller thread alive
        """
        self.logger.info("Starting controller")
        self.start_time = time.time()

        # Register controller network for module discovery
        self.logger.info("Registering controller service...")
        if not self.network.register_service():
            self.logger.error("Failed to register controller network")
            return False
        self.logger.info("Controller network registered successfully")

        # Start PTP
        self.logger.info("Starting PTP manager...")
        self.ptp.start() # This will start a thread to run ptp4l and phc2sys

        # Start the web interface
        if self.web:
            self.logger.info("Starting web interface")
            self.web.start() # This will start a thread to serve a webapp and listen for commands from user
            
            # Update web interface with initial module list
            if hasattr(self, 'modules'):
                self.web.update_modules(self.modules.get_modules())


        # Start the modules manager
        self.modules.start()
        
        # Keep the main thread alive
        try: 
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received. Stopping controller...")
            self.stop()
            return False
        except Exception as e:
            self.logger.error(f"Error in main thread: {e}")
            self.stop()
            return False
        
        return True
        

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        Get a configuration value
        
        Args:
            key: Configuration key path (e.g., "controller.max_buffer_size")
            default: Default value if key doesn't exist
            
        Returns:
            Configuration value
        """
        return self.config.get(key, default)
        

    def set_config(self, key: str, value: Any, persist: bool = False) -> bool:
        """
        Set a configuration value
        
        Args:
            key: Configuration key path (e.g., "controller.max_buffer_size")
            value: Value to set
            persist: Whether to save to config file
            
        Returns:
            True if successful
        """

        # Update in config manager
        return self.config.set(key, value, persist) 


    def on_module_discovered(self, module):
        """Callback for when a new module is discovered"""
        self.logger.info(f"Module discovered: {module.id}")
        
        # Add module to health monitor with initial offline status
        # This allows the health monitor to track the module even before it sends heartbeat
        initial_health_data = {
            'timestamp': time.time(),
            'status': 'online', 
            'cpu_temp': 0,
            'cpu_usage': 0,
            'memory_usage': 0,
            'uptime': 0,
            'disk_space': 0
        }
        self.health.update_module_health(module.id, initial_health_data)
        
        # Update web interface
        if hasattr(self, 'web'):
            self.web.update_modules(self.modules.get_modules())
            self.web.notify_module_update()


    def on_module_removed(self, module):
        """Callback for when a module network is removed"""
        self.web.notify_module_update()


    def get_module_config(self, module_id: str):
        self.communication.send_command(module_id, "get_config", {})


    def get_module_configs(self):
        """Get the module configuration data for online modules only"""
        # Request config from all modules - refresh the config stored on controller
        self.logger.info(f"Sending get_config command to all modules")
        self.communication.send_command("all", "get_config", {})


    def get_export_credentials(self) -> dict:
        """
        Read the Samba credentials written by switch_role.sh and return them
        alongside this controller's IP — ready to push to modules as
        set_export_config params.
        Returns an empty dict if the credentials file is missing.
        """
        creds_path = "/etc/saviour/samba_credentials"
        try:
            username = ""
            password = ""
            with open(creds_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("username="):
                        username = line.split("=", 1)[1]
                    elif line.startswith("password="):
                        password = line.split("=", 1)[1]
            return {
                "share_ip": self.network.ip,
                "share_username": username,
                "share_password": password,
            }
        except FileNotFoundError:
            self.logger.warning(
                f"Samba credentials file not found at {creds_path} — "
                "module export config will not be pushed automatically"
            )
            return {}
        except Exception as e:
            self.logger.error(f"Failed to read export credentials: {e}")
            return {}


    def get_samba_info(self):
        """Get Samba share information from configuration"""
        try:
            # Get controller IP address from service manager (already detected and stored)
            controller_ip = self.network.ip
            
            # Get Samba configuration from config
            samba_config = {
                'share_name': self.config.get('samba.share_name', 'controller_share'),
                'username': self.config.get('samba.username', 'pi'),
                'password': self.config.get('samba.password', 'saviour'),
                'share_path': f'\\\\{controller_ip}\\{self.config.get("samba.share_name", "controller_share")}',
                'controller_ip': controller_ip
            }
            
            self.logger.info(f"Returning Samba info: {samba_config}")
            return samba_config
        except Exception as e:
            self.logger.error(f"Error getting Samba info: {e}")
            return {
                'share_name': 'controller_share',
                'username': 'pi',
                'password': 'saviour',
                'share_path': '\\\\192.168.1.1\\controller_share',
                'controller_ip': '192.168.1.1'
            }


if __name__ == "__main__":
    controller = Controller()
    try:
        # Start the main loop
        controller.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        controller.stop()
    except Exception as e:
        print(f"\nError: {e}")
        controller.stop()
