"""
Habitat System - PTP Functions

This is the base class for all peripheral modules in the Habitat system.

Author: Andrew SG
Created: 31/03/2025
License: GPLv3
"""

import subprocess

# TODO: interpretation of status stdout
# TODO: suppress printing of stdout, maybe just store it in a variable?

# ptp4l service - this should be activatedd first.

def start_ptp4l():
    subprocess.run(["sudo", "systemctl", "start", "ptp4l.service"])
    
def restart_ptp4l():
    subprocess.run(["sudo", "systemctl", "start", "ptp4l.service"])
    
def stop_ptp4l():
    subprocess.run(["sudo", "systemctl", "stop", "ptp4l.service"])

def status_ptp4l():
    subprocess.run(["sudo", "systemctl", "status", "ptp4l.service"])
    
# phc2sys service - this should be activated after ptp4l.
    
def start_phc2sys():
    subprocess.run(["sudo", "systemctl", "start", "phc2sys.service"])
    
def restart_phc2sys():
    subprocess.run(["sudo", "systemctl", "start", "phc2sys.service"])
    
def stop_phc2sys():
    subprocess.run(["sudo", "systemctl", "stop", "phc2sys.service"])

def status_phc2sys():
    subprocess.run(["sudo", "systemctl", "status", "phc2sys.service"])
