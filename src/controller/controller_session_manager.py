import uuid
import time
from datetime import datetime

class SessionManager:
    def __init__(self):
        pass

    def generate_session_id(self, module_id="unknown"):
        """Start a new session for a module"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"REC_{timestamp}_{module_id}" # Generate a new session ID



