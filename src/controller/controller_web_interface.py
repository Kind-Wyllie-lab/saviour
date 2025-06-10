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

    def register_callbacks(self, get_modules=None, get_ptp_history=None, send_command=None, get_module_health=None):
        """Register callbacks for getting data from the command handler"""
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
        
        # API endpoints
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

        # WebSocket event handlers
        @self.socketio.on('connect')
        def handle_connect():
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
            """Handle incoming WebSocket commands"""
            self.logger.info(f"(WEB INTERFACE MANAGER) Received command via WebSocket: {data}")
            
            if not self.send_command_callback:
                self.logger.error(f"(WEB INTERFACE MANAGER) No send_command callback registered")
                return
            
            command_type = data.get('type')
            module_id = data.get('module_id')
            
            if not command_type or not module_id:
                self.logger.error(f"(WEB INTERFACE MANAGER) Invalid command format: {data}")
                return
            
            try:
                # Send the command through the callback
                self.send_command_callback(module_id, command_type)
                self.logger.info(f"(WEB INTERFACE MANAGER) Command sent successfully: {command_type} to module {module_id}")
            except Exception as e:
                self.logger.error(f"(WEB INTERFACE MANAGER) Error sending command: {e}")
                # Optionally emit an error back to the client
                self.socketio.emit('command_error', {
                    'error': str(e),
                    'command': command_type,
                    'module_id': module_id
                })

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
            self.logger.info(f"(WEB INTERFACE) Starting web interface on port {self.port}")
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
