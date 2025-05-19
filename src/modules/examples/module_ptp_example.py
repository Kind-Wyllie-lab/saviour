import sys
import os
import time
import argparse
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from module_ptp_manager import PTPManager, PTPRole, PTPError
import logging
import subprocess

def main():
    ptp = PTPManager(role=PTPRole.SLAVE,
                            logger=logging.getLogger("testlogger"))



    ptp.start()

    try:
        while True:
            status = ptp.get_status()
            print("\nPTP Status:")
            print(f"  Role: {status['role']}")
            print(f"  Status: {status['status']}")
            print(f"  Last Sync: {status['last_sync']}")
            print(f"  Last Offset: {status['last_offset']} ns")
            print(f"  Last Freq: {status['last_freq']} ppb")
            print(f"  Synchronized: {ptp.is_synchronized()}")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nShutting down...")
        ptp.stop()

if __name__ == "__main__":
    main()
