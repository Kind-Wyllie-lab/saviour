#!/usr/bin/env python3
"""
SAVIOUR System - Template Module

Boilerplate for a new module type. Copy this folder to
src/modules/variants/<your_type>/, rename the files, and also copy
variant.conf.example -> variant.conf so saviour-config's module-type menu
picks it up.

Author: Andrew SG
Created: 05/02/2026
"""
# Base Imports
import os
import random
import sys
import time

# Saviour Imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, check, command


class TemplateModule(Module):
    def __init__(self, module_type="template"):
        super().__init__(module_type)

        self.config.load_module_config("template_config.json")

        # Human-readable description, surfaced in health/status payloads.
        self.description = "Template module (boilerplate — not a real device type)"

        # @command()/@check()-decorated methods below are discovered automatically
        # by Module.__init__ — no manual dict/list registration needed.


    @command()
    def do_this(self):
        x = self.config.get("template.x", 2)
        y = self.config.get("template.y", 3)

        # Do something
        d = [x + (i ** y) for i in range(10)]
        return {"result": "success", "output": d}


    @command()
    def get_something(self):
        return {
            "something": random.randint(1, 100),
            "something_else": random.randint(1, 100),
        }


    @command()
    def do_that(self, message: str = "hello"):
        # Commands may take parameters — the controller sends them as JSON
        # and they arrive as kwargs. Always return a JSON-serialisable dict.
        self.logger.info(f"do_that called with message={message!r}")
        return {"result": "success", "echo": message}


    """Config"""
    def configure_module_special(self, updated_keys: list[str] | None):
        # Called whenever module-specific config changes e.g. reconfigure hardware here
        if updated_keys and "template.x" in updated_keys:
            self.logger.info("template.x was changed!")


    """Recording"""
    def _start_new_recording(self) -> bool:
        # Start recording session
        return True


    def _start_next_recording_segment(self) -> bool:
        # Segment based recording
        return True


    def _stop_recording(self) -> bool:
        return True


    """Self Check"""
    @check()
    def _check_something(self):
        if self.config.get("template.x") == self.config.get("template.y"):
            return False, "x must not equal y"
        return True, "x != y"

def main():
    template = TemplateModule()
    template.start()

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down...")
        template.stop()

if __name__ == "__main__":
    main()
