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
        
        # Webhook handlers
        self.ptp_update_handlers = []
        self.module_update_handlers = []
        
        self.register_routes() # Register routes e.g. index, camera, status etc
    
        # Test mode
        self.test = False
        self._running = False

    def register_callbacks(self, get_modules=None, get_ptp_history=None):
        """Register callbacks for getting data from the command handler"""
        self.get_modules_callback = get_modules
        self.get_ptp_history_callback = get_ptp_history

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
            for handler in self.module_update_handlers:
                try:
                    handler(modules)
                    self.logger.info(f"(WEB INTERFACE MANAGER) Successfully sent module update to handler")
                except Exception as e:
                    self.logger.error(f"(WEB INTERFACE MANAGER) Error in module update handler: {e}")

    def register_routes(self):
        # Main pages
        @self.app.route('/')
        def index():
            return render_template('index.html')
        
        # API endpoints
        @self.app.route('/api/list_modules', methods=['GET'])
        def list_modules():
            self.logger.info(f"(WEB INTERFACE MANAGER) Listing modules")
            modules = self.get_modules()
            self.logger.info(f"(WEB INTERFACE MANAGER) Found {len(modules)} modules")
            return jsonify({"modules": modules})

        @self.app.route('/api/ptp_history', methods=['GET'])
        def ptp_history():
            """Get PTP history for all modules"""
            self.logger.info(f"(WEB INTERFACE MANAGER) Getting PTP history")
            if self.get_ptp_history_callback:
                history = self.get_ptp_history_callback()
                self.logger.info(f"(WEB INTERFACE MANAGER) Got PTP history for {len(history)} modules")
                return jsonify(history)
            return jsonify({})

        # WebSocket event handlers
        @self.socketio.on('connect')
        def handle_connect():
            self.logger.info(f"(WEB INTERFACE MANAGER) Client connected")
            # Register this client for updates
            def send_ptp_update(history):
                self.logger.info(f"(WEB INTERFACE MANAGER) Sending PTP update to client")
                self.socketio.emit('ptp_status', history, room=request.sid)
            def send_module_update(modules):
                self.logger.info(f"(WEB INTERFACE MANAGER) Sending module update to client")
                self.socketio.emit('module_update', {"modules": modules}, room=request.sid)
            self.register_ptp_update_handler(send_ptp_update)
            self.register_module_update_handler(send_module_update)
            self.logger.info(f"(WEB INTERFACE MANAGER) Registered update handlers for new client")
            
            # Send initial module list
            modules = self.get_modules()
            self.logger.info(f"(WEB INTERFACE MANAGER) Sending initial module list to new client: {len(modules)} modules")
            self.socketio.emit('module_update', {"modules": modules}, room=request.sid)

        @self.socketio.on('disconnect')
        def handle_disconnect():
            self.logger.info(f"(WEB INTERFACE MANAGER) Client disconnected")
            # Remove this client's handlers
            self.ptp_update_handlers = [h for h in self.ptp_update_handlers if h.__name__ != 'send_ptp_update']
            self.module_update_handlers = [h for h in self.module_update_handlers if h.__name__ != 'send_module_update']
            self.logger.info(f"(WEB INTERFACE MANAGER) Removed handlers for disconnected client. Remaining handlers: {len(self.module_update_handlers)}")

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
