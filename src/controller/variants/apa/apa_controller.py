"""
A controller with APA specific functionality.

Inherits the habitat controller class.

Serves up the system GUI and binds appropriate routes between GUI buttons and module commands.

@author: Andrew SG
@date: 080725
"""

import os
import sys

# Add the current directory to the path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shock_automation import DEFAULT_MODE, DEFAULTS, ShockAutomation

from controller.controller import Controller


class APAController(Controller):
    def __init__(self):
        super().__init__()
        self.config.load_controller_config("apa_controller_config.json")
        self.web.handle_special_module_status = self.handle_special_module_status
        self.web.handle_special_command = self.handle_special_command

        # Grid arming, shock mode and automated shocking
        # (shock_automation.py). The apa_arduino module only pulses while
        # told to; whether and when lives here.
        self.shock_automation = ShockAutomation(
            send_command=self.facade.send_command,
            get_modules=self.facade.get_modules,
            get_active_sessions=self._active_session_names,
            get_settings=lambda: {
                k: self.config.get(f"apa.auto_shock.{k}", v) for k, v in DEFAULTS.items()},
            get_mode=lambda: self.config.get("apa.shock_mode", DEFAULT_MODE),
            save_mode=lambda mode: self.config.set("apa.shock_mode", mode),
            get_share_path=self.facade.get_share_path,
            emit=lambda state: self.web.socketio.emit("apa_shock_state", state),
            logger=self.logger,
        )
        self._register_shock_handlers()

    def _active_session_names(self) -> list[str]:
        return [name for name, s in self.facade.get_recording_sessions().items()
                if str(getattr(s, "state", "")) in ("active", "error")]

    def _register_shock_handlers(self):
        sio, auto = self.web.socketio, self.shock_automation

        @sio.on("get_apa_shock_state")
        def handle_get_apa_shock_state(data=None):
            auto._emit_state(force=True)

        @sio.on("set_shock_mode")
        def handle_set_shock_mode(data=None):
            if not self.web._require_auth("auth_required"):
                return
            mode = (data or {}).get("mode")
            if not auto.set_mode(mode):
                self.web.socketio.emit("apa_shock_error", {"error": f"Unknown shock mode: {mode}"})

        @sio.on("arm_grid")
        def handle_arm_grid(data=None):
            if not self.web._require_auth("auth_required"):
                return
            if not auto.arm(reason="by hand"):
                self.web.socketio.emit("apa_shock_error", {"error": "Dry run: the grid isn't armed"})

        # Not auth-gated: these can only make the rig safer.
        @sio.on("disarm_grid")
        def handle_disarm_grid(data=None):
            auto.disarm(reason="by hand")

        @sio.on("stop_all_shocks")
        def handle_stop_all_shocks(data=None):
            auto.stop_all()

    def handle_special_command(self, module_id, command, params) -> bool:
        """Manual shocks from the dashboard only while armed in manual mode.
        Stops always go through."""
        if command == "activate_shock" and not self.shock_automation.manual_allowed():
            self.shock_automation.refuse_manual()
            return True
        return False

    def start(self) -> bool:
        self.shock_automation.start()
        return super().start()

    def stop(self) -> bool:
        self.shock_automation.stop()
        return super().stop()

    def configure_controller(self, updated_keys: list[str] | None):
        pass

    def handle_special_module_status(self, module_id: str, status: dict):
        match status.get('type'):
            case "arduino_state":
                self.shock_automation.on_arduino_state(status.get("state") or {})
                self.web.socketio.emit("arduino_state", status)
                return True
            case "shock_started_being_delivered":
                self.web.socketio.emit("shock_started_being_delivered", status)
                return True
            case "shock_stopped_being_delivered":
                self.web.socketio.emit("shock_stopped_being_delivered", status)
                return True
            case "zone_entered" | "zone_exited" | "zone_state":
                self.shock_automation.on_zone_status(module_id, status)
                return True
            case _:
                return False

if __name__ == "__main__":
    controller = APAController()
    try:
        # Start the main loop
        controller.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        controller.stop()
    except Exception as e:
        print(f"\nError: {e}")
        controller.stop()
