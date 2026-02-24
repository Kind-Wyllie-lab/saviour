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
import threading


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
    stopped: bool = False # Bool indicating whether the session has been stopped 
    scheduled: bool = False # Whether the session should run on a schedule
    scheduled_start_time: Optional[str] = None # In 24hr time e.g. 19:00
    scheduled_end_time: Optional[str] = None # In 24hr time e.g. 23:00


class Recording():
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.sessions = {} # Dict of recording sessions with session_name as key
        self.session_monitor_thread = threading.Thread(target=self._monitor_sessions, daemon=True).start()


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

    
    def _format_session_name(self, session_name: str) -> str:
        # Add timestamp to session name
        start_time = datetime.now().strftime("%Y%m%d-%H%M%S")
        session_name += ("-" + start_time)

        return session_name


    def create_session(self, session_name: str, target: str) -> None:
        """Create a session that will immediately begin recording.

        Args:
            session_name: The name of the session that will be used as top level folder where files will be saved as well as filename prefix
            target: May be "all", a group name, or a specific module id. 
        """
        session_name = self._format_session_name(session_name)

        self.logger.info(f"Creating recording session {session_name} targeting {target}")

        modules = list(self.facade.get_modules_by_target(target).keys())

        session = RecordingSession(
            session_name = session_name,
            target = target,
            modules = modules,
            active = True,
            start_time = datetime.now().strftime("%Y%m%d-%H%M%S"),
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


    def create_scheduled_session(self, session_name: str, target: str, start_time: str, end_time: str):
        """Create a session that will record between a specified start and end time each day

        Args:
            session_name: The name of the session that will be used as top level folder where files will be saved as well as filename prefix
            target: May be "all", a group name, or a specific module id. 
            start_time: Time of day to start recording, in 24hr format (e.g. 19:30)
            end_time: Time of day to end recording, in 24hr format (e.g. 21:15)
        """

        session_name = self._format_session_name(session_name)
        modules = list(self.facade.get_modules_by_target(target).keys())

        session = RecordingSession(
            session_name = session_name,
            target = target,
            modules = modules,
            start_time = datetime.now().strftime("%Y%m%d-%H%M%S"),
            end_time = None,
            scheduled = True,
            scheduled_start_time = start_time,
            scheduled_end_time = end_time,
            session_folder = self._create_session_folder(session_name)
        )

        self.sessions[session_name] = session

        self.facade.update_sessions(self.sessions)

        self._create_session_file(session_name)


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

        if session.stopped:
            self.logger.info(f"Session {session_name} is already stopped")
            return

        # Stop modules in the session
        self.facade.send_command(session.target, "stop_recording", {})

        # Update session status
        self.sessions[session_name].stopped = True
        self.sessions[session_name].active = False
        self.sessions[session_name].end_time = datetime.now().strftime("%Y%m%d_%H%M%S")

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
            
            if self.sessions[session_name].scheduled == True:
                f.write(f"Session is scheduled to run between {self.sessions[session_name].scheduled_start_time} and {self.sessions[session_name].scheduled_end_time}")


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

        
    def _write_line_to_file(self, session_name: str, line: str) -> None:
        filename = self._get_session_info_file(session_name)
        with open(filename, "a") as f:
            f.write(line)
        

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



    """Session Monitoring"""
    def _monitor_sessions(self):
        cycle_count = 0
        while True:
            cycle_count += 1
            if cycle_count % 10 == 0:
                self.logger.info(f"Recording Monitor cycle {cycle_count}")

            current_time = datetime.now().strftime("%H:%M")
            for session_name, session in self.sessions.items():
                try:
                    if session.stopped:
                        continue

                    self.logger.info(f"{session_name} is not yet stopped")
            
                    if session.scheduled:
                        if current_time == session.scheduled_start_time and not session.active:
                            self.logger.info(f"Starting scheduled session {session_name}")
                            self._write_line_to_file(session_name, f"Starting scheduled session at {current_time}")
                            self.start_scheduled_session(session_name)
                        elif current_time == session.scheduled_end_time and session.active:
                            self.logger.info(f"Ending scheduled session {session_name}")
                            self._write_line_to_file(session_name, f"Ending scheduled session at {current_time}")
                            self.stop_scheduled_session(session_name)
                        else:
                            self.logger.info(f"{session_name}, time is {current_time}, active {session.active}, error {session.error}, scheduled start time {session.scheduled_start_time}, scheduled end time {session.scheduled_end_time}")
                    
                    if session.active:

                        healthy = True # Begin by assuming session is healthy
                        
                        # Check if all modules are recording
                        for module_id in session.modules:
                            error_message = "Not recording modules: "
                            if not self.facade.is_module_recording(module_id):
                                self.logger.info(f"Error in {session}, {module_id} is not recording")
                                healthy = False
                                error_message += f"{module_id} "
                            
                        if not healthy:
                            self.sessions[session_name].error = True
                            self.sessions[session_name].error_message = error_message 
                        else:
                            self.sessions[session_name].error = False
                            self.sessions[session_name].error_message = ""
                except Exception as e:
                    self.logger.error(f"Error monitoring recording sessions: {e}")

            time.sleep(1)

    
    def start_scheduled_session(self, session_name: str) -> None:
        params = {
            "duration": 0,
            "session_name": session_name
        }
        target = self.sessions[session_name].target
        self.facade.send_command(target, "start_recording", params)
        self.sessions[session_name].active = True
        self.facade.update_sessions(self.sessions)


    def stop_scheduled_session(self, session_name: str) -> None:
        target = self.sessions[session_name].target
        self.facade.send_command(target, "stop_recording", {})
        self.sessions[session_name].active = False
        self.facade.update_sessions(self.sessions)
