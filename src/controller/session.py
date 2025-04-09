import uuid
import time
from datetime import datetime

class SessionManager:
    def __init__(self):
        self.session_id = None # A dictionary containing the current session data

    def start_session(self, module_id=None):
        """Start a new session for a module"""
        timestamp = datetime.now().strftime("%Y%m%d %H%M%S")
        self.session_id = f"REC_{timestamp}_{module_id}" # Generate a new session ID
        return self.session_id

    def end_session(self):
        """End the current session"""
        self.session_id = None
    
    def get_session_id(self):
        """Get the current session ID"""
        return self.session_id


