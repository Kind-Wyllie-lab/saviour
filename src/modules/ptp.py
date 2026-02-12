#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module PTP Manager

The PTP manager is responsible for initializing and managing PTP functions using systemd services.
This approach is more robust than managing processes directly.
"""


import subprocess
import threading
import time
import logging
import os
import sys
import re
from enum import Enum
from typing import Callable, Dict, Any, Optional

class PTPRole(Enum):
    MASTER = "master"
    SLAVE = "slave"

class PTPError(Exception): # We define a custom error class
    pass

class PTP:
    def __init__(self,
                 role=PTPRole.SLAVE,
                 interface='eth0',
                 history_size=1000):  # Store last 1000 values by default

        """Initialize the PTP manager

        Args:
            logger: Logger instance
            interface: The network interface, typically eth0
            role: The PTP role - slave for modules, master for controllers
            history_size: Number of historical values to keep
        """

        # Check for root privileges first
        if os.geteuid() != 0:
            raise PTPError("This program must be run as root (use sudo). PTP requires root privileges to adjust system time.")

        # Assign basic params
        self.role = role
        self.interface = interface
        self.logger = logging.getLogger(__name__)
        
        # Check for required packages
        self._check_required_packages()
        
        # Validate interface
        self._validate_interface()

        # Service names - define these before checking services
        self.ptp4l_service = 'ptp4l'
        self.phc2sys_service = 'phc2sys'

        # Check if systemd services exist
        self._check_systemd_services()

        # State variables
        self.running = False
        self.status = 'not running'
        self.last_sync_time = None
        self.last_offset = None
        self.last_freq = None
        self.monitor_thread = None
        
        # Unified offset buffer for storing all PTP values by timestamp
        self.ptp_buffer = []
        self.max_buffer_size = history_size  # Store last 100 timestamp entries

        # Track latest values for each service
        self.latest_ptp4l_offset = None
        self.latest_ptp4l_freq = None
        self.latest_phc2sys_offset = None
        self.latest_phc2sys_freq = None

        # Warning limits
        self.ptp4l_offset_warning_threshold = 5000 # If offsets are larger than this value, a warning will be displayed.
        self.ptp4l_freq_warning_threshold = 100000 # If frequency correction is larger than this value, a warning will be displayed.
        self.phc2sys_offset_warning_threshold = 5000 # If offsets are larger than this value, a warning will be displayed.
        self.phc2sys_freq_warning_threshold = 100000 # If frequency correction is larger than this value, a warning will be displayed.
        self.ptp4l_threshold_flag = False
        self.phc2sys_threshold_flag = False


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
            raise PTPError(f"Missing required packages: {', '.join(missing_packages)}. "
                          f"Please install them using: sudo apt-get install linuxptp")


    def _validate_interface(self):
        """Check if the interface exists and supports PTP."""
        # Check if interface exists
        if not os.path.exists(f'/sys/class/net/{self.interface}'):
            raise PTPError(f"Interface {self.interface} does not exist")
        
        # Check if interface is up
        try:
            with open(f'/sys/class/net/{self.interface}/operstate', 'r') as f:
                if f.read().strip() != 'up':
                    self.logger.warning(f"Interface {self.interface} is not up")
        except IOError:
            self.logger.warning(f"Could not check interface {self.interface} state")
        
        # Check if interface supports PTP
        try:
            result = subprocess.run(['ethtool', '-T', self.interface], 
                                  capture_output=True, text=True)
            if 'PTP Hardware Clock' not in result.stdout:
                self.logger.warning(f"Interface {self.interface} may not support PTP hardware timestamping")
        except subprocess.CalledProcessError:
            self.logger.warning(f"Could not check PTP support for {self.interface}")


    def _check_systemd_services(self):
        """Check if the required systemd services exist."""
        services = [self.ptp4l_service, self.phc2sys_service]
        
        for service in services:
            try:
                result = subprocess.run(['systemctl', 'status', service], 
                                       capture_output=True, text=True)
                if result.returncode == 4:  # Unit not found
                    raise PTPError(f"Systemd service {service} not found. "
                                   f"Please run the setup script to configure PTP services.")
            except subprocess.CalledProcessError as e:
                if e.returncode == 4:  # Unit not found
                    raise PTPError(f"Systemd service {service} not found. "
                                   f"Please run the setup script to configure PTP services.")
                else:
                    self.logger.warning(f"Could not check {service} service status: {e}")


    def _stop_timesyncd(self):
        """
        systemd-timesyncd is a ntp daemon which I have found runs by default on raspberry pi 5.
        It will interfere with phc2sys function if it is running.
        It's also necessary to stop ntp. This is achieved with timedatectl set-ntp false. Make sure to run it as sudo.
        This should be made to happen during setup, but we might as well do it here as well.
        """
        self.logger.info("Attempting to stop systemd-timesyncd")
        try:
            # Stop the service
            result = subprocess.run(["sudo", "systemctl", "stop", "systemd-timesyncd"], 
                                   capture_output=True, text=True, check=True)
            self.logger.info("systemd-timesyncd stopped successfully")
            
            # Disable the service to prevent auto-restart
            result = subprocess.run(["sudo", "systemctl", "disable", "systemd-timesyncd"], 
                                   capture_output=True, text=True, check=True)
            self.logger.info("systemd-timesyncd disabled successfully")
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to stop/disable timesyncd: {e}")
            self.logger.error(f"stdout: {e.stdout}")
            self.logger.error(f"stderr: {e.stderr}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to stop timesyncd: {str(e)}")
            raise
            
        self.logger.info("Attempting to disable NTP via timedatectl")
        try:
            result = subprocess.run(["sudo", "timedatectl", "set-ntp", "false"], 
                                   capture_output=True, text=True, check=True)
            self.logger.info("NTP disabled via timedatectl successfully")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to disable NTP: {e}")
            self.logger.error(f"stdout: {e.stdout}")
            self.logger.error(f"stderr: {e.stderr}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to stop timedatectl ntp: {str(e)}")
            raise
            
        # Verify the service is actually stopped
        try:
            result = subprocess.run(["systemctl", "is-active", "systemd-timesyncd"], 
                                   capture_output=True, text=True)
            status = result.stdout.strip()
            if status == "active":
                self.logger.warning("systemd-timesyncd is still active after stop attempt")
            else:
                self.logger.info(f"systemd-timesyncd status: {status}")
        except Exception as e:
            self.logger.error(f"Could not verify timesyncd status: {str(e)}")
    

    def _start_timesyncd(self):
        """
        Resume timesyncd and ntp on cleanup - clock will drift otherwise.
        """
        try:
            self.logger.info("Attempting to enable systemd-timesyncd")
            # Enable the service first
            result = subprocess.run(["sudo", "systemctl", "enable", "systemd-timesyncd"], 
                                   capture_output=True, text=True, check=True)
            self.logger.info("systemd-timesyncd enabled successfully")
            
            # Start the service
            result = subprocess.run(["sudo", "systemctl", "start", "systemd-timesyncd"], 
                                   capture_output=True, text=True, check=True)
            self.logger.info("systemd-timesyncd started successfully")
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to start systemd-timesyncd: {e}")
            self.logger.error(f"stdout: {e.stdout}")
            self.logger.error(f"stderr: {e.stderr}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to start systemd-timesyncd: {str(e)}")
            raise
            
        try:
            self.logger.info("Attempting to enable NTP via timedatectl")
            result = subprocess.run(["sudo", "timedatectl", "set-ntp", "true"], 
                                   capture_output=True, text=True, check=True)
            self.logger.info("NTP enabled via timedatectl successfully")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to enable NTP: {e}")
            self.logger.error(f"stdout: {e.stdout}")
            self.logger.error(f"stderr: {e.stderr}")
            raise
        except Exception as e:
            self.logger.error(f"Failed to start timedatectl ntp: {str(e)}")
            raise


    def _get_service_status(self, service_name):
        """Get the status of a systemd service."""
        try:
            result = subprocess.run(['systemctl', 'is-active', service_name], 
                                   capture_output=True, text=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return 'unknown'


    def _get_service_logs(self, service_name, lines=10):
        """Get recent logs from a systemd service."""
        try:
            result = subprocess.run(['journalctl', '-u', service_name, '-n', str(lines), '--no-pager'], 
                                   capture_output=True, text=True)
            return result.stdout
        except subprocess.CalledProcessError:
            return ""


    def start(self):
        self.logger.info(f"Starting PTP in {self.role.value} mode on {self.interface}")

        # Ensure timesyncd is disabled, or else phc2sys won't work!
        self._stop_timesyncd()

        try: 
            # Start ptp4l service
            self.logger.info("Starting ptp4l service")
            subprocess.run(["systemctl", "start", self.ptp4l_service], check=True)

            # Check if ptp4l started successfully
            ptp4l_status = self._get_service_status(self.ptp4l_service)
            if ptp4l_status != "active":
                logs = self._get_service_logs(self.ptp4l_service)
                raise PTPError(f"ptp4l service failed to start. Status: {ptp4l_status}\nLogs: {logs}")
            
            self.logger.info("ptp4l started successfully")
            
            # Start phc2sys service
            self.logger.info("Starting phc2sys service")
            subprocess.run(["systemctl", "start", self.phc2sys_service], check=True)

            # Check if phc2sys started successfully
            phc2sys_status = self._get_service_status(self.phc2sys_service)
            if phc2sys_status != 'active':
                logs = self._get_service_logs(self.phc2sys_service)
                raise PTPError(f"phc2sys service failed to start. Status: {phc2sys_status}\nLogs: {logs}")
            
            self.logger.info("phc2sys started successfully")
            
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to start PTP services: {e}")
            self.stop()
            raise
        
        # Start monitoring thread
        self.running = True
        self.status = "starting"
        self.monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self.monitor_thread.start()

    def _monitor(self):
        """Monitor PTP services and parse their output."""
        while self.running:
            try:
                # Check service status
                ptp4l_status = self._get_service_status(self.ptp4l_service)
                phc2sys_status = self._get_service_status(self.phc2sys_service)

                if ptp4l_status != 'active' or phc2sys_status != 'active':
                    self.status = f'ptp4l:{ptp4l_status}, phc2sys:{phc2sys_status}'
                    self.logger.error(f"PTP services not active: ptp4l={ptp4l_status}, phc2sys={phc2sys_status}")
                    self.running = False
                    return

                # Get recent logs and parse them
                ptp4l_logs = self._get_service_logs(self.ptp4l_service, lines=5)
                phc2sys_logs = self._get_service_logs(self.phc2sys_service, lines=5)

                # Parse ptp4l logs
                for line in ptp4l_logs.split('\n'):
                    if line.strip():
                        self._parse_ptp4l_line(line)

                # Parse phc2sys logs
                for line in phc2sys_logs.split('\n'):
                    if line.strip():
                        self._parse_phc2sys_line(line)

            except Exception as e:
                self.logger.error(f"Error in monitor thread: {e}")

            time.sleep(1)  # Check every second

    def _add_buffer_entry(self, timestamp):
        """Add a new entry to the buffer with current values."""
        entry = {
            'timestamp': timestamp,
            'ptp4l_freq': self.latest_ptp4l_freq,
            'ptp4l_offset': self.latest_ptp4l_offset,
            'phc2sys_freq': self.latest_phc2sys_freq,
            'phc2sys_offset': self.latest_phc2sys_offset
        }
        
        self.ptp_buffer.append(entry)
        
        # Keep buffer size manageable
        if len(self.ptp_buffer) > self.max_buffer_size:
            self.ptp_buffer.pop(0)

    def _parse_ptp4l_line(self, line):
        """Parse a line from ptp4l logs."""
        line = line.strip()
        if not line:
            return

        self.logger.debug(f"ptp4l: {line}")

        # Parse offset information - format: "master offset <number> s2 freq <number>"
        if 'master offset' in line:
            try:
                # Extract offset value from line using regex
                import re
                offset_match = re.search(r'master offset\s+(-?\d+)', line)
                if offset_match:
                    current_offset = float(offset_match.group(1))
                    self.latest_ptp4l_offset = current_offset
                    self.last_offset = current_offset
                    self.last_sync_time = time.time()
                    self.status = 'synchronized'
                    
                    # Add entry to buffer
                    self._add_buffer_entry(time.time())
                    
            except (IndexError, ValueError) as e:
                self.logger.warning(f"Could not parse ptp4l offset from line: {line}, error: {e}")

        # Parse freq correction information - format: "s2 freq <number>"
        if 's2 freq' in line:
            try:
                # Extract freq correction from line using regex
                import re
                freq_match = re.search(r's2 freq\s+([+-]?\d+)', line)
                if freq_match:
                    self.latest_ptp4l_freq = int(freq_match.group(1))
                    self.last_freq = self.latest_ptp4l_freq
                    
                    # Add entry to buffer if we don't have a recent one
                    if not self.ptp_buffer or time.time() - self.ptp_buffer[-1]['timestamp'] > 1.0:
                        self._add_buffer_entry(time.time())
                    
            except (IndexError, ValueError) as e:
                self.logger.warning(f"Could not parse ptp4l freq from line: {line}, error: {e}")

        # Check for successful sync
        if 'synchronized' in line.lower():
            self.status = 'synchronized'
            self.logger.info("PTP synchronized successfully")

        # Check for port state changes
        if 'port state' in line.lower():
            self.logger.info(f"PTP port state change: {line}")
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
            self.logger.error(f"PTP error detected: {line}")

    def _parse_phc2sys_line(self, line):
        """Parse a line from phc2sys logs."""
        line = line.strip()
        if not line:
            return

        self.logger.debug(f"phc2sys: {line}")

        # Parse offset information from phc2sys - format: "phc offset <number> s2 freq <number>"
        if 'phc offset' in line:
            try:
                # Extract offset value from line using regex
                import re
                offset_match = re.search(r'phc offset\s+(-?\d+)', line)
                if offset_match:
                    current_offset = float(offset_match.group(1))
                    self.latest_phc2sys_offset = current_offset
                    self.last_offset = current_offset
                    self.last_sync_time = time.time()
                    self.status = 'synchronized'
                    
                    # Add entry to buffer
                    self._add_buffer_entry(time.time())
                    
            except (IndexError, ValueError) as e:
                self.logger.warning(f"Could not parse phc2sys offset from line: {line}, error: {e}")

        # Parse freq correction information from phc2sys - format: "s2 freq <number>"
        if 's2 freq' in line:
            try:
                # Extract freq correction from line using regex
                import re
                freq_match = re.search(r's2 freq\s+([+-]?\d+)', line)
                if freq_match:
                    self.latest_phc2sys_freq = int(freq_match.group(1))
                    
                    # Add entry to buffer if we don't have a recent one
                    if not self.ptp_buffer or time.time() - self.ptp_buffer[-1]['timestamp'] > 1.0:
                        self._add_buffer_entry(time.time())
                    
            except (IndexError, ValueError) as e:
                self.logger.warning(f"Could not parse phc2sys freq from line: {line}, error: {e}")

        # Check for errors
        if 'error' in line.lower():
            self.status = 'error'
            self.logger.error(f"PHC2SYS error detected: {line}")


    def stop(self):
        """Stop PTP services using systemd."""
        self.running = False
        
        try:
            # Stop phc2sys first
            subprocess.run(['systemctl', 'stop', self.phc2sys_service], check=False)
            
            # Stop ptp4l
            subprocess.run(['systemctl', 'stop', self.ptp4l_service], check=False)
            
        except subprocess.CalledProcessError as e:
            self.logger.warning(f"Error stopping services: {e}")

        self.status = 'stopped'
        self.logger.info("Stopped PTP services.")

    def restart(self):
        """Restart PTP services"""
        self.logger.info("Restarting PTP services...")
        try:
            # Stop services
            self.stop()
            
            # Wait a moment
            time.sleep(2)
            
            # Start services
            self.start()
            
            self.logger.info("PTP services restarted successfully")
            return {"status": "success", "message": "PTP services restarted"}
            
        except Exception as e:
            self.logger.error(f"Error restarting PTP services: {e}")
            return {"status": "error", "message": str(e)}

    def get_status(self):
        """Get current PTP status."""
        ptp4l_status = self._get_service_status(self.ptp4l_service)
        phc2sys_status = self._get_service_status(self.phc2sys_service)
        
        return {
            'role': self.role.value,
            'status': self.status,
            'ptp4l_service': ptp4l_status,
            'phc2sys_service': phc2sys_status,
            'last_sync': self.last_sync_time,
            'last_offset': self.last_offset,
            'last_freq': self.last_freq,
            'interface': self.interface,
            'ptp_buffer_size': len(self.ptp_buffer),
            # Add individual service values for health manager
            'ptp4l_offset': self.latest_ptp4l_offset,
            'ptp4l_freq': self.latest_ptp4l_freq,
            'phc2sys_offset': self.latest_phc2sys_offset,
            'phc2sys_freq': self.latest_phc2sys_freq,
        }

    def get_ptp_buffer(self, max_entries=None):
        """Get the ptp_buffer data.
        
        Args:
            max_entries: Maximum number of entries to return (None for all)
            
        Returns:
            List of ptp_buffer dictionaries with timestamp, offset, and freq values
        """
        if max_entries is None:
            return self.ptp_buffer.copy()
        else:
            return self.ptp_buffer[-max_entries:].copy()

    def get_offset_statistics(self):
        """Get statistics from the ptp buffer.
        
        Returns:
            Dictionary with offset and freq statistics for both ptp4l and phc2sys
        """
        def calculate_stats(values):
            if not values:
                return {
                    'count': 0,
                    'mean': None,
                    'std_dev': None,
                    'min': None,
                    'max': None
                }
            
            valid_values = [v for v in values if v is not None]
            if not valid_values:
                return {
                    'count': 0,
                    'mean': None,
                    'std_dev': None,
                    'min': None,
                    'max': None
                }
            
            mean = sum(valid_values) / len(valid_values)
            return {
                'count': len(valid_values),
                'mean': mean,
                'std_dev': (sum((x - mean) ** 2 for x in valid_values) / len(valid_values)) ** 0.5,
                'min': min(valid_values),
                'max': max(valid_values)
            }
        
        # Extract values for each field
        ptp4l_offsets = [entry['ptp4l_offset'] for entry in self.ptp_buffer]
        ptp4l_freqs = [entry['ptp4l_freq'] for entry in self.ptp_buffer]
        phc2sys_offsets = [entry['phc2sys_offset'] for entry in self.ptp_buffer]
        phc2sys_freqs = [entry['phc2sys_freq'] for entry in self.ptp_buffer]
        
        return {
            'ptp4l_offset': calculate_stats(ptp4l_offsets),
            'ptp4l_freq': calculate_stats(ptp4l_freqs),
            'phc2sys_offset': calculate_stats(phc2sys_offsets),
            'phc2sys_freq': calculate_stats(phc2sys_freqs)
        }

    def is_synchronized(self, timeout=5):
        if self.last_sync_time and (time.time() - self.last_sync_time) < timeout:
            return True
        return False

    def get_ptp_time(self):
        # If PTP is synced to system clock, just use time.time()
        return time.time()

    def get_service_logs(self, service_name=None, lines=20):
        """Get logs from PTP services."""
        if service_name is None:
            # Get logs from both services
            ptp4l_logs = self._get_service_logs(self.ptp4l_service, lines)
            phc2sys_logs = self._get_service_logs(self.phc2sys_service, lines)
            return {
                'ptp4l': ptp4l_logs,
                'phc2sys': phc2sys_logs
            }
        else:
            return self._get_service_logs(service_name, lines)