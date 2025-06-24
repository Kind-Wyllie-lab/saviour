#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script to verify export command parsing
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from modules.module_export_manager import ExportManager

def test_export_destination_parsing():
    """Test that export destination parsing works correctly"""
    
    # Test cases
    test_cases = [
        ("controller", ExportManager.ExportDestination.CONTROLLER),
        ("nas", ExportManager.ExportDestination.NAS),
        ("CONTROLLER", ExportManager.ExportDestination.CONTROLLER),
        ("NAS", ExportManager.ExportDestination.NAS),
    ]
    
    print("Testing ExportDestination.from_string() method:")
    for input_str, expected in test_cases:
        try:
            result = ExportManager.ExportDestination.from_string(input_str)
            status = "✓ PASS" if result == expected else "✗ FAIL"
            print(f"  {input_str} -> {result.value} {status}")
        except ValueError as e:
            print(f"  {input_str} -> ERROR: {e} ✗ FAIL")
    
    print("\nTesting invalid destinations:")
    invalid_destinations = ["invalid", "server", "local", ""]
    for invalid in invalid_destinations:
        try:
            result = ExportManager.ExportDestination.from_string(invalid)
            print(f"  {invalid} -> {result.value} ✗ FAIL (should have raised ValueError)")
        except ValueError as e:
            print(f"  {invalid} -> ERROR: {e} ✓ PASS")

def test_command_parsing_simulation():
    """Simulate the command parsing logic"""
    
    print("\n" + "="*50)
    print("Testing command parsing simulation:")
    print("="*50)
    
    # Test cases for command parsing
    test_commands = [
        # (command_string, expected_filename, expected_destination, expected_experiment_name)
        ("export_recordings destination=nas", "all", "nas", None),
        ("export_recordings destination=controller", "all", "controller", None),
        ("export_recordings filename=latest destination=nas", "latest", "nas", None),
        ("export_recordings filename=all destination=controller experiment_name=test", "all", "controller", "test"),
        ("export_recordings latest 0 nas", "latest", "nas", None),  # positional format
        ("export_recordings all 0 controller experiment_name=behavioral_study", "all", "controller", "behavioral_study"),
        ("export_recordings latest nas", "latest", "nas", None),  # positional without length
        ("export_recordings all controller", "all", "controller", None),  # positional without length
    ]
    
    for command_str, expected_filename, expected_dest, expected_exp in test_commands:
        print(f"\nCommand: {command_str}")
        
        # Simulate the parsing logic
        parts = command_str.split()
        command = parts[0]
        params = parts[1:] if len(parts) > 1 else []
        
        # Parse parameters - support both positional and key-value formats
        filename = "all"  # Default to export all
        length = 0
        destination = "controller"  # Default destination
        experiment_name = None
        
        if params:
            # First, check if any parameters use key=value format
            has_key_value = any('=' in param for param in params)
            
            if has_key_value:
                # Parse key-value parameters
                for param in params:
                    if param.startswith('filename='):
                        filename = param.split('=', 1)[1]
                    elif param.startswith('length='):
                        length = int(param.split('=', 1)[1])
                    elif param.startswith('destination='):
                        destination = param.split('=', 1)[1]
                    elif param.startswith('experiment_name='):
                        experiment_name = param.split('=', 1)[1]
            else:
                # Parse positional parameters: filename length destination
                if len(params) >= 1:
                    filename = params[0]
                if len(params) >= 2:
                    try:
                        length = int(params[1])
                    except ValueError:
                        # If second parameter is not a number, it might be destination
                        destination = params[1]
                if len(params) >= 3:
                    # Third parameter is destination (if second was length) or experiment_name
                    if length == 0:  # Second parameter was not a number
                        destination = params[2]
                    else:
                        destination = params[2]
                if len(params) >= 4:
                    # Fourth parameter is experiment_name
                    experiment_name = params[3]
        
        # Check results
        filename_ok = filename == expected_filename
        dest_ok = destination == expected_dest
        exp_ok = experiment_name == expected_exp
        
        status = "✓ PASS" if all([filename_ok, dest_ok, exp_ok]) else "✗ FAIL"
        print(f"  Parsed: filename='{filename}', destination='{destination}', experiment_name='{experiment_name}' {status}")
        
        if not all([filename_ok, dest_ok, exp_ok]):
            print(f"  Expected: filename='{expected_filename}', destination='{expected_dest}', experiment_name='{expected_exp}'")

if __name__ == "__main__":
    test_export_destination_parsing()
    test_command_parsing_simulation() 