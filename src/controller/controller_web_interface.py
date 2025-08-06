#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Interface

Handles user interaction with the habitat controller, including:
- Manual control CLI
- Command parsing and execution
- Help system and module listing
"""

import logging
import time
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from typing import Any
import threading
import json
import os
from datetime import datetime
from pathlib import Path

class WebInterfaceManager:
    def __init__(self, logger: logging.Logger, config_manager):
        self.logger = logger
        self.config = config_manager

        # Get the port from the config
        self.port = self.config.get("interface.web_interface_port")

        # Flask setup
        self.app = Flask(__name__)
        self.socketio = SocketIO(self.app, cors_allowed_origins="*", async_mode='threading')
        
        # Callbacks
        self.callbacks = {} # An empty dict which will later be assigned callback functions

        # Webhook handlers
        self.ptp_update_handlers = []
        self.module_update_handlers = []
        
        # Experiment name persistence
        self.current_experiment_name = ""

        # Register routes and webhooks        
        self.register_routes() # Register routes e.g. index, camera, status etc

        # Test mode
        self.test = False
        self._running = False

        # Set up paths
        self.habitat_share_dir = Path("/home/pi/controller_share")

        self._pending_recordings_requests = None  # For aggregating recordings_list responses
        self._pending_recordings_lock = threading.Lock()
    
    def register_callbacks(self, callbacks={}):
        """Register callbacks based on a dict.
        Should include:
            "get_modules"
            "get_ptp_history"
            "send_command"
            "get_module_health"
        """
        self.callbacks = callbacks

    def register_ptp_update_handler(self, handler):
        """Register a handler for PTP updates"""
        self.ptp_update_handlers.append(handler)

    def register_module_update_handler(self, handler):
        """Register a handler for module list updates"""
        self.logger.info(f"(WEB INTERFACE MANAGER) Registering new module update handler")
        self.module_update_handlers.append(handler)
        self.logger.info(f"(WEB INTERFACE MANAGER) Total module update handlers: {len(self.module_update_handlers)}")

    def notify_ptp_update(self):
        """Notify all registered handlers of a PTP update"""
        if self.callbacks["get_ptp_history"]:
            history = self.callbacks["get_ptp_history"]()
            for handler in self.ptp_update_handlers:
                try:
                    handler(history)
                except Exception as e:
                    self.logger.error(f"(WEB INTERFACE MANAGER) Error in PTP update handler: {e}")

    def notify_module_update(self):
        """Notify all registered handlers of a module list update"""
        self.logger.info(f"(WEB INTERFACE MANAGER) Notifying module update to {len(self.module_update_handlers)} handlers")
        if self.callbacks["get_modules"]:
            modules = self.callbacks["get_modules"]()
            self.logger.info(f"(WEB INTERFACE MANAGER) Got {len(modules)} modules from callback")
            
            # Use socketio.emit instead of individual handlers to ensure proper context
            self.socketio.emit('module_update', {"modules": modules})
            self.logger.info(f"(WEB INTERFACE MANAGER) Sent module update to all clients")

    def register_routes(self):
        # Main pages
        @self.app.route('/')
        def index():
            return render_template('index.html')
        
        @self.app.route('/recordings')
        def recordings():
            return render_template('recordings.html')
    
        @self.app.route('/guide')
        def guide():
            return render_template('guide.html')

        # WebSocket event handlers - for use by the web interface
        @self.socketio.on('connect')
        def handle_connect(auth=None):
            self.logger.info(f"(WEB INTERFACE MANAGER) handle_connect called with auth: {auth}")
            client_ip = request.remote_addr
            self.socketio.emit('client_ip', client_ip)
            self.logger.info(f"(WEB INTERFACE MANAGER) Client connected")
            
            # Send initial module list
            self.logger.info(f"(WEB INTERFACE MANAGER) About to call get_modules()")
            modules = self.get_modules()
            self.logger.info(f"(WEB INTERFACE MANAGER) get_modules() returned: {type(modules)}")
            self.logger.info(f"(WEB INTERFACE MANAGER) Sending initial module list to new client: {len(modules)} modules")
            self.socketio.emit('module_update', {"modules": modules})
            
            # Send current experiment name to new client
            if self.current_experiment_name:
                self.socketio.emit('experiment_name_update', {"experiment_name": self.current_experiment_name})
                self.logger.info(f"(WEB INTERFACE MANAGER) Sent current experiment name to new client: {self.current_experiment_name}")

        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.logger.info(f"(WEB INTERFACE MANAGER) Client disconnected")

        @self.socketio.on('save_experiment_name')
        def handle_save_experiment_name(data):
            """Handle saving experiment name from frontend"""
            try:
                experiment_name = data.get('experiment_name', '').strip()
                self.current_experiment_name = experiment_name
                self.logger.info(f"(WEB INTERFACE MANAGER) Saved experiment name: {experiment_name}")
                
                # Broadcast to all clients
                self.socketio.emit('experiment_name_update', {"experiment_name": experiment_name})
                self.logger.info(f"(WEB INTERFACE MANAGER) Broadcasted experiment name update to all clients")
                
            except Exception as e:
                self.logger.error(f"(WEB INTERFACE MANAGER) Error saving experiment name: {str(e)}")
                self.socketio.emit('error', {'message': str(e)})

        @self.socketio.on('get_experiment_name')
        def handle_get_experiment_name():
            """Handle request for current experiment name"""
            try:
                self.logger.info(f"(WEB INTERFACE MANAGER) Client requested experiment name")
                self.socketio.emit('experiment_name_update', {"experiment_name": self.current_experiment_name})
                self.logger.info(f"(WEB INTERFACE MANAGER) Sent experiment name to client: {self.current_experiment_name}")
                
            except Exception as e:
                self.logger.error(f"(WEB INTERFACE MANAGER) Error getting experiment name: {str(e)}")
                self.socketio.emit('error', {'message': str(e)})

        @self.socketio.on('command')
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
                
                self.logger.info(f"(WEB INTERFACE MANAGER) Received command via WebSocket: {data}")
                
                # Special handling for list_recordings to all modules
                if command_type == 'list_recordings' and module_id == 'all':
                    modules = self.get_modules()
                    if not modules:
                        self.logger.warning("(WEB INTERFACE MANAGER) No modules found for list_recordings aggregation.")
                        self.socketio.emit('recordings_list', {'module_recordings': [], 'exported_recordings': self.get_exported_recordings()})
                        return
                    with self._pending_recordings_lock:
                        self._pending_recordings_requests = {
                            'expected': set(m['id'] for m in modules if 'id' in m),
                            'received': {},
                            'timer': None
                        }
                    # Send list_recordings to all modules
                    for m in modules:
                        if 'id' in m and self.callbacks["send_command"]:
                            self.callbacks["send_command"](m['id'], 'list_recordings', {})
                    # Start a timer to emit after timeout
                    def emit_aggregated():
                        with self._pending_recordings_lock:
                            if self._pending_recordings_requests is None:
                                return
                            all_recordings = []
                            for mod_id, recs in self._pending_recordings_requests['received'].items():
                                for rec in recs:
                                    rec['module_id'] = mod_id
                                    all_recordings.append(rec)
                            self.socketio.emit('recordings_list', {
                                'module_recordings': all_recordings,
                                'exported_recordings': self.get_exported_recordings()
                            })
                            self._pending_recordings_requests = None
                    timer = threading.Timer(2.0, emit_aggregated)
                    self._pending_recordings_requests['timer'] = timer
                    timer.start()
                    return
                
                # Format command with parameters
                command = command_type 
                if params:
                    if command_type == 'start_streaming':
                        # For streaming, we need client_ip and port
                        client_ip = params.get('client_ip')
                        port = params.get('port', 8080)  # Default to 8080 if not specified
                        command = f"{command_type} client_ip={client_ip} port={port}"
                    else:
                        # For other commands, format as key=value pairs
                        param_strings = [f"{k}={v}" for k, v in params.items()]
                        command = f"{command_type} {' '.join(param_strings)}"
                
                # Send command to module
                if self.callbacks["send_command"]:
                    self.callbacks["send_command"](module_id, command, params)
                    self.logger.info(f"(WEB INTERFACE MANAGER) Command sent successfully: {command} to module {module_id}")
                    
                    # If this was a clear_recordings command, request updated list
                    if command_type == 'clear_recordings':
                        # Wait a short moment for the deletion to complete
                        self.socketio.sleep(0.5)
                        # Request updated recordings list
                        if self.callbacks["send_command"]:
                            self.callbacks["send_command"](module_id, 'list_recordings', {})
                            self.logger.info(f"(WEB INTERFACE MANAGER) Requested updated recordings list after clear")
                else:
                    self.logger.error("(WEB INTERFACE MANAGER) No command handler registered")
                    
            except Exception as e:
                self.logger.error(f"(WEB INTERFACE MANAGER) Error handling command: {str(e)}")
                self.socketio.emit('error', {'message': str(e)})

        @self.socketio.on('module_update')
        def handle_module_update():
            """Handle request for module data"""
            self.logger.info(f"(WEB INTERFACE MANAGER) Client requested module data")
            
            # Get current modules from callback
            modules = self.get_modules()
            self.logger.info(f"(WEB INTERFACE MANAGER) Got {len(modules)} modules from callback")
            
            # Send module update to all clients
            self.socketio.emit('module_update', {'modules': modules})
            self.logger.info(f"(WEB INTERFACE MANAGER) Sent module update to all clients")

        @self.socketio.on('module_status')
        def handle_module_status(data):
            """Handle module status update"""
            try:
                self.logger.info(f"(WEB INTERFACE MANAGER) Received module status: {data}")
                if not isinstance(data, dict):
                    raise ValueError("Status data must be a dictionary")
                
                module_id = data.get('module_id')
                status = data.get('status')
                
                if not module_id or not status:
                    raise ValueError("(WEB INTERFACE MANAGER) Status must include 'module_id' and 'status'")
                
                # Handle recordings list response
                if status.get('type') == 'recordings_list':
                    self.logger.info(f"(WEB INTERFACE MANAGER) Broadcasting module recordings for module {module_id}")
                    module_recordings = status.get('recordings', [])
                    
                    # Send individual module recordings response
                    self.socketio.emit('module_recordings', {
                        'module_id': module_id,
                        'recordings': module_recordings
                    })
                    return
                
                # Handle export complete response
                if status.get('type') == 'export_complete':
                    self.logger.info(f"(WEB INTERFACE MANAGER) Broadcasting export complete for module {module_id}")
                    self.socketio.emit('export_complete', {
                        'module_id': module_id,
                        'success': status.get('success', False),
                        'error': status.get('error'),
                        'filename': status.get('filename')
                    })
                    return
                
                # Handle recording started/stopped status
                if status.get('type') in ['recording_started', 'recording_stopped']:
                    self.logger.info(f"(WEB INTERFACE MANAGER) Broadcasting recording status for module {module_id}")
                    self.socketio.emit('module_status', {
                        'module_id': module_id,
                        'status': status
                    })
                    return
                
                # For heartbeat and other status types
                if 'recording_status' not in status:
                    self.logger.warning("(WEB INTERFACE MANAGER) Recording status not in received status update.")
                
                # Broadcast status to all clients
                self.socketio.emit('module_status', {
                    'module_id': module_id,
                    'status': status
                })
                
            except Exception as e:
                self.logger.error(f"(WEB INTERFACE MANAGER) Error handling module status: {str(e)}")
                # Optionally emit error back to client
                # self.socketio.emit('error', {'message': str(e)})

        # REST API endpoints - for use by external services e.g. a Matlab script running an experiment that wants to start recordings
        @self.app.route('/api/list_modules', methods=['GET'])
        def list_modules():
            self.logger.info(f"(WEB INTERFACE MANAGER) /api/list_modules endpoint called. Listing modules")
            modules = self.get_modules()
            self.logger.info(f"(WEB INTERFACE MANAGER) Found {len(modules)} modules")
            return jsonify({"modules": modules})

        @self.app.route('/api/ptp_history', methods=['GET'])
        def ptp_history():
            """Get PTP history for all modules"""
            self.logger.info(f"(WEB INTERFACE MANAGER) /api/ptp_history endpoint called. Getting PTP history")
            if self.callbacks["get_ptp_history"]:
                history = self.callbacks["get_ptp_history"]()
                self.logger.info(f"(WEB INTERFACE MANAGER) Got PTP history for {len(history)} modules")
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
                self.logger.info(f"(WEB INTERFACE MANAGER) Received command request: {data}")
                
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
                
                self.logger.info(f"(WEB INTERFACE MANAGER) Processing command: {command} for module: {module_id}")
                
                if self.callbacks["send_command"]:
                    result = self.callbacks["send_command"](module_id, command, params)
                    return jsonify({
                        "status": "success",
                        "message": "Command sent successfully",
                        "command": command,
                        "module_id": module_id
                    })
                else:
                    self.logger.error("(WEB INTERFACE MANAGER) No command callback registered")
                    return jsonify({
                        "error": "Command system not available",
                        "status": "error"
                    }), 503
                    
            except Exception as e:
                self.logger.error(f"(WEB INTERFACE MANAGER) Error in send_command endpoint: {str(e)}")
                return jsonify({
                    "error": str(e),
                    "status": "error"
                }), 500
                
        @self.app.route('/api/module_health', methods=['GET'])
        def module_health():
            """Get the health status of all modules"""
            self.logger.info(f"(WEB INTERFACE MANAGER) /api/module_health endpoint called. Getting module health")
            if self.callbacks["get_module_health"]:
                health = self.callbacks["get_module_health"]()
                self.logger.info(f"(WEB INTERFACE MANAGER) Got module health for {len(health)} modules")
                return jsonify(health)
            return jsonify({})

        @self.app.route('/api/exported_recordings', methods=['GET'])
        def get_exported_recordings_api():
            """Get list of exported recordings"""
            self.logger.info("(WEB INTERFACE MANAGER) /api/exported_recordings endpoint called")
            exported_recordings = self.get_exported_recordings()
            return jsonify({"exported_recordings": exported_recordings})

        @self.socketio.on('get_exported_recordings')
        def handle_get_exported_recordings():
            """Handle request for exported recordings"""
            try:
                recordings = self.get_exported_recordings()
                self.socketio.emit('exported_recordings_list', {
                    'exported_recordings': recordings
                })
            except Exception as e:
                self.logger.error(f"(WEB INTERFACE MANAGER) Error getting exported recordings: {str(e)}")
                self.socketio.emit('exported_recordings_list', {
                    'exported_recordings': [],
                    'error': str(e)
                })

        @self.socketio.on('get_modules')
        def handle_get_modules():
            """Handle request for list of modules"""
            try:
                modules = self.get_modules()
                self.socketio.emit('modules_list', {
                    'modules': modules
                })
            except Exception as e:
                self.logger.error(f"(WEB INTERFACE MANAGER) Error getting modules: {str(e)}")
                self.socketio.emit('modules_list', {
                    'modules': [],
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
                self.logger.error(f"(WEB INTERFACE MANAGER) Error getting module health: {str(e)}")
                self.socketio.emit('module_health_update', {
                    'module_health': {},
                    'error': str(e)
                })

    def get_modules(self):
        """Get list of all discovered modules"""
        self.logger.info("(WEB INTERFACE MANAGER) Getting modules list")
        if self.callbacks["get_modules"]:
            self.logger.info(f"(WEB INTERFACE MANAGER) Callback type: {type(self.callbacks['get_modules'])}")
            self.logger.info(f"(WEB INTERFACE MANAGER) Callback: {self.callbacks['get_modules']}")
            # Call the callback function to get the actual modules list
            modules = self.callbacks["get_modules"]()
            self.logger.info(f"(WEB INTERFACE MANAGER) Modules type: {type(modules)}")
            self.logger.info(f"(WEB INTERFACE MANAGER) Modules: {modules}")
            return modules
        else:
            self.logger.warning("(WEB INTERFACE MANAGER) No get_modules callback registered")
            return []
    
    def update_modules(self, modules: list):
        """Update the list of modules from the controller service manager"""
        self._modules = modules

    def start(self):
        """Start the web interface in a separate thread"""
        if not self._running:
            self.logger.info(f"(WEB INTERFACE MANAGER) Starting web interface on port {self.port}")
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
        self.logger.info("(WEB INTERFACE MANAGER) Listing modules")
        modules = self.get_modules()
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
        
        self.logger.info(f"(WEB INTERFACE MANAGER) Scanning NAS for recordings...")
        
        # Try to mount NAS if not already mounted
        if not nas_mount_point.exists() or not nas_mount_point.is_mount():
            self.logger.info(f"(WEB INTERFACE MANAGER) NAS not mounted, attempting to mount...")
            if not self.mount_nas():
                self.logger.error(f"(WEB INTERFACE MANAGER) Failed to mount NAS, returning empty list")
                return recordings  # Return empty list if mounting failed
        
        self.logger.info(f"(WEB INTERFACE MANAGER) NAS is mounted at {nas_mount_point}")
        
        # Check what's in the root NAS directory
        if nas_mount_point.exists():
            root_contents = list(nas_mount_point.iterdir())
            self.logger.info(f"(WEB INTERFACE MANAGER) NAS root contents: {[item.name for item in root_contents]}")
            
            # Look specifically for export directories
            export_dirs = [item for item in root_contents if item.is_dir() and item.name.startswith('export_')]
            self.logger.info(f"(WEB INTERFACE MANAGER) Found export directories: {[item.name for item in export_dirs]}")
        else:
            self.logger.error(f"(WEB INTERFACE MANAGER) NAS mount point does not exist: {nas_mount_point}")
            return recordings
        
        # Scan multiple directories for recordings
        directories_to_scan = ["recordings", "videos", "ttl"]
        
        for dir_name in directories_to_scan:
            scan_path = nas_mount_point / dir_name
            self.logger.info(f"(WEB INTERFACE MANAGER) Looking for recordings in: {scan_path}")
            
            if scan_path.exists():
                self.logger.info(f"(WEB INTERFACE MANAGER) {dir_name} directory exists, scanning for files...")
                for file in scan_path.glob('**/*'):
                    self.logger.info(f"(WEB INTERFACE MANAGER) Found file: {file} (suffix: {file.suffix})")
                    if file.is_file() and file.suffix in ['.mp4', '.txt']:
                        self.logger.info(f"(WEB INTERFACE MANAGER) Adding file to recordings list: {file}")
                        recordings.append({
                            'filename': f"nas/{dir_name}/{str(file.relative_to(scan_path))}",
                            'size': file.stat().st_size,
                            'created': datetime.fromtimestamp(file.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                            'is_exported': True,
                            'destination': 'nas'
                        })
            else:
                self.logger.info(f"(WEB INTERFACE MANAGER) {dir_name} directory does not exist: {scan_path}")
        
        # Also scan for export directories (like export_20250624_220253) in the root
        self.logger.info(f"(WEB INTERFACE MANAGER) Scanning for export directories in root...")
        for item in nas_mount_point.iterdir():
            self.logger.info(f"(WEB INTERFACE MANAGER) Checking item: {item.name} (is_dir: {item.is_dir()}, starts_with_export: {item.name.startswith('export_')})")
            if item.is_dir() and item.name.startswith('export_'):
                self.logger.info(f"(WEB INTERFACE MANAGER) Found export directory: {item}")
                for file in item.glob('**/*'):
                    self.logger.info(f"(WEB INTERFACE MANAGER) Found file in export directory: {file} (suffix: {file.suffix})")
                    if file.is_file() and file.suffix in ['.mp4', '.txt']:
                        self.logger.info(f"(WEB INTERFACE MANAGER) Adding export file to recordings list: {file}")
                        recordings.append({
                            'filename': f"nas/{item.name}/{str(file.relative_to(item))}",
                            'size': file.stat().st_size,
                            'created': datetime.fromtimestamp(file.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                            'is_exported': True,
                            'destination': 'nas'
                        })
        
        self.logger.info(f"(WEB INTERFACE MANAGER) Found {len(recordings)} NAS recordings")
        return recordings

    def mount_nas(self):
        """Mount the NAS share"""
        try:
            import subprocess
            
            # NAS configuration - updated to match working module_export_manager implementation
            nas_ip = "192.168.0.2"
            share_path = "data"
            username = "sidbit"
            password = "RaspberryWonder1305"
            mount_point = "/mnt/nas"
            
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
                self.logger.error(f"(WEB INTERFACE MANAGER) Failed to mount NAS: {result.stderr}")
                return False
            
            self.logger.info(f"(WEB INTERFACE MANAGER) Successfully mounted NAS at {mount_point}")
            return True
            
        except Exception as e:
            self.logger.error(f"(WEB INTERFACE MANAGER) NAS mount failed: {str(e)}")
            return False

    def handle_module_status(self, module_id, status):
        """Handle status update from a module and emit to frontend"""
        try:
            self.logger.info(f"(WEB INTERFACE MANAGER) Received status from {module_id}: {status}")

            # Ensure status has required fields
            if not isinstance(status, dict):
                raise ValueError("Status must be a dictionary")

            # Handle recordings list response
            if status.get('type') == 'recordings_list':
                self.logger.info(f"(WEB INTERFACE MANAGER) Broadcasting module recordings for module {module_id}")
                module_recordings = status.get('recordings', [])
                
                # Send individual module recordings response
                self.socketio.emit('module_recordings', {
                    'module_id': module_id,
                    'recordings': module_recordings
                })
                return

            # Handle export complete response
            if status.get('type') == 'export_complete':
                self.logger.info(f"(WEB INTERFACE MANAGER) Broadcasting export complete for module {module_id}")
                self.socketio.emit('export_complete', {
                    'module_id': module_id,
                    'success': status.get('success', False),
                    'error': status.get('error'),
                    'filename': status.get('filename')
                })
                return

            # Handle recording started/stopped status
            if status.get('type') in ['recording_started', 'recording_stopped']:
                self.logger.info(f"(WEB INTERFACE MANAGER) Broadcasting recording status for module {module_id}")
                self.socketio.emit('module_status', {
                    'module_id': module_id,
                    'status': status
                })
                return

            # For heartbeat and other status types
            if 'recording_status' not in status:
                self.logger.warning("(WEB INTERFACE MANAGER) Recording status not in received status update.")
                
            # Emit the status to all connected clients
            self.socketio.emit('module_status', {
                'module_id': module_id,
                'status': status
            })
        except Exception as e:
            self.logger.error(f"(WEB INTERFACE MANAGER) Error handling module status: {str(e)}")
