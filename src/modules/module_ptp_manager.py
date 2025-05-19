import subprocess
import threading
import time
import logging
from enum import Enum

class PTPRole(Enum):
    MASTER = "master"
    SLAVE = "slave"

class PTPManager:
    def __init__(self, role=PTPRole.MASTER, interface='eth0', master_address=None, log_path=None):
        self.role = role
        self.interface = interface
        self.master_address = master_address
        self.log_path = log_path or '/tmp/ptp_manager.log'
        
        # Configure ptp4l arguments based on role
        if role == PTPRole.MASTER:
            self.ptp4l_args = ['ptp4l', '-i', interface, '-m']
            self.phc2sys_args = ['phc2sys', '-c', interface, '-w']
        else:
            if not master_address:
                raise ValueError("master_address is required for slave mode")
            self.ptp4l_args = ['ptp4l', '-i', interface, '-s', master_address]
            self.phc2sys_args = ['phc2sys', '-s', interface, '-w']
        
        self.ptp4l_proc = None
        self.phc2sys_proc = None
        self.monitor_thread = None
        self.running = False
        self.status = 'not running'
        self.last_sync_time = None
        self.last_offset = None
        self.logger = logging.getLogger('PTPManager')
        
        # Configure logging
        if log_path:
            handler = logging.FileHandler(log_path)
            handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
            self.logger.addHandler(handler)

    def start(self):
        self.logger.info(f"Starting PTP in {self.role.value} mode on {self.interface}")
        self.ptp4l_proc = subprocess.Popen(
            self.ptp4l_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        self.phc2sys_proc = subprocess.Popen(
            self.phc2sys_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        self.running = True
        self.status = 'starting'
        self.monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self.monitor_thread.start()

    def _monitor(self):
        while self.running:
            for proc, name in [(self.ptp4l_proc, 'ptp4l'), (self.phc2sys_proc, 'phc2sys')]:
                if proc and proc.poll() is not None:
                    self.status = f'{name} stopped'
                    self.logger.error(f"{name} process stopped unexpectedly!")
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
        if self.phc2sys_proc:
            self.phc2sys_proc.terminate()
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
    
    # Example as master
    master = PTPManager(role=PTPRole.MASTER, interface='eth0')
    master.start()
    
    # Example as slave
    # slave = PTPManager(role=PTPRole.SLAVE, interface='eth0', master_address='192.168.1.100')
    # slave.start()
    
    try:
        while True:
            status = master.get_status()
            print(f"PTP status: {status}")
            time.sleep(2)
    except KeyboardInterrupt:
        master.stop()