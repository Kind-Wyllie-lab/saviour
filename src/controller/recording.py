"""
Recording manager for the SAVIOUR Controller.

Each module can only be associated with one recording session.

Author: Andrew SG
Created: 26/01/2026
"""


import os
import logging
from datetime import datetime
import time
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class RecordingSession():
    """Dataclass to represent a SAVIOUR recording session"""
    session_name: str # Name of the recording session
    target: str # The target of recording e.g. all, group_3, camera_dc67
    modules: list = field(default_factory=list) # The module_ids belonging to this recording session. # TODO: Should this be a dict, with info about each module i.e. if they're recording still?
    start_time: int = None # Time the session was started at - None if not started
    end_time: int = None # Time the session finished at - None if not started
    active: bool = False# Bool indicating whether this session is currently recording
    error: bool = False # Bool indicating whether the session is in an error state
    error_message: str = "" # If fault state, error message here
    session_folder: str = "" # The path on the controller's samba share where session files will be stored


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
        start_time = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_name += ("-" + start_time)

        # Send the command
        params = {
            "duration": duration,
            "session_name": session_name
        }
        self.facade.send_command(target, "start_recording", params)

        # Append session
        self.logger.info(f"Telling {target} to start_recording for {duration}s as session {session_name}")

        modules = list(self.facade.get_modules_by_target(target).keys())
        self.logger.info(f"{target} contains {modules}")

        session = RecordingSession(
            session_name = session_name,
            target = target,
            modules = modules,
            active = True,
            start_time = start_time,
            end_time = None,
            session_folder = self._create_session_folder(session_name)
        )

        self.sessions[session_name] = session

        self._write_session_start_to_file(session_name)


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
            self.logger.warning(f"Could not find session name from target {target}: {e}")

        # Stop them recording
        self.facade.send_command(target, "stop_recording", {})

        self.sessions[session_name].active = False
        self.sessions[session_name].end_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._end_session(session_name)


    def get_session_name_from_target(self, target: str) -> str:
        """
        Find which session the target belongs to.
        Assumption: one target can only belong to one recording session.
        """
        active_sessions = self.get_active_recording_sessions()

        if not active_sessions:
            return None

        if target == "all":
            if len(active_sessions) != 1:
                raise ValueError("Multiple active sessions for 'all'")
            return next(iter(active_sessions))

        for session_name, session in active_sessions.items():
            if target in session.modules:
                return session_name

        return None
            

    """Getter methods"""
    def get_recording_status(self) -> bool:
        # If any session is active, system is recording
        for session_name, session in self.sessions.items():
            if session.active == True:
                return True

    
    def get_recording_sessions(self) -> dict:
        return self.sessions


    def create_session(self, session_name: str, target: str):
        # Add timestamp to session name
        start_time = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_name += ("-" + start_time)

        self.logger.info(f"Creating recording session {session_name} targeting {target}")

        modules = list(self.facade.get_modules_by_target(target).keys())

        session = RecordingSession(
            session_name = session_name,
            target = target,
            modules = modules,
            active = True,
            start_time = start_time,
            end_time = None,
            session_folder = self._create_session_folder(session_name)
        )

        self.sessions[session_name] = session

        self._create_session_file(session_name)


        params = {
            "duration": 0,
            "session_name": session_name
        }
        self.facade.send_command(target, "start_recording", params)

        self.facade.update_sessions(self.sessions)

        self._write_session_start_to_file(session_name)


    def stop_session(self, session_name: str):
        """
        Stop a recording session by session_name.

        This stops all modules in the session, marks the session inactive, 
        sets the end time, and writes to the session info file.
        """
        if session_name not in self.sessions:
            self.logger.warning(f"Tried to stop unknown session {session_name}")
            return

        session = self.sessions[session_name]

        if not session.active:
            self.logger.info(f"Session {session_name} is already stopped")
            return

        # Stop modules in the session
        self.facade.send_command(session.target, "stop_recording", {})

        # Update session status
        session.active = False
        session.end_time = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Write end time to session file
        self.facade.update_sessions(self.sessions)
        self._end_session(session_name)

        self.logger.info(f"Session {session_name} stopped successfully")


    def _create_session_file(self, session_name: str) -> None:
        filename = self._get_session_info_file(session_name)
        self.logger.info(f"Creating {filename}")
        with open(filename, "a") as f:
            f.write(f"Created {session_name} targeting {self.sessions[session_name].target} (")
            for module in self.sessions[session_name].modules:
                f.write(f"{module}, ")


    def _create_session_folder(self, session_name: str) -> str:
        # Create a folder for the session files to be written to in the samba share
        share_path = self.facade.get_share_path()
        session_folder_path = f"{share_path}/{session_name}"
        os.makedirs(session_folder_path, exist_ok=True)
        import pwd, grp
        uid = pwd.getpwnam("pi").pw_uid
        gid = grp.getgrnam("pi").gr_gid
        os.chown(session_folder_path, uid, gid)
        self.logger.info(f"Created session folder {session_folder_path}")
        return session_folder_path


    def _write_session_start_to_file(self, session_name: str) -> None:
        filename = self._get_session_info_file(session_name)
        self.logger.info(f"Writing start time to {filename}")
        with open(filename, "a") as f:
            f.write(f"\nSession started at {self.sessions[session_name].start_time}")
        

    def _end_session(self, session_name: str) -> None:
        filename = self._get_session_info_file(session_name)
        self.logger.info(f"Finishing and closing {filename}")
        with open(filename, "a") as f:
            f.write(f"\nEnded at {self.sessions[session_name].end_time}")


    def _get_session_info_file(self, session_name: str) -> str:
        session_folder = self.sessions[session_name].session_folder
        filename = os.path.abspath(f"{session_folder}/SessionInfo.txt")
        return filename

    
    def get_active_recording_sessions(self) -> dict:
        active_sessions = {
            k: v for k, v in self.sessions.items() if v.active is True
        }
        return active_sessions


    def module_offline(self, module_id: str) -> None:
        """When a module goes offline, make a note of it"""
        self.logger.info(f"{module_id} went offline, recording in session log")
        session_name = self.get_session_name_from_target(module_id)
        filename = self._get_session_info_file(session_name)
        with open(filename, "a") as f:
            f.write(f"\n{module_id} went offline at {datetime.now().strftime('%Y%m%d_%H%M%S')}")
        
        self.sessions[session_name].error=True
        self.sessions[session_name].error_message = f"{module_id} is Offline"

    
    def module_back_online(self, module_id: str) -> None:
        self.logger.info(f"{module_id} is back online, restarting recording")
        session_name = self.get_session_name_from_target(module_id)
        if not session_name:
            pass
        if self.sessions[session_name].active == True:
            filename = self._get_session_info_file(session_name)
            with open(filename, "a") as f:
                f.write(f"\n{module_id} came back online at {datetime.now().strftime('%Y%m%d_%H%M%S')}")

            # Tell it to resume recording
            params = {
                "duration": 0, # TODO: Refactor duration to be an end time instead of a duration in s
                "session_name": session_name
            }
            self.facade.send_command(module_id, "start_recording", params) # TODO: Create "restart_recording" endpoint on module 




