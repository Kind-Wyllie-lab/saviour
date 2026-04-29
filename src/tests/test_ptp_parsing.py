#!/usr/bin/env python3
"""
Test script to verify PTP parsing logic
"""

import sys
import os
import logging
from unittest.mock import patch, MagicMock
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from modules.ptp import PTP, PTPRole


def make_ptp():
    """Instantiate PTP with all environment checks mocked out."""
    with patch("os.geteuid", return_value=0), \
         patch.object(PTP, "_check_required_packages"), \
         patch.object(PTP, "_validate_interface"), \
         patch.object(PTP, "_check_systemd_services"):
        return PTP(role=PTPRole.SLAVE)


def test_ptp4l_parsing():
    """Test ptp4l log parsing with actual log format."""
    ptp_manager = make_ptp()

    test_values = [
        [215, 46728],
        [-35, -45809],
        [-100, 218764],
        [-35, -153196],
        [-156, -154457]
    ]
    test_lines = [
        f"[3777.974] master offset        {test_values[0][0]} s2 freq {test_values[0][1]} path delay      4521",
        f"ptp4l[3778.974]: master offset        {test_values[1][0]} s2 freq {test_values[1][1]} path delay      4554",
        f"[3778.974] master offset        {test_values[2][0]} s2 freq {test_values[2][1]} path delay      4554",
        f"[3779.974] master offset       {test_values[3][0]} s2 freq {test_values[3][1]} path delay      4603",
        f"ptp4l[3780.975]: master offset       {test_values[4][0]} s2 freq {test_values[4][1]} path delay      4610"
    ]

    for i, line in enumerate(test_lines):
        ptp_manager._parse_ptp4l_line(line)
        assert ptp_manager.latest_ptp4l_offset == test_values[i][0]
        assert ptp_manager.latest_ptp4l_freq == test_values[i][1]


def test_phc2sys_parsing():
    """Test phc2sys log parsing with actual log format."""
    ptp_manager = make_ptp()

    test_lines = [
        "[3779.065] CLOCK_REALTIME phc offset       140 s2 freq -114036 delay     55",
        "phc2sys[3780.066]: CLOCK_REALTIME phc offset        67 s2 freq -114067 delay     37",
        "[3780.066] CLOCK_REALTIME phc offset        67 s2 freq -114067 delay     37",
        "[3781.066] CLOCK_REALTIME phc offset      -133 s2 freq -114247 delay     55",
        "phc2sys[3782.066]: CLOCK_REALTIME phc offset      -198 s2 freq -114352 delay     55"
    ]

    for line in test_lines:
        ptp_manager._parse_phc2sys_line(line)

    assert ptp_manager.latest_phc2sys_offset is not None
    assert ptp_manager.latest_phc2sys_freq is not None


def test_buffer_functionality():
    """Test the buffer functionality."""
    ptp_manager = make_ptp()

    ptp_manager.latest_ptp4l_offset = 100
    ptp_manager.latest_ptp4l_freq = -150000
    ptp_manager.latest_phc2sys_offset = 50
    ptp_manager.latest_phc2sys_freq = -110000

    ptp_manager._add_buffer_entry(1234567890.0)

    assert len(ptp_manager.ptp_buffer) == 1

    stats = ptp_manager.get_offset_statistics()
    assert stats is not None
