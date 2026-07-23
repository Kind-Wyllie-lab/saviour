#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SAVIOUR System - Template Module

Author: Andrew SG
Created: 05/02/2026
"""
# Base Imports
import sys
import os
import random
import subprocess
from typing import Optional
import time

# Saviour Imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module, command, check

class TemplateModule(Module):
    def __init__(self, module_type="template"):
        super().__init__(module_type)

        self.config.load_module_config("template_config.json")

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
    def do_that(self):
        subprocess.run(["touch", "that.txt"])


    """Config"""
    def configure_module_special(self, updated_keys: Optional[list[str]]):
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
        print("\nShuttind down...")
        template.stop()

if __name__ == "__main__":
    main()