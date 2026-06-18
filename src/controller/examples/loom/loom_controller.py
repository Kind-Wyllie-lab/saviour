import sys
import os
from typing import Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from controller.controller import Controller


class LoomController(Controller):
    def __init__(self):
        super().__init__()
        self.web.handle_special_module_status = self.handle_special_module_status
        self.config.load_controller_config("loom_controller_config.json")

    def handle_special_module_status(self, module_id: str, status: str):
        match status:
            case _:
                self.logger.warning(f"No logic for {status} from {module_id}")
                return False

    def configure_controller(self, updated_keys: Optional[list[str]]):
        pass


if __name__ == "__main__":
    controller = LoomController()
    try:
        controller.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        controller.stop()
    except Exception as e:
        print(f"\nError: {e}")
        controller.stop()
