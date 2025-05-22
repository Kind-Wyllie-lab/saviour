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
        
        # Camera page
        @self.app.route('/camera')
        def camera():
            return render_template('camera.html')
        
        # Status page
        @self.app.route('/status')
        def status():
            return render_template('status.html')
        
        # API endpoints
        @self.app.route('/api/list_modules', methods=['GET'])
        def list_modules():
            if self.test==True:
                modules=[
                    {"id": "camera_XYZ", "type": "camera", "ip": "192.168.1.XY"},
                    {"id": "camera_ABC", "type": "camera", "ip": "192.168.1.AB"},
                    {"id": "camera_DEF", "type": "camera", "ip": "192.168.1.DE"},
                ]
            else:
                # TODO: Implement this
                modules = []
                # for module in self.controller.service_manager.modules:
            #     modules.append({
            #         "id": module.id,
            #         "type": module.type,
            #         "ip": module.ip
            #     })
            return jsonify({"modules": modules})
    
    def start_web_interface(self):
        """Start the web interface"""
        self.app.run(host='0.0.0.0', port=8080, debug=True)