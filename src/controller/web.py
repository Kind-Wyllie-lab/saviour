#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Web Interface

Handles user interaction with the habitat controller, including:
- Web based GUI
- Command parsing and execution
- Help system and module listing

Author: Andrew SG
Created: ?
"""


import logging
import time
from flask import Flask, render_template, jsonify, request, send_from_directory
from flask_socketio import SocketIO
from typing import Any
import threading
import json
import os
from datetime import datetime
from pathlib import Path
from abc import ABC


from src.controller.config import Config


class Web(ABC):
    def __init__(self, config: Config):
        self.logger = logging.getLogger(__name__)
        self.config = config

        # Get the port from the config
        self.port = self.config.get("interface.web_interface_port")

        # Flask setup
        self.app = Flask(__name__, static_folder="frontend/dist", static_url_path="/")
        self.socketio = SocketIO(self.app, host="0.0.0.0", cors_allowed_origins="*", async_mode='threading')
        
        # Callbacks
        self.callbacks = {} # An empty dict which will later be assigned callback functions

        # Default experiment metadata
        self.experiment_metadata = {
            'experiment': 'demo',
            'rat_id': '001',
            'strain': 'Wistar',
            'batch': 'B1',
            'stage': 'habituation',
            'trial': '1'
        }
        self.current_experiment_name = self._generate_experiment_name() # To be constructed from metadata, or overriden

        # Register routes and webhooks        
        self._register_routes() 
        self._register_socketio_events() 

        # Store module readiness state in memory 
        self.module_readiness = {}  # {module_id: {'ready': bool, 'timestamp': float, 'checks': dict, 'error': str}}

        self.rest_api = False
        if self.rest_api == True:
            self._register_rest_api_routes()

        # Running flag
        self._running = False

        # Set up paths
        self.habitat_share_dir = Path("/home/pi/controller_share")
    
    
    def _generate_experiment_name(self) -> str:
        """Generate experiment name from metadata, skipping empty fields."""
        md = self.experiment_metadata
        parts = []

        # Iterate through metadata keys in desired order
        for key in ['experiment', 'rat_id', 'strain', 'batch', 'stage', 'trial']:
            value = str(md.get(key, "")).strip()
            if value:  # Only append non-empty strings
                parts.append(value)

        # Join non-empty parts with underscores
        name = "_".join(parts)

        if name == "":
            name = "NO_NAME"

        return name


    def register_callbacks(self, callbacks={}):
        """Register callbacks based on a dict.
        Should include:
            "get_modules"
            "get_ptp_history"
            "send_command"
            "get_module_health"
        """
        self.callbacks.update(callbacks)


    def notify_module_update(self):
        """Function that can be used externally by controller.py to notify frontend when modules updated"""
        self.logger.info(f"Getting list of modules and sending emitting 'module_update'")
        if self.callbacks["get_modules"]:
            modules = self.callbacks["get_modules"]()
            self.logger.info(f"Got {len(modules)} modules from callback")
            
            # Use socketio.emit instead of individual handlers to ensure proper context
            self.socketio.emit('module_update', {"modules": modules})
            self.logger.info(f"Sent module update to all clients")


    def push_module_update(self, modules: dict):
        self.logger.info(f"Pushing update module list to frontend: {modules.keys()}")
        self.socketio.emit('modules_update', modules)


    def _register_routes(self):      
        # Serve React app
        @self.app.route("/", defaults={"path": ""})
        @self.app.route("/<path>")
        def serve(path):
            self.logger.info(f"Received request to access {path}")
            static_folder = self.app.static_folder
            file_path = os.path.join(static_folder, path)

            if os.path.exists(file_path) and not os.path.isdir(file_path):
                # If it's a real file, serve it
                return send_from_directory(static_folder, path)

            return send_from_directory(self.app.static_folder, "index.html")


    def _register_socketio_events(self):
        # WebSocket event handlers - for use by the web interface
        @self.socketio.on('connect')
        def handle_connect(auth=None):
            self.logger.info(f"handle_connect called with auth: {auth}")
            client_ip = request.remote_addr
            self.socketio.emit('client_ip', client_ip)
            self.logger.info(f"Client connected")
            
            # Send initial module list
            modules = self.callbacks["get_modules"]()
            self.logger.info(f"Page load get_modules() returned: {modules}, sending {len(modules)} modules to new client")
            self.socketio.emit('module_update', {"modules": modules})
            
            # Send current experiment name to new client
            if self.current_experiment_name:
                self.socketio.emit('experiment_name_update', {"experiment_name": self.current_experiment_name})
                self.logger.info(f"Sent current experiment name to new client: {self.current_experiment_name}")

        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.logger.info(f"Client disconnected")

        @self.socketio.on('send_command')
        def handle_command(data):
            """
            Handle command from frontend.
            Command will be formatted as command_name param1=value1 param2=value2 etc
            For example, start_streaming client_ip=192.168.0.55 port=8080
            Communication manager will format this as cmd/<module_id> <command_name> <param1=value1> <param2=value2> etc 
            
            Args:
                command (json): The command received from the frontend. Should contain type, module_id (may be "all" or a specific module), and params field
            """
            try:
                command_type = data.get('type')
                module_id = data.get('module_id')
                params = data.get('params', {})
                
                self.logger.info(f"Received command via WebSocket: {data}")
                
                # Format command with parameters
                command = command_type 
                if params:
                    if command_type == 'start_streaming':
                        # For streaming, we need client_ip and port
                        client_ip = params.get('client_ip')
                        port = params.get('port', 8080)  # Default to 8080 if not specified
                        command = f"{command_type}"
                        params = {"client_ip": client_ip, "port": port}
                    if command_type == 'start_recording':
                        # Append timestamp
                        params["experiment_name"] += ("_" + datetime.now().strftime('%Y%m%d_%H%M%S'))
                    else:
                        # For other commands format as cmd/module_id command_type {params}
                        command = command_type
                
                # Send command to module
                if self.callbacks["send_command"]:
                    self.callbacks["send_command"](module_id, command, params)
                    self.logger.info(f"Command sent successfully: {command} to module {module_id} with params {params}")
                    
                    # If this was a clear_recordings command, request updated list
                    if command_type == 'clear_recordings':
                        # Wait a short moment for the deletion to complete
                        self.socketio.sleep(0.5)
                        # Request updated recordings list
                        if self.callbacks["send_command"]:
                            self.callbacks["send_command"](module_id, 'list_recordings', {})
                            self.logger.info(f"Requested updated recordings list after clear")
                else:
                    self.logger.error("No command handler registered")
                    
            except Exception as e:
                self.logger.error(f"Error handling command: {str(e)}")
                self.socketio.emit('error', {'message': str(e)})

        """ Get Modules """
        @self.socketio.on('get_modules')
        def handle_module_update():
            """Handle request for module data"""
            self.logger.info(f"Frontend called 'get_modules'")
            
            # Get current modules from callback
            modules = self.callbacks["get_modules"]()
            self.logger.info(f"Got {len(modules)} modules from callback")
            
            # Send module update to all clients
            self.socketio.emit('modules_update', modules)
            self.logger.info(f"Sent module update to all clients: {modules}")

        @self.socketio.on('module_status') # TODO: Does this make sense? Frontend shouldn't be sending module status
        def handle_module_status(data):
            """Handle module status update"""
            self.logger.info("IN WEB HANDLE_MODULE_STATUS")
            try:
                # self.logger.info(f"Received module status: {data}")
                if not isinstance(data, dict):
                    raise ValueError("Status data must be a dictionary")
                
                module_id = data.get('module_id')
                status = data.get('status')
                
                if not module_id or not status:
                    raise ValueError("Status must include 'module_id' and 'status'")
                
                # Handle recordings list response
                if status.get('type') == 'recordings_list':
                    self.logger.info(f"Broadcasting module recordings for module {module_id}")
                    module_recordings = status.get('recordings', [])
                    
                    # Send individual module recordings response
                    self.socketio.emit('module_recordings', {
                        'module_id': module_id,
                        'recordings': module_recordings
                    })
                    return
                
                # Handle export complete response
                if status.get('type') == 'export_complete':
                    self.logger.info(f"Broadcasting export complete for module {module_id}")
                    self.socketio.emit('export_complete', {
                        'module_id': module_id,
                        'success': status.get('success', False),
                        'error': status.get('error'),
                        'filename': status.get('filename')
                    })
                    return
                
                # Handle recording started/stopped status
                if status.get('type') in ['recording_started', 'recording_stopped']:
                    self.logger.info(f"Broadcasting recording status for module {module_id}")
                    self.socketio.emit('module_status', {
                        'module_id': module_id,
                        'status': status
                    })
                    return
                
                # For heartbeat and other status types
                if 'recording_status' not in status:
                    self.logger.warning("Recording status not in received status update.")
                
                # Broadcast status to all clients
                self.socketio.emit('module_status', {
                    'module_id': module_id,
                    'status': status
                })
                
            except Exception as e:
                self.logger.error(f"Error handling module status: {str(e)}")
                # Optionally emit error back to client
                # self.socketio.emit('error', {'message': str(e)})

        """ Experiment Metadata """
        # Experiment metadata
        @self.socketio.on('update_experiment_metadata')
        def handle_update_experiment_metadata(data):
            """Handle experiment metadata updates from frontend"""
            self.logger.info(f"Received experiment metadata update: {data}")

            # Update stored metadata
            if 'experiment' in data:
                self.experiment_metadata['experiment'] = data['experiment']
            if 'rat_id' in data:
                self.experiment_metadata['rat_id'] = data['rat_id']
            if 'strain' in data:
                self.experiment_metadata['strain'] = data['strain']
            if 'batch' in data:
                self.experiment_metadata['batch'] = data['batch']
            if 'stage' in data:
                self.experiment_metadata['stage'] = data['stage']
            if 'trial' in data:
                self.experiment_metadata['trial'] = data['trial']
            
            self.logger.info(f"Updated experiment metadata: {self.experiment_metadata}")

            # Rebuild experiment name
            self.current_experiment_name = self._generate_experiment_name()
            self.logger.info(f"Generated new experiment name: {self.current_experiment_name}")
            
            # Send confirmation back to client
            self.socketio.emit('experiment_metadata_updated', {
                'status': 'success',
                'metadata': self.experiment_metadata,
                'experiment_name': self.current_experiment_name
            })
            self.logger.info(f"Sent experiment metadata update confirmation")

        @self.socketio.on('get_experiment_metadata')
        def handle_get_experiment_metadata(data=None):
            """Handle request for experiment metadata from frontend"""
            self.logger.info(f"Client requested experiment metadata")
            
            # Send current metadata to client
            self.socketio.emit('experiment_metadata_response', {
                'status': 'success',
                'metadata': self.experiment_metadata,
                'experiment_name': self.current_experiment_name
            })
            self.logger.info(f"Sent experiment metadata to client")

        """ Settings Page  """
        @self.socketio.on('get_module_configs')
        def handle_get_module_configs(data=None):
            """Handle request for module configuration data"""
            self.logger.info(f"Get module configs called")
            if "get_module_configs" in self.callbacks:
                # Get the current module configs
                self.callbacks["get_module_configs"]()
            else:
                self.logger.warning(f"get_module_configs callback not available")

        @self.socketio.on('save_module_config')
        def handle_save_module_config(data):
            """Handle save module config from frontend"""
            self.logger.info(f"Received request to save config to module {data['id']} with data {data['config']}")
            if "send_command" in self.callbacks:
                # Format command with parameters
                command = "set_config"
                # Extract params from the data
                params = data.get("config", {})
                
                # Send the config update command to all modules
                self.callbacks["send_command"](data['id'], command, params)
            else:
                self.logger.error("No 'send command' callback registered")


        """Controller Level Config"""
        @self.socketio.on('get_controller_config')
        def handle_get_controller_config(data=None):
            self.logger.info("Received request for controller config")
            config = self.config.get_all()
            self.socketio.emit("controller_config_response", {
                "config": config
            })


        @self.socketio.on('save_controller_config')
        def handle_save_controller_config(data):
            self.logger.info("Saving controller config")
            self.api.set_config(data.get("config", {}))
            self.socketio.emit("controller_config_response", {
                "config": self.api.get_controller_config()
            })


        """Viewing exported recordings on the share"""
        @self.socketio.on('get_exported_recordings')
        def handle_get_exported_recordings():
            """Handle request for exported recordings"""
            try:
                recordings = self.get_exported_recordings()
                self.socketio.emit('exported_recordings_list', {
                    'exported_recordings': recordings
                })
            except Exception as e:
                self.logger.error(f"Error getting exported recordings: {str(e)}")
                self.socketio.emit('exported_recordings_list', {
                    'exported_recordings': [],
                    'error': str(e)
                })

        @self.socketio.on('get_module_health')
        def handle_get_module_health():
            """Handle request for module health status"""
            try:
                if self.callbacks["get_module_health"]:
                    health = self.callbacks["get_module_health"]()
                    self.socketio.emit('module_health_update', {
                        'module_health': health
                    })
                else:
                    self.socketio.emit('module_health_update', {
                        'module_health': {},
                        'error': 'Module health callback not available'
                    })
            except Exception as e:
                self.logger.error(f"Error getting module health: {str(e)}")
                self.socketio.emit('module_health_update', {
                    'module_health': {},
                    'error': str(e)
                })

        """ Debug """
        @self.socketio.on('get_debug_data')
        def handle_get_debug_info():
            self.logger.info(f"Received request for debug data")
            debug_data = {}
            debug_data["modules"] = self.callbacks["get_modules"]()
            debug_data["module_health"] = self.callbacks["get_module_health"]()
            debug_data["discovered_modules"] = self.callbacks["get_discovered_modules"]()
            debug_data["module_configs"] = self.callbacks["get_module_configs"]()
            self.socketio.emit("debug_data", debug_data)

        """ Login """
        @self.socketio.on("login")
        def handle_login(data):
            username = data.get("username")
            password = data.get("password")

            # Replace with secure password check
            if username == "admin" and password == "secret":
                self.socketio.emit("login_success", room=request.sid)
            else:
                self.socketio.emit("login_error", "Wrong username or password", room=request.sid)

        """ Commands and utility """
        @self.socketio.on('remove_module')
        def handle_remove_module(module):
            self.logger.info(f"Received request to remove module: {module['id']}")
            self.callbacks["remove_module"](module['id'])


        


    def update_modules(self, modules: list):
        """Update the list of modules from the controller service manager"""
        self._modules = modules


    def update_module_readiness(self, module_id: str, ready_status: dict):
        """Update module readiness state and broadcast to all clients"""
        import time
        
        # Store the readiness status with timestamp
        self.module_readiness[module_id] = {
            'ready': ready_status.get('ready', False),
            'timestamp': time.time(),
            'checks': ready_status.get('checks', {}),
            'error': ready_status.get('error')
        }
        
        self.logger.info(f"Updated readiness for {module_id}: {'ready' if ready_status.get('ready') else 'not ready'}")
        
        # Broadcast to all connected clients
        self.socketio.emit('update_module_readiness', {
            'module_id': module_id,
            'ready': ready_status.get('ready', False),
            'timestamp': self.module_readiness[module_id]['timestamp'],
            'checks': ready_status.get('checks', {}),
            'error': ready_status.get('error')
        })


    def start(self):
        """Start the web interface in a separate thread"""
        if not self._running:
            self.logger.info(f"Starting web interface on port {self.port}")
            self._running = True
            self.web_thread = threading.Thread(
                target=self._run_server,
                daemon=True
            )
            self.web_thread.start()
            return self.web_thread


    def _run_server(self):
        """Internal method to run the Flask server"""
        self.socketio.run(self.app, host='0.0.0.0', port=self.port, debug=False, allow_unsafe_werkzeug=True)


    def stop(self):
        """Stop the web interface"""
        if self._running:
            self._running = False
            self.socketio.stop()


    def list_modules(self):
        """List all discovered modules"""
        self.logger.info("Listing modules")
        modules = self.callbacks["get_modules"]()
        return jsonify({"modules": modules})


    def get_exported_recordings(self):
        """Get list of exported recordings from controller share and NAS directories"""
        recordings = []
        
        # Get controller share recordings
        if self.habitat_share_dir.exists():
            for file in self.habitat_share_dir.glob('**/*'):
                if file.is_file() and file.suffix in ['.mp4', '.txt']:
                    recordings.append({
                        'filename': f"controller/{str(file.relative_to(self.habitat_share_dir))}",
                        'size': file.stat().st_size,
                        'created': datetime.fromtimestamp(file.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                        'is_exported': True,
                        'destination': 'controller'
                    })
        
        # Get NAS recordings (if mounted)
        nas_recordings = self.get_nas_recordings()
        recordings.extend(nas_recordings)
        
        return recordings


    def get_nas_recordings(self):
        """Get list of exported recordings from NAS"""
        recordings = []
        nas_mount_point = Path("/mnt/nas")
        
        self.logger.info(f"Scanning NAS for recordings...")
        
        # Try to mount NAS if not already mounted
        if not nas_mount_point.exists() or not nas_mount_point.is_mount():
            self.logger.info(f"NAS not mounted, attempting to mount...")
            if not self.mount_nas():
                self.logger.error(f"Failed to mount NAS, returning empty list")
                return recordings  # Return empty list if mounting failed
        
        self.logger.info(f"NAS is mounted at {nas_mount_point}")
        
        # Check what's in the root NAS directory
        if nas_mount_point.exists():
            root_contents = list(nas_mount_point.iterdir())
            self.logger.info(f"NAS root contents: {[item.name for item in root_contents]}")
            
            # Look specifically for export directories
            export_dirs = [item for item in root_contents if item.is_dir() and item.name.startswith('export_')]
            self.logger.info(f"Found export directories: {[item.name for item in export_dirs]}")
        else:
            self.logger.error(f"NAS mount point does not exist: {nas_mount_point}")
            return recordings
        
        # Scan multiple directories for recordings
        directories_to_scan = ["recordings", "videos", "ttl"]
        
        for dir_name in directories_to_scan:
            scan_path = nas_mount_point / dir_name
            self.logger.info(f"Looking for recordings in: {scan_path}")
            
            if scan_path.exists():
                self.logger.info(f"{dir_name} directory exists, scanning for files...")
                for file in scan_path.glob('**/*'):
                    self.logger.info(f"Found file: {file} (suffix: {file.suffix})")
                    if file.is_file() and file.suffix in ['.mp4', '.txt']:
                        self.logger.info(f"Adding file to recordings list: {file}")
                        recordings.append({
                            'filename': f"nas/{dir_name}/{str(file.relative_to(scan_path))}",
                            'size': file.stat().st_size,
                            'created': datetime.fromtimestamp(file.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                            'is_exported': True,
                            'destination': 'nas'
                        })
            else:
                self.logger.info(f"{dir_name} directory does not exist: {scan_path}")
        
        # Also scan for export directories (like export_20250624_220253) in the root
        self.logger.info(f"Scanning for export directories in root...")
        for item in nas_mount_point.iterdir():
            self.logger.info(f"Checking item: {item.name} (is_dir: {item.is_dir()}, starts_with_export: {item.name.startswith('export_')})")
            if item.is_dir() and item.name.startswith('export_'):
                self.logger.info(f"Found export directory: {item}")
                for file in item.glob('**/*'):
                    self.logger.info(f"Found file in export directory: {file} (suffix: {file.suffix})")
                    if file.is_file() and file.suffix in ['.mp4', '.txt']:
                        self.logger.info(f"Adding export file to recordings list: {file}")
                        recordings.append({
                            'filename': f"nas/{item.name}/{str(file.relative_to(item))}",
                            'size': file.stat().st_size,
                            'created': datetime.fromtimestamp(file.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                            'is_exported': True,
                            'destination': 'nas'
                        })
        
        self.logger.info(f"Found {len(recordings)} NAS recordings")
        return recordings


    def mount_nas(self):
        """Mount the NAS share"""
        try:
            import subprocess
            
            # NAS configuration - updated to match working module_export_manager implementation
            nas_ip = self.config.get("nas.ip")
            share_path = self.config.get("nas.share_path")
            username = self.config.get("nas.username")
            password = self.config.get("nas.password")
            mount_point = self.config.get("nas.local_mount")
            
            # Create mount point if it doesn't exist
            mount_path = Path(mount_point)
            mount_path.mkdir(exist_ok=True)
            
            # Unmount if already mounted
            if mount_path.is_mount():
                subprocess.run(['sudo', 'umount', mount_point], check=True)
            
            # Mount the NAS share - matching module_export_manager implementation
            mount_cmd = [
                'sudo', 'mount', '-t', 'cifs',
                f'//{nas_ip}/{share_path}',
                mount_point,
                '-o', f'username={username},password={password}'
            ]
            
            result = subprocess.run(mount_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                self.logger.error(f"Failed to mount NAS: {result.stderr}")
                return False
            
            self.logger.info(f"Successfully mounted NAS at {mount_point}")
            return True
            
        except Exception as e:
            self.logger.error(f"NAS mount failed: {str(e)}")
            return False


    # @abstractmethod # TODO: Should web be an abstract method?
    def handle_special_module_status(self, module_id, status):
        """To be overriden by rig specific functionality""" 
        pass

    def handle_module_status(self, module_id, status):
        """Handle status update from a module and emit to frontend"""
        try:
            # Ensure status has required fields
            if not isinstance(status, dict):
                raise ValueError("Status must be a dictionary")

            status_type = status.get('type')
            if not status_type:
                self.logger.warning(f"Bad status type: {status}")

            match status_type:  
                # Handle recordings list response
                case 'recordings_list':
                    self.logger.info(f"Broadcasting module recordings for module {module_id}")
                    module_recordings = status.get('recordings', [])
                    
                    # Send individual module recordings response
                    self.socketio.emit('module_recordings', {
                        'module_id': module_id,
                        'recordings': module_recordings
                    })

                # Handle export complete response
                case 'export_complete':
                    self.logger.info(f"Broadcasting export complete for module {module_id}")
                    self.socketio.emit('export_complete', {
                        'module_id': module_id,
                        'success': status.get('success', False),
                        'error': status.get('error'),
                        'filename': status.get('filename')
                    })

                # Handle recording started/stopped status
                case ('recording_started' | 'recording_stopped'):
                    self.logger.info(f"Broadcasting recording status for module {module_id}")
                    self.socketio.emit('module_status', {
                        'module_id': module_id,
                        'status': status
                    })

                case _:              
                    was_special_status = self.handle_special_module_status(module_id, status)
                    if not was_special_status:
                        self.logger.warning(f"No logic for {status} from {module_id}")
        except Exception as e:
            self.logger.error(f"Error handling module status: {str(e)}")


    def _register_rest_api_routes(self):
        """
        REST API endpoints - for use by external services e.g. a Matlab script running an experiment that wants to start recordings
        """
        @self.app.route('/api/list_modules', methods=['GET'])
        def list_modules():
            self.logger.info(f"/api/list_modules endpoint called. Listing modules")
            modules = self.callbacks["get_modules"]()
            self.logger.info(f"Found {len(modules)} modules")
            return jsonify({"modules": modules})

        @self.app.route('/api/ptp_history', methods=['GET'])
        def ptp_history():
            """Get PTP history for all modules"""
            self.logger.info(f"/api/ptp_history endpoint called. Getting PTP history")
            if self.callbacks["get_ptp_history"]:
                history = self.callbacks["get_ptp_history"]()
                self.logger.info(f"Got PTP history for {len(history)} modules")
                return jsonify(history)
            return jsonify({})

        @self.app.route('/api/send_command', methods=['POST'])
        def send_command():
            """
            Send a command to a module.
            
            Request format:
            {
                "command": "string",  # The command to execute
                "module_id": "string", # The module ID or "all"
                "params": {           # Optional parameters
                    "key": "value"
                }
            }
            
            Example:
            curl -X POST http://192.168.0.98:5000/api/send_command -H "Content-Type: application/json" -d "{\"command\":\"start_recording\",\"module_id\":\"all\"}"
            """
            try:
                if not request.is_json:
                    return jsonify({
                        "error": "Request must be JSON",
                        "content_type": request.content_type,
                        "example": {
                            "command": "start_recording",
                            "module_id": "all"
                        }
                    }), 400
                
                data = request.get_json(force=True)
                self.logger.info(f"Received command request: {data}")
                
                command = data.get('command')
                module_id = data.get('module_id')
                params = data.get('params', {})
                
                if not command or not module_id:
                    return jsonify({
                        "error": "Missing required fields",
                        "required": ["command", "module_id"],
                        "received": {
                            "command": command,
                            "module_id": module_id
                        }
                    }), 400
                
                self.logger.info(f"Processing command: {command} for module: {module_id}")
                
                if self.callbacks["send_command"]:
                    result = self.callbacks["send_command"](module_id, command, params)
                    return jsonify({
                        "status": "success",
                        "message": "Command sent successfully",
                        "command": command,
                        "module_id": module_id
                    })
                else:
                    self.logger.error("No command callback registered")
                    return jsonify({
                        "error": "Command system not available",
                        "status": "error"
                    }), 503
                    
            except Exception as e:
                self.logger.error(f"Error in send_command endpoint: {str(e)}")
                return jsonify({
                    "error": str(e),
                    "status": "error"
                }), 500
                
        @self.app.route('/api/module_health', methods=['GET'])
        def module_health():
            """Get the health status of all modules"""
            self.logger.info(f"/api/module_health endpoint called. Getting module health")
            if self.callbacks["get_module_health"]:
                health = self.callbacks["get_module_health"]()
                self.logger.info(f"Got module health for {len(health)} modules")
                return jsonify(health)
            return jsonify({})

        @self.app.route('/api/exported_recordings', methods=['GET'])
        def get_exported_recordings_api():
            """Get list of exported recordings"""
            self.logger.info("/api/exported_recordings endpoint called")
            exported_recordings = self.get_exported_recordings()
            return jsonify({"exported_recordings": exported_recordings})
