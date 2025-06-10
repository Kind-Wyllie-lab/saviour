#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PTP Manager

The PTP manager is responsible for initializing and managing PTP functions.
Important things to note about PTP on raspberry pi 5:
- By default, systemd-timesyncd is used to sync the system clock. It must be disabled for PTP to work.
- PTP updates every
"""


import subprocess
import threading
import time
import logging
import os
import sys
import tempfile
import re
from enum import Enum
from typing import Callable, Dict, Any, Optional

class PTPRole(Enum):
    MASTER = "master"
    SLAVE = "slave"

class PTPError(Exception): # We define a custom error class
    pass

class PTPManager:
    def __init__(self,
                 role=PTPRole.MASTER,
                 interface='eth0',
                 logger: logging.Logger = None):

        """Initialize the PTP manager

        Args:
            logger: Logger instance
            interface: The network interface, typically eth0
            role: The PTP role - slave for modules, master for controllers
        """

        # Check for root privileges first
        if os.geteuid() != 0:
            raise PTPError("(PTP MANAGER) This program must be run as root (use sudo). PTP requires root privileges to adjust system time.")

        # Assign basic params
        self.role = role
        self.interface = interface
        self.logger = logger

        # Check for required packages
        self._check_required_packages()

        # Validate interface
        self._validate_interface()

        # Create temporary config file
        self.config_file = self._create_config_file()

        # Configure ptp4l arguments based on role
        if role == PTPRole.MASTER:
            self.ptp4l_args = ['ptp4l', '-i', interface, '-m', '-l', '6', '-f', self.config_file]
            # For master, use autoconfiguration with system clock sync
            self.phc2sys_args = ['phc2sys', '-a', '-r', '-r']  # -r twice to consider system clock as time source
        else:
            # self.ptp4l_args = ['ptp4l', '-i', interface, '-s',  '-l', '6', '-f', self.config_file]
            self.ptp4l_args = ['ptp4l', '-i', interface, '-s', '-m']
            # For slave, use manual configuration with the interface as master
            # self.phc2sys_args = ['phc2sys', '-s', interface, '-w', '-l', '6']
            self.phc2sys_args = ['phc2sys', '-s', '/dev/ptp0', '-w', '-m']

        self.ptp4l_proc = None
        self.phc2sys_proc = None
        self.monitor_thread = None
        self.running = False
        self.status = 'not running'
        self.last_sync_time = None
        self.last_offset = None
        self.last_freq = None
        self.active_ptp4l_processes = None
        self.active_phc2sys_processes = None

    def _check_required_packages(self):
        """Check if required PTP packages are installed."""
        required_packages = ['ptp4l', 'phc2sys']
        missing_packages = []

        for package in required_packages:
            try:
                subprocess.run(['which', package], check=True, capture_output=True)
            except subprocess.CalledProcessError:
                missing_packages.append(package)

        if missing_packages:
            raise PTPError(f"(PTP MANAGER) Missing required packages: {', '.join(missing_packages)}. "
                           f"Please install them using: sudo apt-get install linuxptp")

    def _validate_interface(self):
        """Check if the interface exists and supports PTP."""
        # Check if interface exists
        if not os.path.exists(f'/sys/class/net/{self.interface}'):
            raise PTPError(f"(PTP MANAGER) Interface {self.interface} does not exist")

        # Check if interface is up
        try:
            with open(f'/sys/class/net/{self.interface}/operstate', 'r') as f:
                if f.read().strip() != 'up':
                    self.logger.warning(f"(PTP MANAGER) Interface {self.interface} is not up")
        except IOError:
            self.logger.warning(f"(PTP MANAGER) Could not check interface {self.interface} state")

        # Check if interface supports PTP
        try:
            result = subprocess.run(['ethtool', '-T', self.interface],
                                    capture_output=True, text=True)
            if 'PTP Hardware Clock' not in result.stdout:
                self.logger.warning(f"(PTP MANAGER) Interface {self.interface} may not support PTP hardware timestamping")
        except subprocess.CalledProcessError:
            self.logger.warning(f"(PTP MANAGER) Could not check PTP support for {self.interface}")

    def _create_config_file(self):
        """Create a temporary configuration file for ptp4l."""
        config_content = f"""
        [global]
        verbose               1
        time_stamping        hardware
        tx_timestamp_timeout 1
        logAnnounceInterval  1
        logSyncInterval      0
        logMinDelayReqInterval 0
        """
        # Create temporary file
        fd, path = tempfile.mkstemp(prefix='ptp4l_', suffix='.conf')
        with os.fdopen(fd, 'w') as f:
            f.write(config_content)
        self.logger.debug(f"(PTP MANAGER) Created PTP config file at {path}")
        return path

    def _stop_timesyncd(self):
        """
        systemd-timesyncd is a ntp daemon which I have found runs by default on raspberry pi 5.
        It will interfere with phc2sys function if it is running.
        This should be made to happen during setup, but we might as well do it here as well.
        """
        self.logger.info("(PTP MANAGER) Attempting to stop systemd.timesyncd")
        try:
            subprocess.Popen(["sudo",
                              "systemctl",
                              "stop",
                              "systemd-timesyncd"])

        except Exception as e:
            self.logger.error(f"(PTP MANAGER) Failed to stop timesyncd: {str(e)}")
            raise

    def _check_ptp_running(self):
        """
        Checks if ptp4l or phc2sys is running.
        """

        p = re.compile('\d+') # The regex pattern that will be used to find processes

        output, _ = subprocess.Popen(["pgrep","ptp4l"],stdout=subprocess.PIPE).communicate()
        self.active_ptp4l_processes = p.findall(str(output))

        output, _ = subprocess.Popen(["pgrep","phc2sys"],stdout=subprocess.PIPE).communicate()
        self.active_phc2sys_processes = p.findall(str(output))

    def _kill_ptp_processes(self):
        """
        Kill all active ptp4l and phc2sys processes. Part of cleanup procedure.
        """
        cmd = [] # Empty array for command arguments
        for proc in self.active_ptp4l_processes:
            cmd.append(proc)
        for proc in self.active_phc2sys_processes:
            cmd.append(proc)
        if not cmd:
            self.logger.info("(PTP MANAGER) Did not find any active processes to kill.")
            pass
        else:
            cmd.append("kill")
            cmd.reverse() # Reverse so that kill comes at the front - process order shouldn't matter here
            self.logger.info(f"(PTP MANAGER) Killing all ptp processes with command: {cmd}")
            subprocess.Popen(cmd)


    def start(self):
        self.logger.info(f"(PTP MANAGER) Starting PTP in {self.role.value} mode on {self.interface}")

        # Ensure timesyncd is disabled, or else phc2sys won't work!
        self._stop_timesyncd()

        # Check for any active ptp processes
        self._check_ptp_running()

        # Kill them so we start clean
        self._kill_ptp_processes()

        # Start ptp4l with error capture
        try:
            self.ptp4l_proc = subprocess.Popen(
                self.ptp4l_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
                universal_newlines=True
            )

            # Check if process started successfully
            time.sleep(0.5)  # Give it a moment to start
            if self.ptp4l_proc.poll() is not None: # poll() checks the process has terminated.
                error = self.ptp4l_proc.stderr.read()
                raise PTPError(f"(PTP MANAGER) ptp4l failed to start: {error}")

            self.logger.info("(PTP MANAGER) ptp4l started successfully")

            # Start phc2sys
            self.phc2sys_proc = subprocess.Popen(
                self.phc2sys_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # Line buffered
                universal_newlines=True
            )

            # Check if process started successfully
            time.sleep(0.5)
            if self.phc2sys_proc.poll() is not None:
                error = self.phc2sys_proc.stderr.read()
                self.ptp4l_proc.terminate()
                raise PTPError(f"(PTP MANAGER) phc2sys failed to start: {error}")

            self.logger.info("(PTP MANAGER) phc2sys started successfully")

        except Exception as e:
            self.logger.error(f"(PTP MANAGER) Failed to start PTP processes: {str(e)}")
            self.stop()
            raise

        self.running = True
        self.status = 'starting'
        self.monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self.monitor_thread.start()

    def _monitor(self):
        while self.running:
            for proc, name in [(self.ptp4l_proc, 'ptp4l'), (self.phc2sys_proc, 'phc2sys')]:
                if proc and proc.poll() is not None:
                    error = proc.stderr.read() if proc.stderr else "No error output"
                    self.status = f'{name} stopped'
                    self.logger.error(f"(PTP MANAGER) {name} process stopped unexpectedly! Error: {error}")
                    self.running = False
                    return

                line = proc.stdout.readline() if proc and proc.stdout else ''
                if line:
                    line = line.strip()
                    self.logger.debug(f"{name}: {line}")

                    # Parse offset information
                    # TODO: Distinguish ptp4l and phc2sys offset
                    if 'master offset' in line or 'offset' in line:
                        try:
                            # Extract offset value from line
                            offset_str = line.split('offset')[1].split()[0]
                            self.last_offset = float(offset_str)
                            self.last_sync_time = time.time()
                            self.status = 'synchronized'
                            self.logger.info(f"(PTP MANAGER) PTP offset: {self.last_offset} ns")
                        except (IndexError, ValueError):
                            self.logger.warning(f"(PTP MANAGER) Could not parse offset from line: {line}")

                    # Parse freq correction information
                    if 'freq' in line:
                        try:
                            # Extract freq correction from line
                            freq_str = line.split('freq')[1].split()[0]
                            self.last_freq = int(freq_str)
                        except(IndexError, ValueError):
                            self.logger.warning(f"(PTP MANAGER) Could not parse freq from line: {line}")

                    # Check for successful sync
                    if 'synchronized' in line.lower():
                        self.status = 'synchronized'
                        self.logger.info("(PTP MANAGER) PTP synchronized successfully")

                    # Check for port state changes
                    if 'port state' in line.lower():
                        self.logger.info(f"(PTP MANAGER) PTP port state change: {line}")
                        if 'LISTENING' in line:
                            self.status = 'listening'
                        elif 'UNCALIBRATED' in line:
                            self.status = 'uncalibrated'
                        elif 'SLAVE' in line:
                            self.status = 'slave'
                        elif 'MASTER' in line:
                            self.status = 'master'

                    # Check for errors
                    if 'FAULT' in line or 'error' in line.lower():
                        self.status = 'error'
                        self.logger.error(f"(PTP MANAGER) PTP error detected: {line}")

                    # Check for clock selection
                    if 'selected' in line and 'PTP clock' in line:
                        self.logger.info(f"(PTP MANAGER) PTP clock selected: {line}")

                    # Check for frequency adjustment
                    if 'frequency' in line and 'adjustment' in line:
                        self.logger.info(f"(PTP MANAGER) PTP frequency adjustment: {line}")

                    # Check for announce messages
                    if 'announce' in line.lower():
                        self.logger.debug(f"(PTP MANAGER) PTP announce: {line}")

                    # Check for sync messages
                    if 'sync' in line.lower() and 'message' in line.lower():
                        self.logger.debug(f"(PTP MANAGER) PTP sync message: {line}")

            time.sleep(0.1)

    def stop(self):
        self.running = False
        if self.ptp4l_proc:
            self.ptp4l_proc.terminate()
            try:
                self.ptp4l_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.ptp4l_proc.kill()

        if self.phc2sys_proc:
            self.phc2sys_proc.terminate()
            try:
                self.phc2sys_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.phc2sys_proc.kill()

        # Clean up config file
        try:
            os.remove(self.config_file)
        except OSError:
            pass

        self.status = 'stopped'
        self.logger.info("(PTP MANAGER) Stopped PTP processes.")

    def get_status(self):
        return {
            'role': self.role.value,
            'status': self.status,
            'last_sync': self.last_sync_time,
            'last_offset': self.last_offset,
            'last_freq': self.last_freq,
            'interface': self.interface
        }

    def is_synchronized(self, timeout=5):
        if self.last_sync_time and (time.time() - self.last_sync_time) < timeout:
            return True
        return False

    def get_ptp_time(self):
        # If PTP is synced to system clock, just use time.time()
        return time.time()
