"""
Habitat controller.

Inherits the base Controller class and serves the Habitat GUI.

@author: Andrew SG
@date: 080725
"""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from gate_map import GateMapStore
from gate_map_service import GateMapService, register_socket_handlers, rfid_module_ids

from controller.controller import Controller


class HabitatController(Controller):
    def __init__(self):
        super().__init__()

        self.config.load_controller_config("habitat_controller_config.json")

        self.web.handle_special_module_status = self.handle_special_module_status

        # RFID gate mapping (plans/rfid-gate-mapping.md): the Gates page.
        self.gate_map = GateMapService(
            GateMapStore(),
            emit=self.web.socketio.emit,
            send_command=self.facade.send_command,
            rfid_module_ids=self._rfid_module_ids,
            logger=self.logger,
        )
        self.web.module_status_listeners.append(self.gate_map.on_module_status)

        self._register_habitat_socket_handlers()


    # ── Socket event handlers ─────────────────────────────────────────────────

    def _register_habitat_socket_handlers(self) -> None:
        sio = self.web.socketio

        def handle_get_habitat_config(data=None):
            sio.emit("habitat_config", self._get_habitat_config())

        sio.on_event("get_habitat_config", handle_get_habitat_config)
        self._register_gate_map_handlers()

    def _rfid_module_ids(self) -> list[str]:
        return rfid_module_ids(self.facade.get_modules())

    def _register_gate_map_handlers(self) -> None:
        register_socket_handlers(self.web.socketio, self.gate_map,
                                 require_auth=self.web._require_auth,
                                 logger=self.logger)


    # ── Habitat config ────────────────────────────────────────────────────────

    def _get_habitat_config(self) -> dict:
        # Just the display name for HabitatRecordingControl's top banner;
        # controller.name is what the user sets on the Settings page.
        return {"name": self.config.get("controller.name") or "Habitat"}


    # ── Controller overrides ──────────────────────────────────────────────────

    def configure_controller(self, updated_keys: list[str] | None):
        pass


    def handle_special_module_status(self, module_id: str, status: dict):
        match status.get('type'):
            case _:
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
