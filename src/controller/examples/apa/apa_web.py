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