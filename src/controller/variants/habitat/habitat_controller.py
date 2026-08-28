"""
Habitat controller.

Inherits the base Controller class and serves the Habitat GUI.

@author: Andrew SG
@date: 080725
"""

import os
import sys

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

        sio.on_event("get_habitat_config", handle_get_habitat_config)


    # ── Habitat config ────────────────────────────────────────────────────────

    def _get_habitat_config(self) -> dict:
        # Just the display name for HabitatRecordingControl's top banner.
        # controller.name is what the user sets on the Settings page; the old
        # habitat.name / audio_start / audio_end keys (and the one-button
        # start_habitat_recording campaign flow they fed) were removed
        # 2026-08-28 -- audio scheduling is a normal scheduled session now.
        return {"name": self.config.get("controller.name") or "Habitat"}


    # ── Controller overrides ──────────────────────────────────────────────────

    def configure_controller(self, updated_keys: list[str] | None):
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
