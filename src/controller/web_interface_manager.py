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
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
from typing import Any

class WebInterfaceManager:
    def __init__(self, logger: logging.Logger):
        self.logger = logger

        # Flask setup
        self.app = Flask(__name__)
        self.socketio = SocketIO(self.app) # We use socketio to serve the web interface
        self.register_routes() # Register routes e.g. index, camera, status etc
    
        # Test mode
        self.test = False

        # Web Interface 
    def register_routes(self):
        # Main pages
        @self.app.route('/')
        def index():
            return render_template('index.html')
        
        # API endpoints
        @self.app.route('/api/list_modules', methods=['GET'])
        def list_modules():
            self.logger.info(f"(WEB INTERFACE MANAGER) Listing modules")
            if self.test==True:
                self.logger.info(f"(WEB INTERFACE MANAGER) In test mode: listing dummy modules")
                modules=[
                    {"id": "camera_XYZ", "type": "camera", "ip": "192.168.1.XY"},
                    {"id": "camera_ABC", "type": "camera", "ip": "192.168.1.AB"},
                    {"id": "camera_DEF", "type": "camera", "ip": "192.168.1.DE"},
                ]
            else:
                modules = self.get_modules()
            return jsonify({"modules": modules})
    
    def get_modules(self):
        """Get the list of modules"""
        return self._modules if hasattr(self, '_modules') else [] # Should we return the empty list if no modules are found? Or should we raise an error?
    
    def update_modules(self, modules: list):
        """Update the list of modules from the controller service manager"""
        self._modules = modules
    def start_web_interface(self):
        """Start the web interface"""
        self.app.run(host='0.0.0.0', port=8080, debug=True)