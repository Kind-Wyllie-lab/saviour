"""
A webapp based GUI for the APA rig.

Serves a react app located in frontend/ and built using vite into frontend/dist/
"""
import sys
import os
import logging
import threading
import time
from flask import Flask, render_template, jsonify, request, send_from_directory
from flask_socketio import SocketIO


# Add the current directory to the path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from habitat.src.controller.web import Web
from habitat.src.controller.config import Config

class APAWeb(Web):
    def __init__(self, config: Config):
        self.logger = logging.getLogger(__name__)
        self.config = config

        self.app = Flask(
            __name__,
            static_folder="frontend/dist",
            static_url_path="/"
            )
        self.logger.info(f"Created app with id {id(self.app)}")
        self.socketio = SocketIO(self.app, host="0.0.0.0", cors_allowed_origins="*", async_mode="threading")

        # Flask params
        self.port = 5000
        self._running = False
        
        # Callbacks
        self.callbacks = {}
        
        # Store experiment metadata in memory
        self.experiment_metadata = {
            'experiment': "APA",
            'rat_id': '001',
            'strain': 'Wistar',
            'batch': 'B1',
            'stage': 'habituation',
            'trial': '1'
        }

        self.current_experiment_name = " "

        # Register the webapp routes and Socket.IO events
        self._register_routes()
        self._register_socketio_events()

    def register_callbacks(self, callbacks):
        """Register callbacks for the web interface"""
        # Merge with existing callbacks instead of replacing them
        super().register_callbacks(callbacks) # Get superclass to do it 
        # self.callbacks.update(callbacks)

    def _register_routes(self):
        super()._register_routes() # Get superclass to do it

    def _register_socketio_events(self):
        """Register Socket.IO event handlers"""
        super()._register_socketio_events()

        # Create new routes or override them
        @self.socketio.on('send_command')
        def handle_command(data):
            """
            Handle command from frontend.
            Command will be formatted as command_name {params_json}
            For example, start_streaming {client_ip:"192.168.1.151", port: 8080} 
            
            Args:
                command (json): The command received from the frontend. Should contain type, module_id (may be "all" or a specific module), and params field
            """
            try:
                self.logger.info(f"Received command: {data}")

                command_type = data.get('type')
                module_id = data.get('module_id')
                params = data.get('params', {})

                self.logger.info(f"Command type: {command_type}, Module: {module_id}, Params: {params}")
                
                # Send immediate feedback to frontend
                self.socketio.emit('command_response', {
                    'status': 'info',
                    'message': f'Sending {command_type} command...'
                })
                
                # Send command to module using parent's logic
                if self.callbacks["send_command"]:
                    self.callbacks["send_command"](module_id, command_type, params)
                    self.logger.info(f"Parent command passed to callback: {command_type} to module {module_id}")
                    
                    # Handle experiment commands - also send recording commands to all modules
                    if command_type == 'start_experiment':
                        self.logger.info(f"Starting experiment - sending start_recording to all modules")
                        
                        # Get experiment name from frontend metadata (check both params and data)
                        experiment_name = params.get('experiment_name') or data.get('experiment_name', "experiment_" + str(int(time.time())))
                        self.logger.info(f"Using experiment name from frontend: {experiment_name}")
                        
                        # Use the controller's start_experiment method
                        if "start_experiment" in self.callbacks:
                            result = self.callbacks["start_experiment"](experiment_name)
                            if result.get('status') == 'success':
                                self.socketio.emit('command_response', {
                                    'status': 'success',
                                    'message': f'Experiment started: {experiment_name}\nExperiment folder: {result.get("experiment_folder", "unknown")}'
                                })
                            else:
                                self.socketio.emit('command_response', {
                                    'status': 'error',
                                    'message': f'Failed to start experiment: {result.get("message", "unknown error")}'
                                })
                        else:
                            # Fallback to old method
                            recording_params = {"experiment_name": experiment_name}
                            self.callbacks["send_command"]("all", "start_recording", recording_params)
                            self.socketio.emit('command_response', {
                                'status': 'success',
                                'message': f'{command_type} command sent successfully. Recording started on all modules with experiment name: {experiment_name}'
                            })
                    elif command_type == 'stop_experiment':
                        self.logger.info(f"Stopping experiment - sending stop_recording to all modules")
                        self.callbacks["send_command"]("all", "stop_recording", {})
                        self.socketio.emit('command_response', {
                            'status': 'success',
                            'message': f'{command_type} command sent successfully. Recording stopped on all modules.'
                        })
                    elif command_type == 'validate_readiness':
                        self.logger.info(f"Validating readiness for all modules")
                        # Send validate_readiness to all modules
                        self.callbacks["send_command"]("all", "validate_readiness", {})
                        self.socketio.emit('command_response', {
                            'status': 'success',
                            'message': f'Readiness validation command sent to all modules. Check module responses for detailed results.'
                        })
                    elif command_type == 'test_communication':
                        self.logger.info(f"Testing communication with all modules")
                        if "test_communication" in self.callbacks:
                            result = self.callbacks["test_communication"](module_id)
                            self.logger.info(f"Communication test result: {result}")
                            self.socketio.emit('communication_test_result', result)
                        else:
                            self.logger.warning(f"test_communication callback not available")
                            self.socketio.emit('communication_test_result', {
                                'status': 'error',
                                'message': 'Communication test not available'
                            })
                    else:
                        # Send success response for other commands
                        self.socketio.emit('command_response', {
                            'status': 'success',
                            'message': f'{command_type} command sent successfully'
                        })
                    
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
                    self.socketio.emit('command_response', {
                        'status': 'error',
                        'message': 'No command handler registered'
                    })
                    
            except Exception as e:
                self.logger.error(f"Error handling parent command: {str(e)}")
                self.socketio.emit('command_response', {
                    'status': 'error',
                    'message': f'Error: {str(e)}'
                })

        # @self.socketio.on('request_module_configs')
        # def handle_request_module_configs(data=None):
        #     """Handle request for module configuration data"""
        #     self.logger.info(f"Request module configs called")
        #     self.logger.info(f"Available callbacks: {list(self.callbacks.keys())}")
        #     if "get_module_configs" in self.callbacks:
        #         # Request config from all modules - refresh the config stored on controller
        #         if "send_command" in self.callbacks:
        #             self.logger.info(f"Sending get_config command to all modules")
        #             self.callbacks["send_command"]("all", "get_config", {})
        #         # Get the current module configs
        #         module_configs = self.callbacks["get_module_configs"]()
        #         self.logger.info(f"Retrieved module configs: {module_configs}")
        #         self.socketio.emit('module_configs_update', {
        #             'module_configs': module_configs
        #         })
        #     else:
        #         self.logger.warning(f"get_module_configs callback not available")
        #         self.socketio.emit('module_configs_update', {
        #             'module_configs': {},
        #             'error': 'Module configs not available'
        #         })
            
        # @self.socketio.on('save_module_config')
        # def handle_save_module_config(data):
        #     """Handle save module config from frontend"""
        #     self.logger.info(f"(WEB INTERFACE MANAGER) Received request to save config to module with data {data}")
        #     if "send_command" in self.callbacks:
        #         # Format command with parameters
        #         command = "set_config"
        #         # Extract params from the data
        #         params = data.get("params", {})
                
        #         # Send the config update command to all modules
        #         self.callbacks["send_command"]("all", command, params)
                
        #         # Get updated module configs
        #         if "get_module_configs" in self.callbacks:
        #             module_configs = self.callbacks["get_module_configs"]()
        #             self.socketio.emit('module_configs_update', {
        #                 'module_configs': module_configs
        #             })
        #         else:
        #             self.socketio.emit('module_configs_update', {
        #                 'module_configs': {},
        #                 'error': 'Module configs not available'
        #             })
        #     else:
        #         self.socketio.emit('module_configs_update', {
        #             'module_configs': {},
        #             'error': 'Send command not available'
        #         })
        
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
            
            # Send confirmation back to client
            self.socketio.emit('experiment_metadata_updated', {
                'status': 'success',
                'metadata': self.experiment_metadata
            })
            self.logger.info(f"Sent experiment metadata update confirmation")
        
        @self.socketio.on('get_experiment_metadata')
        def handle_get_experiment_metadata(data=None):
            """Handle request for experiment metadata from frontend"""
            self.logger.info(f"Client requested experiment metadata")
            
            # Send current metadata to client
            self.socketio.emit('experiment_metadata_response', {
                'status': 'success',
                'metadata': self.experiment_metadata
            })
            self.logger.info(f"Sent experiment metadata to client")
        
        @self.socketio.on('get_module_readiness')
        def handle_get_module_readiness(data=None):
            """Handle request for module readiness state from frontend"""
            self.logger.info(f"Client requested module readiness state")
            
            # Get current readiness state
            readiness_state = self.get_module_readiness()
            
            # Send readiness state to client
            self.socketio.emit('module_readiness_response', {
                'status': 'success',
                'readiness': readiness_state
            })
            self.logger.info(f"Sent module readiness state to client")
        
        @self.socketio.on('check_all_modules_ready')
        def handle_check_all_modules_ready(data=None):
            """Handle request to check if all modules are ready"""
            self.logger.info(f"Client requested all modules ready check")
            
            all_ready = self.are_all_modules_ready()
            
            # Send result to client
            self.socketio.emit('all_modules_ready_response', {
                'status': 'success',
                'all_ready': all_ready
            })
            self.logger.info(f"Sent all modules ready check result: {all_ready}")


    
    # TODO: Below was used in the APA controller that went dark but previously was working at transmitting commands.
    def _handle_parent_command(self, data):
        """Handle commands using parent class logic"""
        try:
            command_type = data.get('type')
            module_id = data.get('module_id')
            params = data.get('params', {})
            
            # Import json for JSON formatting
            import json
            
            # Format command with parameters
            command = command_type 
            if params:
                if command_type == 'start_streaming':
                    # For streaming, we need client_ip and port
                    client_ip = params.get('client_ip')
                    port = params.get('port', 8080)  # Default to 8080 if not specified
                    command = f"{command_type} client_ip={client_ip} port={port}"
                elif command_type in ['send_shock', 'motor_control', 'stop_motor', 'pid_status', 'read_encoder']:
                    # For APA Arduino commands, format as JSON
                    json_params = json.dumps(params)
                    command = f"{command_type} {json_params}"
                    self.logger.info(f"Formatted APA command as JSON: {command}")
                else:
                    # For other commands, format as key=value pairs
                    param_strings = [f"{k}={v}" for k, v in params.items()]
                    command = f"{command_type} {' '.join(param_strings)}"
            
            # Send command to module using parent's logic
            if self.callbacks["send_command"]:
                self.callbacks["send_command"](module_id, command, params)
                self.logger.info(f"Parent command sent successfully: {command} to module {module_id}")
                
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
            self.logger.error(f"Error handling parent command: {str(e)}")
            self.socketio.emit('error', {'message': str(e)})

    def start(self):
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
        """Run the Flask server"""
        try:
            self.socketio.run(self.app, host='0.0.0.0', port=self.port, debug=False, allow_unsafe_werkzeug=True) # Debug = True doesn't seem to work in threaded mode...
        except Exception as e:
            self.logger.error(f"Error running web server: {e}")
            self._running = False
    
    def stop(self):
        """Stop the web server"""
        if self._running:
            self.logger.info("Stopping web interface")
            self._running = False
            # Note: Flask-SocketIO doesn't have a clean stop method
            # The thread will exit when the main process ends

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
        self.socketio.emit('module_readiness_update', {
            'module_id': module_id,
            'ready': ready_status.get('ready', False),
            'timestamp': self.module_readiness[module_id]['timestamp'],
            'checks': ready_status.get('checks', {}),
            'error': ready_status.get('error')
        })
        
    def get_module_readiness(self, module_id: str = None):
        """Get current module readiness state, with optional expiration check"""
        import time
        
        if module_id:
            # Return specific module readiness
            if module_id in self.module_readiness:
                readiness = self.module_readiness[module_id]
                # Check if readiness has expired
                if time.time() - readiness['timestamp'] > self.readiness_expiration_time:
                    self.logger.info(f"Readiness for {module_id} has expired")
                    return None
                return readiness
            return None
        else:
            # Return all module readiness, filtering out expired entries
            current_time = time.time()
            valid_readiness = {}
            
            for mid, readiness in self.module_readiness.items():
                if current_time - readiness['timestamp'] <= self.readiness_expiration_time:
                    valid_readiness[mid] = readiness
                else:
                    self.logger.info(f"Removing expired readiness for {mid}")
            
            # Clean up expired entries
            self.module_readiness = valid_readiness
            
            return valid_readiness
    
    def are_all_modules_ready(self):
        """Check if all online modules are ready"""
        if "get_modules" not in self.callbacks:
            return False
        
        modules = self.callbacks["get_modules"]()
        if not modules:
            return False
        
        # Check if all online modules have valid ready status
        for module in modules:
            module_id = module['id']
            readiness = self.get_module_readiness(module_id)
            if not readiness or not readiness['ready']:
                return False
        
        return True
