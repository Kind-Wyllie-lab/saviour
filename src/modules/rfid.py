"""
Habitat System - RFID Module Class

This module is used to read RFID tags from a RFID reader.

Author: Andrew SG
Created: 11/04/2025
License: GPLv3
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from src.modules.module import Module
import random
import time
import threading

class RFIDModule(Module):
    """Class to represent a RFID module"""

    def __init__(self, config: dict):
        """Initialize the RFID module"""
        super().__init__(module_type="rfid", config=config) # call the parent class constructor
        
        # RFID Thread
        self.running = True
        self.rfid_thread = threading.Thread(target=self.read_fake_rfid_thread, daemon=True)
        self.rfid_thread.start()

    def read_rfid(self):
        """Read a RFID tag from the RFID reader"""
        pass

    def read_fake_rfid(self):
        """Read a fake RFID tag from the RFID reader"""
        return random.randint(1000000000000000, 9999999999999999)

    def read_fake_rfid_thread(self):
        """Read a fake RFID tag from the RFID reader in a separate thread"""
        while self.running:
            n = random.randint(1,10)
            if n == 1:
                print("RFID tag read: ", self.read_fake_rfid())
            else:
                print("0")
            time.sleep(1)
