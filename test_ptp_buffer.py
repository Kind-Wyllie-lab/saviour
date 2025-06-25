#!/usr/bin/env python3
"""
Test script to demonstrate PTP buffer functionality
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from controller.controller_ptp_manager import PTPManager, PTPRole
import logging

def main():
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    try:
        # Create PTP manager (this will require root privileges)
        ptp_manager = PTPManager(role=PTPRole.MASTER, logger=logger)
        
        print("PTP Manager initialized successfully!")
        print(f"Role: {ptp_manager.role.value}")
        print(f"Interface: {ptp_manager.interface}")
        
        # Start PTP services
        print("\nStarting PTP services...")
        ptp_manager.start()
        
        # Let it run for a few seconds to collect some data
        import time
        print("Collecting PTP data for 10 seconds...")
        time.sleep(10)
        
        # Show buffer contents
        print("\n=== PTP Buffer (Unified) ===")
        ptp_data = ptp_manager.get_ptp_buffer(max_entries=10)
        for i, entry in enumerate(ptp_data):
            print(f"Entry {i+1}:")
            print(f"  Timestamp: {entry['timestamp']}")
            print(f"  PTP4L - Offset: {entry['ptp4l_offset']} ns, Freq: {entry['ptp4l_freq']}")
            print(f"  PHC2SYS - Offset: {entry['phc2sys_offset']} ns, Freq: {entry['phc2sys_freq']}")
            print()
        
        # Show statistics
        print("\n=== PTP Statistics ===")
        stats = ptp_manager.get_offset_statistics()
        
        print("PTP4L Offset Statistics:")
        for key, value in stats['ptp4l_offset'].items():
            print(f"  {key}: {value}")
        
        print("\nPTP4L Frequency Statistics:")
        for key, value in stats['ptp4l_freq'].items():
            print(f"  {key}: {value}")
        
        print("\nPHC2SYS Offset Statistics:")
        for key, value in stats['phc2sys_offset'].items():
            print(f"  {key}: {value}")
        
        print("\nPHC2SYS Frequency Statistics:")
        for key, value in stats['phc2sys_freq'].items():
            print(f"  {key}: {value}")
        
        # Show current status
        print("\n=== Current Status ===")
        status = ptp_manager.get_status()
        for key, value in status.items():
            print(f"{key}: {value}")
        
        # Stop PTP services
        print("\nStopping PTP services...")
        ptp_manager.stop()
        
    except Exception as e:
        print(f"Error: {e}")
        print("Note: This script requires root privileges to run PTP services")

if __name__ == "__main__":
    main() 