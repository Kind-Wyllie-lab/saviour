"""
Recording manager for the SAVIOUR Controller.

Each module can only be associated with one recording session.

Author: Andrew SG
Created: 26/01/2026
"""

import logging
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class RecordingSession():
    """Dataclass to represent a SAVIOUR recording session"""
    session_name: str # Name of the recording session
    target: str # The target of recording e.g. all, group_3, camera_dc67
    modules: list = field(default_factory=list) # The module_ids belonging to this recording session
    start_time: int = None # Time the session was started at - None if not started
    end_time: int = None # Time the session finished at - None if not started
    active: bool = False# Bool indicating whether this session is currently recording


class Recording():
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.sessions = {} # Dict of recording sessions with session_name as key


    def start_recording(self, target, session_name: str, duration: int):
        """
        Handles a command to start a new recording session.

        Args:
            - target: The module or modules to start recording (e.g. all, camera_dc67, group_2)
            - session_name: The provided session name 
            - duration: Recording session duration in seconds
        """
        # Add timestamp to session name
        start_time = datetime.now().strftime("%Y%M%d_%H%m%S")
        session_name += ("_" + start_time)

        # Send the command
        params = {
            "duration": duration,
            "session_name": session_name
        }
        self.facade.send_command(target, "start_recording", params)

        # Append session
        self.logger.info(f"Telling {target} to start_recording for {duration}s as session {session_name}")

        modules = self.facade.get_modules_by_target(target).keys()
        self.logger.info(f"{target} contains {modules}")

        session = RecordingSession(
            session_name = session_name,
            target = target,
            modules = modules,
            active = True,
            start_time = start_time,
            end_time = None
        )

        self.sessions[session_name] = session

        self._write_session_to_file(session_name)


    def stop_recording(self, target: str):
        """
        Stops a recording session based on target name.
        
        Args:
            - target: The module or modules to stop recording (e.g. all, camera_dc67, group_2)
        """
        try: 
            session_name = self.get_session_name_from_target(target)
            self.logger.info(f"{target} corresponds to {session_name}")
        except Exception as e:
            self.logger.warning(f"Could not find session name from target {target}")

        # Stop them recording
        self.facade.send_command(target, "stop_recording", {})

        self.sessions[session_name].active = False
        self.sessions[session_name].end_time = time.time()


    def get_session_name_from_target(self, target: str):
        """
        Find which session the target belongs to.
        Assumption: one target can only belong to one recording session.
        """
        for session_name, session in self.sessions.items():
            if session.get("target") == target:
                return session_name
            

    """Getter methods"""
    def get_recording_status(self) -> bool:
        # If any session is active, system is recording
        for session_name, session in self.sessions:
            if session.active == True:
                return True

    
    def get_recording_sessions(self) -> dict:
        return self.sessions


    def _write_session_to_file(self, session_name: str) -> None:
        with open(f"/tmp/{session_name}.txt", "a") as f:
            f.write(f"{session_name} targeting {self.sessions[session_name].target} (")
            for module in self.sessions[session_name].modules:
                f.write(f"{module}, ")
            f.write(f") started at {self.sessions[session_name].start_time}, ended at {self.sessions[session_name].end_time}")
        
    
    def get_active_recording_sessions(self) -> dict:
        return {
            k: v for k, v in self.sessions.items() if v.get("recording") is True
        }
