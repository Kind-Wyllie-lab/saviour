"""
Recording manager for the SAVIOUR Controller

Author: Andrew SG
Created: 26/01/2026
"""

import logging
from datetime import datetime
from typing import Optional


class Recording():
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.current_session_name: str = None
        self.recording: bool = False

        self.sessions = {} 


    def start_recording(self, target, session_name: str, duration: int):
        """
        Handles a command to start a new recording session.

        Args:
            - target: The module or modules to start recording (e.g. all, camera_dc67, group_2)
            - session_name: The provided session name 
            - duration: Recording session duration in seconds
        """
        # Add timestamp to session name
        start_time = datetime.now().strftime("%Y%M%d_%H%m$S")
        session_name += ("_" + start_time)

        # Send the command
        params = {
            "duration": duration,
            "session_name": session_name
        }
        self.api.send_command(target, "start_recording", params)

        # Append session
        self.logger.info(f"Telling {target} to start_recording for {duration}s as session {session_name}")
        self.sessions[session_name] = {
            "start_time": start_time,
            "target": target, # "all" | "box_1" | "camera_dc76"
            "duration": duration,
            "recording": True
        }
        self.current_session_name = session_name
        self.recording = True


    def stop_recording(self, target: str):
        """
        Stops a recording session based on target or session name.
        
        Args:
            - target: The module or modules to stop recording (e.g. all, camera_dc67, group_2)
        """
        #TODO:  Find the corresponding session based on target


        # Stop them recording
        self.api.send_command(target, "stop_recording", {})

        self.current_session_name = None
        self.recording = False
    

    def get_session_name_from_target(self, target: str):
        """
        Find which session the target belongs to.
        Assumption: one target can only belong to one recording session.
        """
        for session_name, session in self.sessions.items():
            if session.get("target") == target:
                return session_name
            return None


    """Getter methods"""
    def get_recording_status(self) -> bool:
        return self.recording

    
    def get_recording_sessions(self) -> dict:
        return self.sessions

    
    def get_active_recording_sessions(self) -> dict:
        return {
            k: v for k, v in self.sessions.items() if v.get("recording") is True
        }
