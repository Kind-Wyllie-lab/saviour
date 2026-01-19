#!/usr/bin/env python3
"""
Test script for camera module

"""

from src.controller.controller import Controller
def test_controller():
    c = Controller()
    assert c