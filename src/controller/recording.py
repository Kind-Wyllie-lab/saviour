"""
Recording manager for the SAVIOUR Controller

Author: Andrew SG
Created: 26/01/2026
"""

class Recording():
    def __init__(self):
        self.current_recording_session_name: str = None
        self.recording: bool = False

    def start_recording(self, target, session_name: str, duration: int):
        """

        Handles a command to start a new recording session.

        Args:
            - target: The module or modules to start recording (e.g. all, camera_dc67, group_2)
            - session_name: The provided session name 
            - duration: Recording session duration in seconds
        """
        # Add timestamp to session name
        session_name += ("_" + datetime.now().strftime("%Y%M%d_%H%m$S"))


        self.api.send_command("start_recording", duration, session_name)