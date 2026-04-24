"""
Extension of the SAVIOUR web class to handle APA specific socket events and callbacks.
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

from web import Web
from config import Config

class APAWeb(Web):
    def __init__(self, config: Config):
        super().__init__(config)
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"APAWeb instantiated.")


    # Override base method
    def handle_special_module_status(self, module_id: str, status: str) -> bool:
        """
        APA web callbacks for events from modules    

        Args:
            module_id
            status

        Returns
            bool - True if handled, False if not
        """ 
        match status.get('type'):
            case "arduino_state":
                self.socketio.emit("arduino_state", status)
                return True
            case "shock_started_being_delivered":
                self.socketio.emit("shock_started_being_delivered", status)
                return True
            case "shock_stopped_being_delivered":
                self.socketio.emit("shock_stopped_being_delivered", status)
                return True
            case _:
                self.logger.warning(f"APA web has no logic for {status.get('type')} from {module_id}")
                return False
    