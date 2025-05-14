import subprocess
import threading
import logging
import time
import enum
from typing import Optional

class DeviceType(enum.Enum):
    MASTER = "master"
    SLAVE = "slave"

class PTPManager:
    def __init__(self, device_type: DeviceType, interface: str = "eth0", logger: Optional[logging.Logger] = None):
        self.device_type = device_type
        self.interface = interface
        self.logger = logger or logging.getLogger("PTPManager")
        
        self.ptp4l_process: Optional[subprocess.Popen] = None
        self.phc2sys_process: Optional[subprocess.Popen] = None
        self._is_running = False
        self._monitor_thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        """Start PTP synchronization"""
        try:
            # Start ptp4l with appropriate flags based on node type
            ptp4l_cmd = ["sudo", "ptp4l", "-i", self.interface, "-H"]
            if self.device_type == DeviceType.MASTER:
                ptp4l_cmd.extend(["-m"])  # Master specific flags
            else:
                ptp4l_cmd.extend(["-s"])  # Slave specific flags
            
            self.ptp4l_process = subprocess.Popen(
                ptp4l_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Give ptp4l time to initialize
            time.sleep(2)
            
            # Start phc2sys with appropriate flags
            phc2sys_cmd = ["sudo", "phc2sys", "-s", "CLOCK_REALTIME", "-c", self.interface, "-w"]
            if self.device_type == DeviceType.MASTER:
                phc2sys_cmd.extend(["-m"])
            
            self.phc2sys_process = subprocess.Popen(
                phc2sys_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            self._is_running = True
            self._start_monitoring()
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start PTP: {e}")
            self.stop()
            return False

    def stop(self) -> None:
        """Stop PTP synchronization"""
        self._is_running = False
        
        for process in [self.ptp4l_process, self.phc2sys_process]:
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                except Exception as e:
                    self.logger.error(f"Error stopping process: {e}")

        self.ptp4l_process = None
        self.phc2sys_process = None

    def _start_monitoring(self) -> None:
        """Start monitoring thread to log PTP status"""
        def monitor():
            while self._is_running:
                if self.ptp4l_process and self.phc2sys_process:
                    # Read and log ptp4l output
                    ptp4l_output = self.ptp4l_process.stdout.readline()
                    if ptp4l_output:
                        self.logger.debug(f"ptp4l: {ptp4l_output.strip()}")
                    
                    # Read and log phc2sys output
                    phc2sys_output = self.phc2sys_process.stdout.readline()
                    if phc2sys_output:
                        self.logger.debug(f"phc2sys: {phc2sys_output.strip()}")
                time.sleep(0.1)

        self._monitor_thread = threading.Thread(target=monitor, daemon=True)
        self._monitor_thread.start()

    @property
    def is_running(self) -> bool:
        """Check if PTP is running"""
        return self._is_running and \
               self.ptp4l_process and \
               self.phc2sys_process and \
               self.ptp4l_process.poll() is None and \
               self.phc2sys_process.poll() is None