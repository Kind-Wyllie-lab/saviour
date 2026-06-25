"""
Habitat controller.

Inherits the base Controller class and serves the Habitat GUI.

@author: Andrew SG
@date: 080725
"""

import sys
import os
import logging
import threading
from typing import Optional, List

# Add the current directory to the path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Import habitat controller
from controller.controller import Controller

class HabitatController(Controller):
    def __init__(self):
        super().__init__()

        self.config.load_controller_config("habitat_controller_config.json")

        self.web.handle_special_module_status = self.handle_special_module_status

        self._start_configured_sessions()


    def _start_configured_sessions(self) -> None:
        """Create scheduled sessions declared in habitat_controller_config.json.

        Idempotent: if a scheduled or active session already exists for a target
        type (loaded from disk on startup) it is left untouched.
        """
        config = self.config.get_all()
        sessions_config = config.get("scheduled_sessions", [])
        if not sessions_config:
            return

        existing = self.facade.get_recording_sessions()
        already_scheduled = {
            s.target
            for s in existing.values()
            if s.scheduled and s.state in ("scheduled", "active")
        }

        for sc in sessions_config:
            target = sc.get("target", "all")
            if target in already_scheduled:
                self.logger.info(
                    f"Scheduled session for target '{target}' already exists — skipping"
                )
                continue
            name = sc.get("name") or target
            result = self.facade.create_scheduled_session(
                session_name=name,
                target=target,
                start_time=sc.get("start_time", "00:00"),
                end_time=sc.get("end_time", "23:59"),
                days=sc.get("days") or [],
                researcher=sc.get("researcher"),
            )
            self.logger.info(f"Auto-created scheduled session for '{target}': {result}")


    def configure_controller(self, updated_keys: Optional[list[str]]):
        pass


    def handle_special_module_status(self, module_id: str, status: dict):
        match status.get('type'):
            case _:
                self.logger.warning(f"Habitat controller has no logic for {status.get('type')} from {module_id}")
                return False

if __name__ == "__main__":
    controller = HabitatController()
    try:
        # Start the main loop
        controller.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        controller.stop()
    except Exception as e:
        print(f"\nError: {e}")
        controller.stop()
