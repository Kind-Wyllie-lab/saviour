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


        self.module_checks = {
            self._check_something,
            self._check_something_else
        }

        self.template_commands = {
            "do_this": self._do_this,
            "get_something": self._get_something,
            "do_that": self._do_that
        }

        self.command.set_commands(self.template_commands)

        # Intialise special stuff


    @command()
    def _do_this(self):
        x = self.config.get("template.x", 2) 
        y = self.config.get("template.y", 3) 
        timestamp = time.time_ns()

        # Do something
        d = []
        for i in range(10):
            z = x + (i**y)
            d.append[z]


    @command()
    def _get_something(self):
        response = {
            "something": random.randint(1,100),
            "something_else": random.randint(1,100)
        }
        return response


    @command()
    def _do_that(self):
        cmd = [
            "touch", "that.txt"
        ]
        subprocess.run(cmd)


    """Config"""
    def configure_module(self, updated_keys: Optional[list[str]]):
        # Configure self however necessary
        special_key = "x"
        if special_key in updated_keys:
            self.logger.info(f"{special_key} was changed!")


    """Recording"""
    def _start_new_recording(self):
        # Start recording session
        pass
    

    def _start_next_recording_segment(self):
        # Segment based recording
        pass


    def _stop_recording(self):
        pass


    """Self Check"""
    @check()
    def _check_something(self):
        if self.config.get("x") == self.config.get("y"):
            return False, "x must not equal y"
        else: 
            return True

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