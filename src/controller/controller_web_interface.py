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
        self.get_modules_callback = None
        self.get_ptp_history_callback = None
        self.send_command_callback = None
        # Webhook handlers
        self.ptp_update_handlers = []
        self.module_update_handlers = []
        
        self.register_routes() # Register routes e.g. index, camera, status etc
    
        # Test mode
        self.test = False
        self._running = False

        self.habitat_share_dir = Path("../habitat_share")

    def register_callbacks(self, get_modules=None, get_ptp_history=None, send_command=None, get_module_health=None):
        """Register callbacks for getting data from the command handler"""
        # TODO: Swich to dict based callback registration
        self.get_modules_callback = get_modules
        self.get_ptp_history_callback = get_ptp_history
        self.send_command_callback = send_command
        self.get_module_health_callback = get_module_health

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
        if self.get_ptp_history_callback:
            history = self.get_ptp_history_callback()
            for handler in self.ptp_update_handlers:
                try:
                    handler(history)
                except Exception as e:
                    self.logger.error(f"(WEB INTERFACE MANAGER) Error in PTP update handler: {e}")

    def notify_module_update(self):
        """Notify all registered handlers of a module list update"""
        self.logger.info(f"(WEB INTERFACE MANAGER) Notifying module update to {len(self.module_update_handlers)} handlers")
        if self.get_modules_callback:
            modules = self.get_modules_callback()
            self.logger.info(f"(WEB INTERFACE MANAGER) Got {len(modules)} modules from callback")
            
            # Use socketio.emit instead of individual handlers to ensure proper context
            self.socketio.emit('module_update', {"modules": modules})
            self.logger.info(f"(WEB INTERFACE MANAGER) Sent module update to all clients")

    def register_routes(self):
        # Main pages
        @self.app.route('/')
        def index():
            return render_template('index.html')
    
        # WebSocket event handlers - for use by the web interface
        @self.socketio.on('connect')
        def handle_connect():
            client_ip = request.remote_addr
            self.socketio.emit('client_ip', client_ip)
            self.logger.info(f"(WEB INTERFACE MANAGER) Client connected")
            
            # Send initial module list
            modules = self.get_modules()
            self.logger.info(f"(WEB INTERFACE MANAGER) Sending initial module list to new client: {len(modules)} modules")
            self.socketio.emit('module_update', {"modules": modules})

        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.logger.info(f"(WEB INTERFACE MANAGER) Client disconnected")

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
                params = data.get('params', {}) # Params may be none depending on the command
                
                self.logger.info(f"(WEB INTERFACE MANAGER) Received command via WebSocket: {data}")
                
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
                if self.send_command_callback:
                    self.send_command_callback(module_id, command)
                    self.logger.info(f"(WEB INTERFACE MANAGER) Command sent successfully: {command} to module {module_id}")
                    
                    # If this was a clear_recordings command, request updated list
                    if command_type == 'clear_recordings':
                        # Wait a short moment for the deletion to complete
                        self.socketio.sleep(0.5)
                        # Request updated recordings list
                        if self.send_command_callback:
                            self.send_command_callback(module_id, 'list_recordings')
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
            modules = self.get_modules_callback()
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
                    self.logger.info(f"(WEB INTERFACE MANAGER) Broadcasting recordings list for module {module_id}")
                    # Get module recordings
                    module_recordings = status.get('recordings', [])
                    # Get exported recordings
                    exported_recordings = self.get_exported_recordings()
                    
                    # Send both lists separately
                    self.socketio.emit('recordings_list', {
                        'module_id': module_id,
                        'module_recordings': module_recordings,
                        'exported_recordings': exported_recordings
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
            if self.get_ptp_history_callback:
                history = self.get_ptp_history_callback()
                self.logger.info(f"(WEB INTERFACE MANAGER) Got PTP history for {len(history)} modules")
                return jsonify(history)
            return jsonify({})

        @self.app.route('/api/send_command', methods=['POST'])
        def send_command():
            """Send a command to a module"""
            self.logger.info(f"(WEB INTERFACE MANAGER) /api/send_command endpoint called with command: {request.json.get('command')} and module_id: {request.json.get('module_id')}")
            command = request.json.get('command')
            module_id = request.json.get('module_id')
            if self.send_command_callback:
                self.send_command_callback(module_id, command)
            else:
                self.logger.error(f"(WEB INTERFACE MANAGER) No send_command callback registered")
                return jsonify({"error": "No send_command callback registered"}), 500
                
        @self.app.route('/api/module_health', methods=['GET'])
        def module_health():
            """Get the health status of all modules"""
            self.logger.info(f"(WEB INTERFACE MANAGER) /api/module_health endpoint called. Getting module health")
            if self.get_module_health_callback:
                health = self.get_module_health_callback()
                self.logger.info(f"(WEB INTERFACE MANAGER) Got module health for {len(health)} modules")
                return jsonify(health)
            return jsonify({})


    def get_modules(self):
        """Get the list of modules"""
        if self.get_modules_callback:
            return self.get_modules_callback()
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
        modules = []
        for module in self.controller.service_manager.modules:
            # Convert module to dict and ensure all keys are strings
            module_dict = {
                'id': module.id,
                'type': module.type,
                'ip': module.ip,
                'port': module.port,
                'properties': {k.decode() if isinstance(k, bytes) else k: 
                             v.decode() if isinstance(v, bytes) else v 
                             for k, v in module.properties.items()}
            }
            modules.append(module_dict)
        return jsonify({"modules": modules})

    def get_exported_recordings(self):
        """Get list of exported recordings from habitat_share directory"""
        recordings = []
        if self.habitat_share_dir.exists():
            for file in self.habitat_share_dir.glob('**/*'):
                if file.is_file() and file.suffix in ['.mp4', '.txt']:
                    recordings.append({
                        'filename': str(file.relative_to(self.habitat_share_dir)),
                        'size': file.stat().st_size,
                        'created': datetime.fromtimestamp(file.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                        'is_exported': True
                    })
        return recordings

    def handle_module_status(self, module_id, status):
        """Handle status update from a module and emit to frontend"""
        try:
            self.logger.info(f"(WEB INTERFACE MANAGER) Received status from {module_id}: {status}")

            # Ensure status has required fields
            if not isinstance(status, dict):
                raise ValueError("Status must be a dictionary")

            # Handle recordings list response
            if status.get('type') == 'recordings_list':
                self.logger.info(f"(WEB INTERFACE MANAGER) Broadcasting recordings list for module {module_id}")
                # Get module recordings
                module_recordings = status.get('recordings', [])
                # Get exported recordings
                exported_recordings = self.get_exported_recordings()
                
                # Send both lists separately
                self.socketio.emit('recordings_list', {
                    'module_id': module_id,
                    'module_recordings': module_recordings,
                    'exported_recordings': exported_recordings
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
