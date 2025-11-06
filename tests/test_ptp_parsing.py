#!/usr/bin/env python3
"""
Test script to verify PTP parsing logic
"""

import sys
import os
import logging
from unittest.mock import patch
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from modules.ptp import PTP, PTPRole

def test_ptp4l_parsing():
    with patch("os.geteuid", return_value=0):
        """Test ptp4l log parsing with actual log format."""
        print("Testing ptp4l log parsing...")
        
        # Create a PTP manager instance (without starting services)
        logging.basicConfig(level=logging.DEBUG)
        logger = logging.getLogger(__name__)
        
        # Create manager but don't start it
        ptp_manager = PTP(role=PTPRole.SLAVE)
        
        # Test lines from actual ptp4l logs
        test_lines = [
            "[3777.974] master offset        215 s2 freq -154110 path delay      4521",
            "ptp4l[3778.974]: master offset        -35 s2 freq -154295 path delay      4554",
            "[3778.974] master offset        -35 s2 freq -154295 path delay      4554",
            "[3779.974] master offset       -100 s2 freq -154371 path delay      4603",
            "ptp4l[3780.975]: master offset       -156 s2 freq -154457 path delay      4610"
        ]
        
        print("\nParsing ptp4l test lines:")
        for i, line in enumerate(test_lines):
            print(f"\nLine {i+1}: {line}")
            ptp_manager._parse_ptp4l_line(line)
            print(f"  Latest offset: {ptp_manager.latest_ptp4l_offset}")
            print(f"  Latest freq: {ptp_manager.latest_ptp4l_freq}")

def test_phc2sys_parsing():
    """Test phc2sys log parsing with actual log format."""
    with patch("os.geteuid", return_value=0):
        print("\n\nTesting phc2sys log parsing...")
        
        # Create a PTP manager instance (without starting services)
        logging.basicConfig(level=logging.DEBUG)
        logger = logging.getLogger(__name__)
        
        # Create manager but don't start it
        ptp_manager = PTP(role=PTPRole.SLAVE)
        
        # Test lines from actual phc2sys logs
        test_lines = [
            "[3779.065] CLOCK_REALTIME phc offset       140 s2 freq -114036 delay     55",
            "phc2sys[3780.066]: CLOCK_REALTIME phc offset        67 s2 freq -114067 delay     37",
            "[3780.066] CLOCK_REALTIME phc offset        67 s2 freq -114067 delay     37",
            "[3781.066] CLOCK_REALTIME phc offset      -133 s2 freq -114247 delay     55",
            "phc2sys[3782.066]: CLOCK_REALTIME phc offset      -198 s2 freq -114352 delay     55"
        ]
        
        print("\nParsing phc2sys test lines:")
        for i, line in enumerate(test_lines):
            print(f"\nLine {i+1}: {line}")
            ptp_manager._parse_phc2sys_line(line)
            print(f"  Latest offset: {ptp_manager.latest_phc2sys_offset}")
            print(f"  Latest freq: {ptp_manager.latest_phc2sys_freq}")
        
def test_buffer_functionality():
    """Test the buffer functionality."""
    with patch("os.geteuid", return_value=0):
        print("\n\nTesting buffer functionality...")
        
        logging.basicConfig(level=logging.INFO)
        
        ptp_manager = PTP(role=PTPRole.SLAVE)
        
        # Simulate some data collection
        ptp_manager.latest_ptp4l_offset = 100
        ptp_manager.latest_ptp4l_freq = -150000
        ptp_manager.latest_phc2sys_offset = 50
        ptp_manager.latest_phc2sys_freq = -110000
        
        ptp_manager._add_buffer_entry(1234567890.0)
        
        print(f"Buffer size: {len(ptp_manager.ptp_buffer)}")
        if ptp_manager.ptp_buffer:
            entry = ptp_manager.ptp_buffer[0]
            print(f"Buffer entry: {entry}")
        
        # Test statistics
        stats = ptp_manager.get_offset_statistics()
        print(f"\nStatistics: {stats}")

if __name__ == "__main__":
    print("PTP Parsing Test")
    print("=" * 50)
    
    try:
        # Test ptp4l parsing
        ptp4l_manager = test_ptp4l_parsing()
        
        # Test phc2sys parsing
        phc2sys_manager = test_phc2sys_parsing()
        
        # Test buffer functionality
        test_buffer_functionality()
        
        print("\n" + "=" * 50)
        print("All tests completed successfully!")
        
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc() 