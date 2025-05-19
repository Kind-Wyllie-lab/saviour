import subprocess
import threading
import time
import logging
import os
import sys
from enum import Enum

class PTPRole(Enum):
    MASTER = "master"
    SLAVE = "slave"

class PTPError(Exception):
    pass

class PTPManager:
    def __init__(self, role=PTPRole.MASTER, interface='eth0', master_address=None, log_path=None):
        # Check for root privileges first
        if os.geteuid() != 0:
            raise PTPError("This program must be run as root (use sudo). PTP requires root privileges to adjust system time.")
            
        self.role = role
        self.interface = interface
        self.master_address = master_address # Is this actually needed?
        self.log_path = log_path or '/tmp/ptp_manager.log'
        
        # Configure logging first
        self.logger = logging.getLogger('PTPManager')
        self.logger.setLevel(logging.DEBUG)
        
        # Add console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        self.logger.addHandler(console_handler)
        
        # Add file handler if path provided
        if log_path:
            file_handler = logging.FileHandler(log_path)
            file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(file_handler)
        
        # Check for required packages
        self._check_required_packages()
        
        # Validate interface
        self._validate_interface()
        
        # Configure ptp4l arguments based on role
        if role == PTPRole.MASTER:
            self.ptp4l_args = ['ptp4l', '-i', interface, '-m', '-l', '6']
            # Updated phc2sys args to use autoconfiguration
            self.phc2sys_args = ['phc2sys', '-a'] #, '-r', '-w', '-l', '6'
        else:
            if not master_address:
                raise ValueError("master_address is required for slave mode")
            self.ptp4l_args = ['ptp4l', '-i', interface, '-s', master_address, '-l', '6']
            self.phc2sys_args = ['phc2sys', '-s', interface, '-w', '-l', '6']
        
        self.ptp4l_proc = None
        self.phc2sys_proc = None
        self.monitor_thread = None
        self.running = False
        self.status = 'not running'
        self.last_sync_time = None
        self.last_offset = None

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

    def start(self):
        self.logger.info(f"Starting PTP in {self.role.value} mode on {self.interface}")
        
        # Start ptp4l with error capture
        try:
            self.ptp4l_proc = subprocess.Popen(
                self.ptp4l_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Check if process started successfully
            time.sleep(0.5)  # Give it a moment to start
            if self.ptp4l_proc.poll() is not None:
                error = self.ptp4l_proc.stderr.read()
                raise PTPError(f"ptp4l failed to start: {error}")
            
            self.logger.info("ptp4l started successfully")
            
            # Start phc2sys
            self.phc2sys_proc = subprocess.Popen(
                self.phc2sys_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Check if process started successfully
            time.sleep(0.5)
            if self.phc2sys_proc.poll() is not None:
                error = self.phc2sys_proc.stderr.read()
                self.ptp4l_proc.terminate()
                raise PTPError(f"phc2sys failed to start: {error}")
            
            self.logger.info("phc2sys started successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to start PTP processes: {str(e)}")
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
                    self.logger.error(f"{name} process stopped unexpectedly! Error: {error}")
                    self.running = False
                    return
                
                line = proc.stdout.readline() if proc and proc.stdout else ''
                if line:
                    self.logger.debug(f"{name}: {line.strip()}")
                    
                    # Parse offset information
                    if 'master offset' in line or 'offset' in line:
                        try:
                            # Extract offset value from line
                            offset_str = line.split('offset')[1].split()[0]
                            self.last_offset = float(offset_str)
                            self.last_sync_time = time.time()
                            self.status = 'synchronized'
                        except (IndexError, ValueError):
                            self.logger.warning(f"Could not parse offset from line: {line.strip()}")
                    
                    # Check for errors
                    if 'FAULT' in line or 'error' in line.lower():
                        self.status = 'error'
                        self.logger.error(f"PTP error detected: {line.strip()}")
            
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
        
        self.status = 'stopped'
        self.logger.info("Stopped PTP processes.")

    def get_status(self):
        return {
            'role': self.role.value,
            'status': self.status,
            'last_sync': self.last_sync_time,
            'last_offset': self.last_offset,
            'interface': self.interface
        }

    def is_synchronized(self, timeout=5):
        if self.last_sync_time and (time.time() - self.last_sync_time) < timeout:
            return True
        return False

    def get_ptp_time(self):
        # If PTP is synced to system clock, just use time.time()
        return time.time()

# Example usage:
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    
    try:
        # Example as master
        master = PTPManager(role=PTPRole.MASTER, interface='eth0')
        master.start()
        
        while True:
            status = master.get_status()
            print(f"PTP status: {status}")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nShutting down...")
        master.stop()
    except PTPError as e:
        print(f"Error: {e}")
        sys.exit(1)