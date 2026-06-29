"""
Habitat controller.

Inherits the base Controller class and serves the Habitat GUI.

@author: Andrew SG
@date: 080725
"""

import sys
import os
import logging
from datetime import date
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from controller.controller import Controller


class HabitatController(Controller):
    def __init__(self):
        super().__init__()

        self.config.load_controller_config("habitat_controller_config.json")

        self.web.handle_special_module_status = self.handle_special_module_status

        self._register_habitat_socket_handlers()


    # ── Socket event handlers ─────────────────────────────────────────────────

    def _register_habitat_socket_handlers(self) -> None:
        sio = self.web.socketio

        def handle_get_habitat_config(data=None):
            sio.emit("habitat_config", self._get_habitat_config())

        def handle_start_habitat_recording(data=None):
            result = self.start_habitat_recording()
            if not result.get("success"):
                sio.emit("session_error", {"error": result.get("error")})

        def handle_stop_habitat_recording(data=None):
            self.stop_habitat_recording()

        sio.on_event("get_habitat_config",      handle_get_habitat_config)
        sio.on_event("start_habitat_recording", handle_start_habitat_recording)
        sio.on_event("stop_habitat_recording",  handle_stop_habitat_recording)


    # ── Habitat config ────────────────────────────────────────────────────────

    def _get_habitat_config(self) -> dict:
        config = self.config.get_all()
        hcfg = config.get("habitat", {})
        # controller.name is what the user sets in the Settings page
        name = (config.get("controller", {}).get("name")
                or hcfg.get("name")
                or "Habitat")
        return {
            "name":       name,
            "audioStart": hcfg.get("audio_start", "20:00"),
            "audioEnd":   hcfg.get("audio_end",   "02:00"),
        }


    # ── Recording control ─────────────────────────────────────────────────────

    def start_habitat_recording(self) -> dict:
        """Start a habitat recording campaign.

        Creates:
          - A continuous camera session that records until Stop is pressed.
          - A daily-scheduled microphone session that auto-starts / stops within
            the configured audio window each day.

        Session names are  {habitat_name}_cameras_{YYYY-MM-DD}  and
        {habitat_name}_audio_{YYYY-MM-DD},  using today's date so each
        campaign start is unique on the NAS.

        Idempotent: if a camera session is already active the call returns an
        error rather than creating a duplicate.
        """
        hcfg = self._get_habitat_config()
        name  = hcfg["name"]
        today = date.today().strftime("%Y%m%d")

        # Guard: don't start if cameras already recording
        existing = self.facade.get_recording_sessions()
        for s in existing.values():
            if (s.target == "camera"
                    and s.state in ("active", "error")
                    and s.session_name.startswith(name)):
                return {"success": False, "error": "Habitat recording is already active"}

        errors = []

        camera_result = self.facade.create_session(
            session_name=f"{name}_cameras_{today}",
            target="camera",
            raw_name=True,
        )
        if not camera_result.get("success"):
            errors.append(f"cameras: {camera_result.get('error')}")

        audio_result = self.facade.create_scheduled_session(
            session_name=f"{name}_audio_{today}",
            target="microphone",
            start_time=hcfg["audioStart"],
            end_time=hcfg["audioEnd"],
            raw_name=True,
        )
        if not audio_result.get("success"):
            errors.append(f"audio: {audio_result.get('error')}")

        if errors:
            return {"success": False, "error": "; ".join(errors)}

        self.logger.info(
            f"Habitat recording started: cameras={camera_result}, audio={audio_result}"
        )
        return {"success": True}


    def stop_habitat_recording(self) -> None:
        """Stop all active habitat sessions (cameras + audio schedule)."""
        name = self._get_habitat_config()["name"]
        existing = self.facade.get_recording_sessions()
        for session_name, session in existing.items():
            if (session.session_name.startswith(name)
                    and session.state in ("active", "scheduled", "error")):
                self.facade.stop_session(session_name)
                self.logger.info(f"Habitat stop: sent stop to '{session_name}'")


    # ── Controller overrides ──────────────────────────────────────────────────

    def configure_controller(self, updated_keys: Optional[list[str]]):
        pass


    def handle_special_module_status(self, module_id: str, status: dict):
        match status.get('type'):
            case _:
                self.logger.warning(
                    f"Habitat controller has no logic for {status.get('type')} from {module_id}"
                )
                return False


if __name__ == "__main__":
    controller = HabitatController()
    try:
        controller.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        controller.stop()
    except Exception as e:
        print(f"\nError: {e}")
        controller.stop()
