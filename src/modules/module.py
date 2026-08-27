#!/usr/bin/env python3
"""
SAVIOUR System - Base Module Class

This is the base class for all peripheral modules in the Habitat system.

Author: Andrew SG
Created: 17/03/2025
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
import datetime
import logging
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

INSTALL_DIR = "/usr/local/src/saviour"

# mend.sh exit code meaning "mend ran fine, but a bootloader/kernel setting
# (NVMe APST / PSU_MAX_CURRENT) needs a reboot to take effect".
MEND_REBOOT_REQUIRED_EXIT = 10

# Check if running under systemd
is_systemd = os.environ.get('INVOCATION_ID') is not None

# Setup logging ONCE for all modules
if is_systemd:
    # Under systemd, let it handle timestamps
    format_string = '%(levelname)s - %(name)s - %(message)s'
else:
    # When running directly, include timestamps
    format_string = '%(asctime)s - %(levelname)s - %(name)s - %(message)s'

# Setup logging ONCE for all additional classes that log
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(name)s - %(message)s'
)

# Import managers
from src.modules.command import Command
from src.modules.communication import Communication
from src.modules.config import Config
from src.modules.export import Export
from src.modules.facade import ModuleFacade
from src.modules.health import Health
from src.modules.network import Network
from src.modules.ptp import PTP, PTPRole
from src.modules.recording import Recording
from src.shared.zip_extract import extract_preserving_permissions

_SENSITIVE_KEY_FRAGMENTS = {"password", "credential", "secret", "token"}

def _sanitise_config(cfg: dict) -> dict:
    """Recursively redact values whose key contains a sensitive word."""
    out = {}
    for k, v in cfg.items():
        if any(s in k.lower() for s in _SENSITIVE_KEY_FRAGMENTS):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _sanitise_config(v)
        else:
            out[k] = v
    return out


def command(name=None):
    """
    Decorator to mark a method as a command.
    Can be used as @command() or @command(name="foo")
    Commands should return a dict response.

    """
    def decorator(func):
        func._is_command = True
        func._cmd_name = name or func.__name__
        return func
    return decorator


def check(name=None):
    """
    Decorator to mark a method as a ready check.
    Can be used as @command() or @command(name="foo")
    Commands should return a tuple response: bool for whether succeeded, str message describing the result.
    """
    def decorator(func):
        func._is_check = True
        func._cmd_name = name or func.__name__
        return func
    return decorator


class Module(ABC):
    """
    Base class for all modules in the Habitat Controller.

    This class provides common functionality that all hardware modules (camera, microphone, TTL IO, RFID) share.
    It handles network communication with the main controller, PTP synchronization, power management, health monitoring, and basic lifecycle operations.

    Attributes:
        module_id (str): Unique identifier for the module
        module_type (str): Type of module (camera, microphone, ttl_io, rfid)
        config (dict): Configuration parameters for the module

    """
    # Names of the built-in checks in `self.checks` — excluded from the
    # auto-discovered `module_checks` list since they're already run unconditionally.
    _BASE_CHECK_NAMES = {
        "_check_running", "_check_readwrite", "_check_diskspace",
        "_check_ptp", "_check_recording", "_check_export",
    }

    def __init__(self, module_type: str):
        # Setup logging first
        self.logger = logging.getLogger(__name__)

        # Module type
        self.module_type = module_type
        self.module_id = self.generate_module_id(self.module_type)
        self.description = "No description" # A human readable description to be overridden by child classes
        self.version = self._get_version()


        self.logger.info(f"Initializing {self.module_type} module {self.module_id}, SAVIOUR {self.version}")

        # Manager objects
        self.config = Config()

        # Ensure group is set — active_config.json created before this field
        # existed (or saved with an empty value) would otherwise override the
        # base-config default and leave the module unsubscribed from its group topic.
        if not self.config.get("module.group"):
            self.logger.info(
                f"module.group not set, defaulting to module type '{self.module_type}'"
            )
            self.config.set("module.group", self.module_type)

        self.export = Export(module_id=self.module_id, config=self.config) # Export object - exports to samba share
        self.communication = Communication(config=self.config) # Communication object - handles ZMQ messaging
        self.health = Health(config=self.config) # Health object - monitors system health e.g. temperature, resource utilisation, ptp sync
        self.ptp = PTP(role=PTPRole.SLAVE) # PTP object - initialises ptp sync
        self.recording = Recording(config=self.config) # Recording object - sets up, maintains and manages recording sessions
        self.command = Command(config=self.config) # Command object - routes incoming commands
        self.network = Network(self.config, module_id=self.module_id, module_type=self.module_type) # Network object - registers zeroconf service, discovers controller
        self.facade = ModuleFacade(module=self) # API object - provides internal routing between objects e.g. network and recording

        # A registry of commands that the module can respond to
        self.command_callbacks = {
            'restart_ptp': self.ptp.restart, # Restart PTP services
            'get_health': self.health.get_health,
            'start_recording': self.recording.start_recording,
            'stop_recording': self.recording.stop_recording,
            'list_recordings': self.list_recordings,
            'list_commands': self.list_commands,
            'get_config': self.get_config, # Gets the complete config from
            'set_config': self._handle_set_config,
            # 'set_config': lambda config: self.set_config(config, persist=True), # Uses a dict to update the config manager
            'validate_readiness': self.validate_readiness, # Validate module readiness for recording
            'shutdown': self._shutdown,
            "update_saviour": self.update_saviour,
            "reboot": self.reboot,
            "restart_service": self.restart_service,
            "run_mend": self.run_mend,
            "reset_config": self.reset_config,
            "start_export": self.start_export,
            "set_export_config": self.set_export_config,
            "get_diagnostics": self.get_diagnostics,
        }

        # Register callbacks and facade
        self.config.configure_module = self.configure_module
        self.network.facade = self.facade
        self.health.facade = self.facade
        self.communication.facade = self.facade
        self.command.facade = self.facade
        self.export.facade = self.facade
        self.recording.facade = self.facade

        # Register commands with command router
        self.command.set_commands(self.command_callbacks)

        # Recording management
        self.recording_session_id = None
        self.current_filename_prefix = None
        self.session_files = []

        # Control State flags
        self.is_running = False  # Start as False
        self.is_recording = False # Flag to indicate if the module is recording e.g. video, TTL, audio, etc.
        self.is_streaming = False # Flag to indicate if the module is streaming on a network port e.g. video, TTL, audio, etc.
        self.is_connected_to_controller = False
        self.is_ready = False  # Flag to indicate if module is ready for recording
        self.last_readiness_check = None  # Timestamp of last readiness check

        # Grace-period timer: when the controller disconnects we don't stop
        # recording immediately.  If the controller reconnects within
        # DISCONNECT_RECORDING_GRACE_SECS, the timer is cancelled and recording
        # continues uninterrupted.  This protects against brief network blips
        # and controller restarts that would otherwise cut a segment short.
        self._disconnect_recording_timer = None  # type: Optional[threading.Timer]
        self._disconnect_recording_timer_lock = threading.Lock()
        # Prevents concurrent mDNS events from running when_controller_discovered
        # simultaneously, which would interleave ZMQ cleanup and reconnect.
        self._discovery_lock = threading.Lock()

        # Ready checks
        self.checks = [
            self._check_running,
            self._check_readwrite,
            self._check_diskspace,
            self._check_ptp,
            self._check_recording,
            self._check_export,
        ]

        # Auto-discover any @command()/@check()-decorated methods anywhere in this
        # class's MRO — a module author only needs to decorate a method, no manual
        # dict/list registration required. module_checks may still be overridden by
        # a subclass assigning `self.module_checks = [...]` after `super().__init__()`.
        self._finalize_command_registration()

        self.logger.info(f"Registered these readiness checks: {self.checks}")

        # Track when module started for uptime calculation
        self.start_time = None

        # Log to file if enabled
        if self.config.get("logging.to_file", True):
            self.setup_logger_file_handling()


        # self.check_interrupted_recordings()


    def _auto_register_decorated_methods(self) -> tuple[dict[str, Any], list]:
        """Scan this instance's full class hierarchy for @command()/@check()
        methods and return (commands, module_checks) ready to register.

        `dir(type(self))` walks the MRO de-duplicated, with the most-derived
        override winning, so this picks up decorated methods defined anywhere
        from `Module` down through the concrete module class — even though this
        runs during `Module.__init__`, before the subclass's own `__init__` body
        has executed (methods are class attributes, so they already exist).
        """
        commands: dict[str, Any] = {}
        module_checks = []
        for name in dir(type(self)):
            member = getattr(type(self), name, None)
            if not callable(member):
                continue
            if getattr(member, "_is_command", False):
                cmd_name = getattr(member, "_cmd_name", name)
                commands[cmd_name] = getattr(self, name)
            elif getattr(member, "_is_check", False) and name not in self._BASE_CHECK_NAMES:
                module_checks.append(getattr(self, name))
        return commands, module_checks


    def _finalize_command_registration(self) -> None:
        """Merge auto-discovered @command() methods into the command router.

        A manually-registered name always wins on collision — e.g. 'set_config'
        is deliberately wired to the kwargs-wrapping _handle_set_config, not the
        same-named @command()-decorated set_config() it wraps internally.
        Auto-discovery must not clobber that, so already-registered names are
        skipped rather than overwritten.
        """
        auto_commands, self.module_checks = self._auto_register_decorated_methods()
        auto_commands = {
            name: fn for name, fn in auto_commands.items()
            if name not in self.command.commands
        }
        self.command.set_commands(auto_commands)


    def get_module_name(self) -> str:
        name = self.config.get("module.name")
        if name == "":
            name = self.module_id
        return name


    def get_module_group(self) -> str:
        return self.config.get("module.group")


    def reboot(self) -> None:
        os.system("sudo reboot now")

    def restart_service(self) -> dict:
        """Restart the saviour service. Sends ack before restarting so the controller hears it."""
        import threading
        import time as _time
        def _do_restart():
            self.communication.send_status({
                "type": "cmd_ack",
                "command": "restart_service",
                "result": "success",
                "output": "Service restarting…",
            })
            _time.sleep(1)
            subprocess.run(["sudo", "systemctl", "restart", "saviour.service"])
        threading.Thread(target=_do_restart, daemon=True, name="saviour-restart").start()
        return {"result": "started"}


    def update_saviour(self, controller_url: str = "") -> dict:
        """Download and apply a staged update from the controller's HTTP endpoint.

        The controller serves the package at GET /update/package.  After rsync
        the service restarts itself so the ZMQ command thread returns first.
        """
        import shutil
        import threading
        import urllib.request
        import zipfile

        def _do_update():
            try:
                if not controller_url:
                    raise ValueError("controller_url parameter is required")

                pkg_url = f"{controller_url.rstrip('/')}/update/package"
                self.logger.info(f"Downloading update from {pkg_url}")

                tmp_zip = "/tmp/saviour_update.zip"
                # Retry download — the controller may be mid-restart when we
                # first try, so back off and retry before giving up.
                for _attempt in range(1, 6):
                    try:
                        urllib.request.urlretrieve(pkg_url, tmp_zip)
                        break
                    except Exception as _dl_err:
                        if _attempt == 5:
                            raise
                        import time as _t
                        self.logger.warning(
                            f"Download attempt {_attempt} failed ({_dl_err}); "
                            f"retrying in 15s"
                        )
                        _t.sleep(15)

                if not zipfile.is_zipfile(tmp_zip):
                    raise ValueError("Downloaded file is not a valid ZIP archive")

                extract_dir = "/tmp/saviour_update_extract"
                shutil.rmtree(extract_dir, ignore_errors=True)
                os.makedirs(extract_dir)
                extract_preserving_permissions(tmp_zip, extract_dir)

                contents = os.listdir(extract_dir)
                source = extract_dir
                if (len(contents) == 1
                        and os.path.isdir(os.path.join(extract_dir, contents[0]))):
                    source = os.path.join(extract_dir, contents[0])

                subprocess.run([
                    "rsync", "-a",
                    "--chown=pi:pi",
                    "--exclude=env/",
                    "--exclude=.git/",
                    f"{source}/",
                    f"{INSTALL_DIR}/",
                ], check=True)

                # pip install is best-effort — modules are offline.
                pip_result = subprocess.run([
                    f"{INSTALL_DIR}/env/bin/pip", "install", "-q",
                    "--no-index",
                    f"{INSTALL_DIR}/",
                ])
                if pip_result.returncode != 0:
                    self.logger.warning(
                        "pip install --no-index failed; new dependencies (if any) "
                        "will need a manual install with internet access"
                    )

                self.logger.info("Update applied — restarting module service")
                self.communication.send_status({
                    "type": "cmd_ack",
                    "command": "update_saviour",
                    "result": "success",
                    "output": "Update applied — module is restarting...",
                })
                import time as _time
                _time.sleep(2)
                subprocess.Popen(["sudo", "systemctl", "restart", "saviour.service"])

            except Exception as e:
                self.logger.error(f"SAVIOUR update failed: {e}")
                self.communication.send_status({
                    "type": "cmd_ack",
                    "command": "update_saviour",
                    "result": "error",
                    "output": str(e),
                })

        threading.Thread(target=_do_update, daemon=True, name="saviour-update").start()
        return {"result": "started", "output": "Update download started."}


    def run_mend(self, reboot: bool = False) -> dict:
        """Run mend.sh to repair/refresh this device's dependencies, config,
        and systemd unit file, then restart the service.

        Typically sent by the controller as a deliberate follow-up after
        `update_saviour` (once the module has reconnected post-update) --
        unlike update_saviour, which only rsyncs new code, mend.sh also
        regenerates saviour.service via `saviour-config --regenerate-service`
        to match whatever variant/path layout the new code landed at (see
        the "Blocking prerequisite" note in CLAUDE.md's Architectural
        concerns section), so a code change that renames/moves a variant
        folder needs both commands, not just update_saviour alone.

        Args:
            reboot: pass --reboot to mend.sh so it reboots the device itself
                iff a reboot-dependent step (NVMe APST, PSU_MAX_CURRENT)
                actually changed something this run. Without it, mend.sh
                exits 10 when a reboot is pending and this method reports
                result "reboot_required" (still a success -- the mend ran).
        """
        import threading

        def _do_mend():
            try:
                mend_script = os.path.join(INSTALL_DIR, "mend.sh")
                if not os.path.isfile(mend_script):
                    raise FileNotFoundError(f"mend.sh not found at {mend_script}")
                argv = ["sudo", "bash", mend_script]
                if reboot:
                    argv.append("--reboot")
                self.logger.info(f"Running {' '.join(argv[2:])}")
                # mend.sh's own last step restarts saviour.service, which
                # tears down this very process -- same race as
                # update_saviour's restart above, accepted there for the
                # same reason: the "started" ack already returned at
                # dispatch time is the reliable signal, this one is best-effort.
                result = subprocess.run(
                    argv,
                    capture_output=True, text=True, timeout=1200, check=False,
                )
                tail = ((result.stdout or "") + (result.stderr or ""))[-2000:]
                # 10 = mend ran fine but a bootloader/kernel setting needs a
                # reboot to take effect (and --reboot was not given).
                if result.returncode == MEND_REBOOT_REQUIRED_EXIT:
                    self.logger.warning("mend.sh completed — reboot required")
                    self.communication.send_status({
                        "type": "cmd_ack",
                        "command": "run_mend",
                        "result": "reboot_required",
                        "output": "mend.sh completed; reboot required to activate "
                                  "NVMe APST / PSU_MAX_CURRENT",
                    })
                    return
                if result.returncode != 0:
                    raise RuntimeError(f"mend.sh exited {result.returncode}: {tail}")
                self.logger.info("mend.sh completed successfully")
                self.communication.send_status({
                    "type": "cmd_ack",
                    "command": "run_mend",
                    "result": "success",
                    "output": "mend.sh completed",
                })
            except Exception as e:
                self.logger.error(f"run_mend failed: {e}")
                self.communication.send_status({
                    "type": "cmd_ack",
                    "command": "run_mend",
                    "result": "error",
                    "output": str(e),
                })

        threading.Thread(target=_do_mend, daemon=True, name="saviour-mend").start()
        return {"result": "started", "output": "mend.sh started"}


    def _handle_set_config(self, **kwargs) -> dict:
        return self.set_config(kwargs, persist=True)


    def setup_logger_file_handling(self) -> None:
        # Add file handler if none exists
        if not self.logger.handlers:
            # Add file handler for persistent logging (useful when running as systemd service)
            try:
                # Create logs directory if it doesn't exist
                log_dir = self.config.get("logging.directory", "/var/log/saviour")
                os.makedirs(log_dir, exist_ok=True)

                # Generate log filename with module info
                log_filename = f"{self.module_type}_{self.module_id}.log"
                log_filepath = os.path.join(log_dir, log_filename)

                # Get config values for rotation
                max_bytes = self.config.get("logging.max_file_size_mb", 10) * 1024 * 1024
                backup_count = self.config.get("logging.backup_count", 5)

                # Add file handler with rotation
                from logging.handlers import RotatingFileHandler
                file_handler = RotatingFileHandler(
                    log_filepath,
                    maxBytes=max_bytes,
                    backupCount=backup_count
                )
                file_handler.setLevel(logging.INFO)
                self.logger.addHandler(file_handler)

                self.logger.info(f"File logging enabled: {log_filepath}")
                self.logger.info(f"Log rotation: max {max_bytes//(1024*1024)}MB, keep {backup_count} backups")

            except Exception as e:
                # If file logging fails, log the error but don't crash
                self.logger.warning(f"Failed to setup file logging: {e}")
                self.logger.info("Continuing with console logging only")
        else:
            self.logger.info("Logger file handler was not set up as handlers already exist")


    def configure_module(self, updated_keys: list[str] | None) -> None:
        self.logger.info(f"Received notification that module config changed, calling configure_module() with keys {updated_keys}")

        # Check for special keys
        export_keys = ["export.max_bitrate_mb", "export.max_burst_kb", "export.share_ip", "export.share_path", "export.share_username", "export.share_password"]
        should_reconfigure_samba = False
        for key in export_keys:
            if key in updated_keys:
                should_reconfigure_samba = True
        if should_reconfigure_samba:
            self.logger.info("Reconfiguring samba")
            self.export._samba_settings_changed()

        if "module.group" in updated_keys:
            self.communication.group_changed()

        self.configure_module_special(updated_keys)


    @abstractmethod
    def configure_module_special(self, updated_keys: list[str] | None):
        """Gets called when module specific configuration changes e.g. framerate for a camera - allows modules to update their settings when they change"""
        self.logger.warning("No implementation provided for abstract method configure_module")


    def when_controller_discovered(self, controller_ip: str, controller_port: int):
        """Callback when controller is discovered via zeroconf"""
        self.logger.info(f"Network manager informs that controller was discovered at {controller_ip}:{controller_port}")
        if not self._discovery_lock.acquire(blocking=False):
            self.logger.info("Controller discovery already in progress — skipping duplicate event")
            return
        try:
            self._when_controller_discovered_inner(controller_ip, controller_port)
        finally:
            self._discovery_lock.release()

    def _when_controller_discovered_inner(self, controller_ip: str, controller_port: int):
        self.logger.info("Module will now initialize the necessary managers")

        # Cancel any pending stop-recording grace timer.  Must happen both
        # before and after controller_disconnected() because that call may start
        # a fresh timer if the module is still recording.
        def _cancel_grace_timer():
            with self._disconnect_recording_timer_lock:
                if self._disconnect_recording_timer is not None:
                    self._disconnect_recording_timer.cancel()
                    self._disconnect_recording_timer = None

        _cancel_grace_timer()

        # If already connected to any controller (same or different), tear down
        # cleanly before re-initialising.  A same-IP update means the controller
        # restarted; its ZMQ sockets are new and we must reconnect from scratch.
        if self.communication.controller_ip:
            self.logger.info("Existing controller connection found, disconnecting before reconnecting")
            self.controller_disconnected()
            # controller_disconnected() starts a grace timer if recording — cancel it
            # immediately since we are about to reconnect.
            _cancel_grace_timer()
            self.logger.info("Controller rediscovered — cancelled disconnect recording timer")

        try:

            # 2. Connect communication manager
            self.logger.info("Connecting communication manager to controller")
            if not self.communication.connect(controller_ip, controller_port):
                raise Exception("Failed to connect communication manager")
            self.logger.info("Communication manager connected to controller")

            # 3. Start command listener
            self.logger.info("Requesting communication manager to start command listener")
            if not self.communication.start_command_listener():
                raise Exception("Failed to start command listener")
            self.logger.info("Command listener started")

            # 4. Start heartbeats if module is running
            if self.is_running:
                self.logger.info("Requesting health manager to start heartbeats")
                self.health.start_heartbeats()
                self.logger.info("Heartbeats started")

            # 5. Start
            self.logger.info("Starting PTP manager")
            self.ptp.start()

            self.logger.info("Controller connection and initialization complete")

            self.is_connected_to_controller = True

            self.check_interrupted_recordings()

        except Exception as e:
            self.logger.error(f"Error during controller initialization: {e}")
            # Clean up any partial initialization
            self.communication.cleanup()
            self.file_transfer = None
            raise


    def controller_disconnected(self):
        """Callback when controller is disconnected"""
        # What should happen here - we don't want to stop the module altogether, we want to stop recording, deregister controller, and wait for new controller connection.
        self.logger.info("Controller disconnected")

        self.is_connected_to_controller = False

        # Stop recording after a grace period rather than immediately, so a
        # brief controller restart or network blip doesn't cut the segment.
        if self.is_recording:
            grace = self.config.get("module.disconnect_recording_grace_secs", 120)
            self.logger.info(
                f"Controller disconnected while recording — will stop in {grace}s "
                f"unless controller reconnects first"
            )
            with self._disconnect_recording_timer_lock:
                if self._disconnect_recording_timer is not None:
                    self._disconnect_recording_timer.cancel()
                self._disconnect_recording_timer = threading.Timer(
                    grace, self._on_disconnect_grace_expired
                )
                self._disconnect_recording_timer.daemon = True
                self._disconnect_recording_timer.start()

        # Stop heartbeats before cleaning up communication
        self.health.stop_heartbeats()

        # Clean up communication manager (this will recreate ZMQ context and sockets)
        self.communication.cleanup()

        # Clean up file transfer
        self.file_transfer = None
        self.is_streaming = False

        self.logger.info("Controller disconnection cleanup complete, ready for reconnection")

    def _on_disconnect_grace_expired(self):
        """Called when the disconnect grace period elapses with no reconnection."""
        with self._disconnect_recording_timer_lock:
            self._disconnect_recording_timer = None
        if self.is_recording:
            self.logger.info("Disconnect grace period expired — stopping recording")
            self._stop_recording()

    """Recording methods"""
    @abstractmethod
    def _start_new_recording(self) -> bool:
        """To be implemented by subclasses"""
        pass


    @abstractmethod
    def _start_next_recording_segment(self) -> bool:
        """To be implemented by subclasses"""
        pass


    @abstractmethod
    def _stop_recording(self) -> bool:
        """To be implemented by subclasses"""
        pass


    def _check_recording_alive(self) -> tuple[bool, str | None]:
        """Module-specific liveness check for an in-progress recording --
        e.g. a capture thread that died unexpectedly (microphone) or the
        capture pipeline having gone silent (camera). Distinct from the
        readiness checks in self.checks, which only run pre-flight and
        actively fail while recording (see _check_recording).

        Default: nothing to check. Override in a subclass that owns
        something worth watching; not abstract because most module types
        (TTL, RFID) have no equivalent signal.

        Returns (True, None) if healthy, or (False, detail) if not.
        """
        return True, None


    """File IO"""
    def _check_file_exists(self, filename: str) -> bool:
        """Check if a file exists in the recording folder."""
        if filename in os.listdir(self.facade.get_recording_folder()):
            return True
        else:
            return False


    @command()
    def list_recordings(self):
        """List all recorded files with metadata and send to controller"""
        try:
            recordings = [] # TODO: Get recordings here

            # Send status response
            self.communication.send_status({
                "type": "recordings_list",
                "recordings": recordings
            })

            return recordings

        except Exception as e:
            self.logger.error(f"Error listing recordings: {e}")
            # Send error status but don't re-raise the exception
            try:
                self.communication.send_status({
                    "type": "recordings_list_failed",
                    "error": str(e)
                })
            except Exception as send_error:
                self.logger.error(f"Error sending failure status: {send_error}")
            # Return empty list instead of re-raising
            return []


    def list_commands(self):
        """
        Return the names of zmq commands that the module understands.
        """
        return list(self.command.commands.keys())


    # Start and stop functions
    def start(self) -> bool:
        """
        Start the module.

        This method should be overridden by the subclass to implement specific module initialization logic.
        
        Returns:
            bool: True if the module started successfully, False otherwise.
        """
        self.logger.info(f"Starting {self.module_type} module {self.module_id}")
        if self.is_running:
            self.logger.info("Module already running")
            return False

        # Wait for proper network connectivity (DHCP-assigned IP)
        if not self._wait_for_network_ready():
            self.logger.error("Failed to get proper network connectivity")
            return False

        # Register service with proper IP address
        if not self.network.register_service():
            self.logger.error("Failed to register service")
            return False

        self.is_running = True
        self.start_time = time.strftime("%Y-%m-%d %H:%M:%S")
        self.logger.info(f"Module started at {self.start_time}")

        # Update start time in command handler
        self.command.start_time = self.start_time

        # Start command listener thread if controller is discovered
        if self.network.controller_ip:
            self.logger.info(f"Attempting to connect to controller at {self.network.controller_ip}")
            # Connect to the controller
            self.communication.connect(
                self.network.controller_ip,
                self.network.controller_port
            )
            self.communication.start_command_listener()

            # Start PTP only if it's not already running
            if not self.ptp.running:
                time.sleep(0.1)
                self.ptp.start()

            # Start sending heartbeats
            time.sleep(1)
            self.health.start_heartbeats()

        return True


    def _wait_for_network_ready(self, check_interval: float = 2.0) -> bool:
        """
        Wait for proper network connectivity with DHCP-assigned IP address.
        Will keep trying indefinitely until a proper IP is obtained.
        
        Args:
            check_interval: Time between checks in seconds (default: 2.0)
            
        Returns:
            bool: True if proper IP is obtained (will always return True eventually)
        """
        self.logger.info("Waiting for proper network connectivity (will keep trying until IP is obtained)")

        attempts = 0

        while True:
            attempts += 1

            try:
                # Get all IP addresses
                result = subprocess.run(['hostname', '-I'],
                                      capture_output=True, text=True, timeout=5)

                if result.returncode == 0:
                    ip_addresses = result.stdout.strip().split()

                    # Check for proper DHCP-assigned IP (192.168.x.x)
                    for ip in ip_addresses:
                        if ip.startswith('192.168.') or ip.startswith('10.0.'):
                            self.logger.info(f"Network ready! Got IP: {ip} (attempt {attempts})")
                            return True

                    # Log current IPs for debugging
                    if ip_addresses:
                        self.logger.info(f"Attempt {attempts}: Current IPs: {ip_addresses}")
                    else:
                        self.logger.info(f"Attempt {attempts}: No IP addresses found")

                else:
                    self.logger.warning(f"Attempt {attempts}: hostname -I failed: {result.stderr}")

            except subprocess.TimeoutExpired:
                self.logger.warning(f"Attempt {attempts}: hostname -I timed out")
            except Exception as e:
                self.logger.warning(f"Attempt {attempts}: Error checking network: {e}")

            # Wait before next check
            time.sleep(check_interval)


    def stop(self) -> bool:
        """
        Stop the module.

        This method should be overridden by the subclass to implement specific module shutdown logic.

        Returns:
            bool: True if the module stopped successfully, False otherwise.
        """
        self.logger.info(f"Stopping {self.module_type} module {self.module_id} at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        if not self.is_running:
            self.logger.info("Module already stopped")
            return False

        try:
            # First: Clean up command handler (stops streaming and thread)
            self.logger.info("Cleaning up command handler...")
            self.command.cleanup()

            # Second: Stop the health manager (and its heartbeat thread)
            self.logger.info("Stopping health manager...")
            self.health.stop_heartbeats()

            # Fourth: Stop the service manager (doesn't use ZMQ directly)
            self.logger.info("Cleaning up network manager...")
            self.network.cleanup()

            # Fifth: Stop the communication manager (ZMQ cleanup)
            self.logger.info("Cleaning up communication manager...")
            self.communication.cleanup()

            # Unmount any mounted destination
            if hasattr(self, 'export'):
                self.export.unmount()

        except Exception as e:
            self.logger.error(f"Error stopping module: {e}")
            return False

        # Confirm the module is stopped
        self.is_running = False
        self.logger.info(f"Module stopped at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        return True


    def generate_session_id(self, module_id="unknown"):
        """Start a new session for a module"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"REC_{timestamp}_{module_id}" # Generate a new session ID


    def _shutdown(self):
        """Shut down the module"""
        try:
            # Ack before shutdown — the command dispatcher sends its ack after
            # the handler returns, but the process is killed by systemd before
            # that happens, so we must send it here while the socket is alive.
            self.communication.send_status({
                "type": "cmd_ack",
                "command": "shutdown",
                "result": "success",
            })
            if self.stop():
                subprocess.run(["sudo", "shutdown", "now"])
            else:
                self.logger.error("Failed to stop module, not shutting down system")
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")


    """Config Methods"""
    @command()
    def get_config(self):
        response = {
            "config": self.config.get_all()
        }
        self.logger.info(f"Get config called, returning config with {len(response['config'])} keys")
        return response

    def get_diagnostics(self) -> dict:
        """Collect logs and sanitised config for a controller-initiated bug report."""
        try:
            result = subprocess.run(
                ["journalctl", "-u", "saviour.service", "-n", "500",
                 "--no-pager", "--output=short-precise"],
                capture_output=True, text=True, timeout=10,
            )
            logs = result.stdout if result.returncode == 0 else f"journalctl error: {result.stderr}"
        except Exception as e:
            logs = f"Could not collect logs: {e}"

        return {
            "result": "success",
            "logs": logs,
            "config": _sanitise_config(self.config.get_all()),
            "module_type": self.module_type,
            "version": self.version,
        }


    @command()
    def set_config(self, config: dict, persist: bool = True) -> bool:
        """
        Set the entire configuration from a dictionary
        
        Args:
            config: Dictionary containing the new configuration
            persist: Whether to persist the changes to the config file
            
        Returns:
            True if successful, False otherwise
        """
        self.logger.info(f"Set config called with config {config} and persist {persist}")
        try:
            # Validate that config is a dictionary
            if not isinstance(config, dict):
                self.logger.error(f"set_config called with non-dict argument: {type(config)}")
                return False

            # Use the config manager's merge method to update the config
            self.config.set_all(config, persist=persist)
            return {"result": "success", "config": self.config.get_all()}
        except Exception as e:
            self.logger.error(f"Error setting all config: {e}")
            return {"result": f"Error setting all config: {e}"}


    @command()
    def reset_config(self) -> dict:
        """
        Reset config to defaults (base + module defaults) and reconfigure the module.
        Returns the new config so the controller can confirm the reset.
        """
        self.logger.info("reset_config called — restoring defaults")
        self.config.reset_to_defaults()
        # Re-register the configure_module callback (reset rebuilds the dict in-place
        # but the callback reference on the Config object is preserved).
        module_keys = list(getattr(self.config, 'module_config_keys', []))
        self.configure_module(module_keys)
        return {"config": self.config.get_all()}


    @command()
    def set_export_config(self, share_ip: str, share_username: str, share_password: str, share_path: str = "") -> dict:
        """
        Update the export (Samba) credentials sent by the controller on discovery.
        Persists to active_config.json via config.set(). Credentials are always
        re-sent by the controller on reconnect so they do not need to survive a
        config reset.
        Skipped if export.export_target is not 'controller'.
        """
        if self.config.get("export.export_target", "controller") != "controller":
            self.logger.info("set_export_config skipped — export_target is not controller")
            return {"result": "skipped"}
        self.logger.info(f"set_export_config called — updating share_ip to {share_ip}, share_path to {share_path!r}")

        self.config.set("export.share_ip", share_ip)
        self.config.set("export.share_username", share_username)
        self.config.set("export.share_password", share_password)
        if share_path:
            self.config.set("export.share_path", share_path)

        return {"result": "success"}


    def _get_required_disk_space_mb(self) -> float:
        """
        Get the required disk space in MB for this module.
        Reads from config with fallback to default.
        
        Returns:
            float: Required disk space in MB (default: 100MB)
        """
        return self.config.get("module.required_disk_space_mb", 100.0)


    def _get_ptp_offset_threshold_us(self) -> float:
        """
        Get the maximum acceptable PTP offset in microseconds.
        Reads from config with fallback to default.
        
        Returns:
            float: Maximum acceptable PTP offset in microseconds (default: 1000μs = 1ms)
        """
        return self.config.get("module.ptp_offset_threshold_us", 1000000.0)


    """Ready to record checks"""
    def _perform_module_specific_checks(self) -> tuple[bool, str]:
        """
        Perform module-specific readiness checks.
        Subclasses should override this method to add their own validation.
        
        Args:
            checks: Dictionary to store check results
            
        Returns:
            tuple: (ready: bool, error_msg: str or None)
        """
        self.logger.info(f"Performing {self.module_type} specific checks")
        for check in self.module_checks:
            self.logger.info(f"Running {check.__name__}")
            result, message = check()
            if result == False:
                self.logger.info(f"A check failed: {check.__name__}, {message}")
                return False, message
                break # Exit loop on first failed check
        return True, f"{self.module_type} checks passed"


    @check()
    def _check_running(self) -> tuple[bool, str]:
        if not self.is_running:
            return False, "Module is not running"
        else:
            return True, "Module is running"


    @check()
    def _check_readwrite(self) -> tuple[bool, str]:
        try:
            self.logger.debug(f"Checking can write to {self.facade.get_recording_folder()}")
            if not os.path.exists(self.facade.get_recording_folder()):
                os.makedirs(self.facade.get_recording_folder(), exist_ok=True)
            self.logger.debug("Created folder OK")
            # Test write access
            test_file = os.path.join(self.facade.get_recording_folder(), '.test_write')
            self.logger.debug(f"Going to write to test file {test_file}")
            with open(test_file, 'w') as f:
                self.logger.debug(f"Opened test file {f}")
                f.write('test')
            self.logger.debug("Removing test file")
            os.remove(test_file)
            return True, "Recording folder writable"
        except PermissionError as e:
            return False, f"Permission error: {e}"
        except OSError as e:
            return False, f"OSError during write/delete test: {e}"
        except Exception as e:
            return False, f"Recording folder not writable: {e}"


    @check()
    def _check_diskspace(self) -> tuple[bool, str]:
        try:
            statvfs = os.statvfs(self.facade.get_recording_folder())
            free_bytes = statvfs.f_frsize * statvfs.f_bavail
            free_mb = free_bytes / (1024 * 1024)
            required_mb = self._get_required_disk_space_mb()
            if free_mb >= required_mb:
                return True, f"Sufficient disk space: {free_mb:.1f}MB free (need at least {required_mb:.1f}MB)"
            return False, f"Insufficient disk space: {free_mb:.1f}MB free (need at least {required_mb:.1f}MB)"
        except Exception as e:
            return False, f"Cannot check disk space: {e!s}"


    @check()
    def _check_ptp(self) -> tuple[bool, str]:
        # TODO: Add check for frequency offset as well as time offset
        try:
            ptp_status = self.ptp.get_status()
            # Check if PTP offset is reasonable (configurable threshold)
            max_offset_us = self._get_ptp_offset_threshold_us()
            last_offset = ptp_status["last_offset"]
            if last_offset is None:
                return False, "PTP reporting Nonetype offsets - may need time to settle"
            if abs(last_offset) > max_offset_us:
                return False, f"PTP not synchronized: offset {last_offset}μs (max: {max_offset_us}μs)"
            else:
                return True, f"PTP synchronised to {ptp_status['last_offset']}μs"
        except Exception as e:
            return False, f"PTP check failed: {e}"


    @check()
    def _check_recording(self) -> tuple[bool, str]:
        if self.is_recording:
            return False, "Module is currently recording"
        else:
            return True, "Module not currently recording"


    @check()
    def _check_export(self) -> tuple[bool, str]:
        if self.config.get("export.export_target", "controller") != "controller":
            return True, "Export target is not controller — skipping share check"

        # share_ip, not password, is the real "has anything been configured
        # at all" signal -- a blank password is a legitimate configuration
        # for a guest-access share (see export.py's _mount_share() and
        # web.py's ensure_export_share_mounted(), both of which already fall
        # back to "guest" auth when username is empty). Gating on password
        # here instead used to hard-fail readiness for a genuinely working
        # guest share (confirmed live 2026-08-24: mount succeeded fine with
        # `-o guest` against a real NAS with `guest ok = yes`), before ever
        # attempting the mount below that would have shown it working.
        share_ip = self.config.get("export.share_ip", "")
        if not share_ip:
            return False, (
                "Export credentials not set — use 'Sync Export' in Controller Settings "
                "(Settings page → Controller)"
            )

        password   = self.config.get("export.share_password", "")
        share_path = self.config.get("export.share_path", "controller_share")
        username   = self.config.get("export.share_username", "saviour_module")
        mount_point = self.export.mount_point
        _CHECK_TIMEOUT_S = 8
        # Several modules pressing "Check Ready" at once all hit the controller's
        # Samba server within the same instant; smbd can transiently refuse or
        # stall a connection under that simultaneous-connect burst even though
        # the share is otherwise healthy. A single attempt made a random one or
        # two modules fail every time; retry like export.py's _mount_share does.
        _CHECK_MOUNT_MAX_ATTEMPTS = 3
        _CHECK_MOUNT_RETRY_DELAY_S = 1.5

        os.makedirs(mount_point, exist_ok=True)

        already_mounted = os.path.ismount(mount_point)
        mounted_for_check = False

        def _mount() -> tuple[bool, str]:
            """Mount the share, retrying transient failures; return (success, err)."""
            # Same guest-auth fallback as export.py's _mount_share() and
            # web.py's ensure_export_share_mounted() -- a blank username
            # means a guest-access share, not "no credentials configured"
            # (that case already returned above via the share_ip check).
            auth = f"username={username},password={password}" if username else "guest"
            last_err = "unknown error"
            for attempt in range(1, _CHECK_MOUNT_MAX_ATTEMPTS + 1):
                try:
                    r = subprocess.run(
                        [
                            "sudo", "mount", "-t", "cifs",
                            f"//{share_ip}/{share_path}", mount_point,
                            "-o", f"{auth},uid=pi,gid=pi,file_mode=0664,dir_mode=0775,cache=none",
                        ],
                        capture_output=True, text=True, timeout=_CHECK_TIMEOUT_S,
                    )
                    if r.returncode == 0:
                        return True, ""
                    last_err = r.stderr.strip() or "unknown error"
                except subprocess.TimeoutExpired:
                    last_err = f"mount timed out after {_CHECK_TIMEOUT_S}s"
                if attempt < _CHECK_MOUNT_MAX_ATTEMPTS:
                    time.sleep(_CHECK_MOUNT_RETRY_DELAY_S)
            return False, f"Cannot mount //{share_ip}/{share_path}: {last_err}"

        def _test_write() -> tuple[bool, str]:
            test_path = os.path.join(mount_point, ".saviour_check")
            try:
                with open(test_path, "w") as f:
                    f.write("check")
                os.remove(test_path)
                return True, ""
            except OSError as e:
                return False, str(e)

        # Surfaced whenever a failure here could plausibly be fleet-wide NAS/SMB
        # contention rather than a genuinely broken share -- confirmed live
        # 2026-08-24: an unstaggered fleet-wide "Check Ready" against a
        # 20-module habitat deployment produced a mix of I/O error / device
        # busy / no-such-file failures across most of the fleet on this exact
        # write/delete step, even though the share itself was healthy
        # throughout (fixed at the dispatch side too, see web.py's
        # check_ready handler -- this message is a backstop for whatever
        # contention the staggering doesn't fully absorb, e.g. a very large
        # fleet or a slow NAS).
        _CONTENTION_HINT = (
            "If several modules checked readiness at the same moment, this "
            "can be transient NAS/share contention rather than a real fault "
            "— try Check Ready again."
        )

        try:
            if not already_mounted:
                ok, err = _mount()
                if not ok:
                    return False, err
                mounted_for_check = True

            ok, write_err = _test_write()
            if ok:
                return True, f"Controller share //{share_ip}/{share_path} reachable and writable"

            # Write failed — either a stale CIFS connection left over from a
            # previous export session, or a mount that reported success but
            # isn't actually serving a working share yet (e.g. controller's
            # Samba was mid-restart). Attempt a lazy unmount and fresh remount
            # either way, but only if no export is currently active (we must
            # not pull the rug from live I/O).
            if not self.export.exporting:
                subprocess.run(
                    ["sudo", "umount", "-l", mount_point],
                    capture_output=True, timeout=5,
                )
                ok, remount_err = _mount()
                if not ok:
                    return False, (
                        f"Stale mount, remount failed: {remount_err} "
                        f"(original write error: {write_err}). {_CONTENTION_HINT}"
                    )
                mounted_for_check = True
                ok, write_err2 = _test_write()
                if ok:
                    return True, (
                        f"Controller share //{share_ip}/{share_path} reachable "
                        f"and writable (remounted stale connection)"
                    )
                return False, (
                    f"Write still failing after remount: {write_err2}. {_CONTENTION_HINT}"
                )

            return False, (
                f"Export check failed: write test failed ({write_err}) while an "
                f"export is currently active, so the mount was left untouched "
                f"rather than remounted."
            )

        except subprocess.TimeoutExpired:
            return False, f"Mount timed out after {_CHECK_TIMEOUT_S}s — controller unreachable?"
        except Exception as e:
            return False, f"Export check failed: {e}"
        finally:
            if mounted_for_check and os.path.ismount(mount_point):
                subprocess.run(["sudo", "umount", mount_point], capture_output=True, timeout=10)


    def _run_checks(self):
        self.logger.info("Running checks...")
        checks = {}
        for check in self.checks:
            self.logger.info(f"Running {check.__name__}")
            result, message = check()
            checks[check.__name__] = result, message
            if result == False:
                self.logger.info(f"A check failed: {check.__name__}, {message}")
                return False, message
                break # Exit loop on first failed check

        result, message = self._perform_module_specific_checks()
        if result == False:
            self.logger.info(f"Check failed {result} {message}")
            return result, message

        self.logger.info("ALL CHECKS PASSED")
        return True, "All tests passed" # Everything passed


    def validate_readiness(self) -> dict:
        try:
            result, message = self._run_checks()
        except Exception as e:
            result, message = False, f"Error running readiness checks: {e}"
        return {
            'ready': result,
            'timestamp': time.time(),
            'message': message
        }


    def check_interrupted_recordings(self):
        files = self.check_recordings()
        if len(files["pending"]) == 0:
            return

        self.logger.warning(f"Module has only just booted but incomplete recordings are present. Will attempt to export {len(files['pending'])} files.")

        incomplete_files = files["pending"]
        for file in incomplete_files:
            self.logger.info(f"Backing up {file}")

            # Rename file to indicate that it is a partial recording
            filename, filetype = file.split(".")
            current_filepath = f"{self.facade.get_recording_folder()}/{file}"
            new_filepath = f"{self.facade.get_recording_folder()}/{filename}_PARTIAL.{filetype}"
            os.rename(current_filepath, new_filepath)

            # Get session name from filename
            session_name = self.facade.get_session_from_filename(filename)
            file_start_date = self.facade.get_start_time_from_filename(filename)[0:8]

            # Create export path
            export_path = f"{session_name}/{file_start_date}/{self.facade.get_module_name()}"

            self.logger.info(f"Staging file for export: {new_filepath} from session {session_name}")
            self.facade.stage_file_for_export(new_filepath)
            self.facade.export_staged(export_path)


    def start_export(self, export_path: str = None) -> dict:
        """Handle a start_export command from the controller.

        Spawns a background thread to perform the actual export so the command
        listener is not blocked.  The thread sends export_complete or
        export_failed when done so the controller can dispatch the next module.
        """
        if self.export.exporting:
            self.logger.warning("start_export received but already exporting")
            return {"result": "error", "message": "Already exporting"}
        threading.Thread(
            target=self._run_export,
            args=(export_path,),
            daemon=True
        ).start()
        return {"result": "accepted"}

    def _run_export(self, export_path: str) -> None:
        """Background thread: run export and report outcome to controller."""
        try:
            session_results = self.facade.export_staged(export_path)
            # Determine success based on the triggered session only —
            # stale files from other sessions failing should not taint this result.
            triggered_session = export_path.split('/', maxsplit=1)[0] if export_path and '/' in export_path else export_path
            success = session_results.get(triggered_session, False)
            if success:
                self.facade.send_status({
                    "type": "export_complete",
                    "export_path": export_path
                })
            else:
                self.facade.send_status({
                    "type": "export_failed",
                    "export_path": export_path
                })
        except Exception as e:
            self.logger.error(f"Error in export thread: {e}")
            self.facade.send_status({
                "type": "export_failed",
                "export_path": export_path,
                "error": str(e)
            })

    def check_recordings(self) -> dict:
        recordings_folder = self.facade.get_recording_folder()
        to_export_folder = self.facade.get_to_export_folder()
        exported_folder = self.facade.get_exported_folder()

        pending_files = os.listdir(recordings_folder)
        to_export_files = os.listdir(to_export_folder)
        exported_files = os.listdir(exported_folder)

        files = {
            "pending": pending_files,
            "to_export": to_export_files,
            "exported": exported_files
        }
        return files


    @command()
    def report_recording_state(self, session_name: str = None) -> dict:
        """Remotely-callable summary of this module's local recording
        pipeline state (pending/to_export/exported), grouped by session and
        reduced to counts/sizes/timestamps -- never raw filenames. Polled by
        the controller so the Recordings page can show something meaningful
        for a session that's still running, including habitat's 16-module,
        weeks-long deployments where "wait until stopped" isn't workable.
        """
        return self.facade.summarize_recording_state(session_name)



    """Helper functions"""
    def generate_module_id(self, module_type: str) -> str:
        """Generate a module ID based on the module type and the MAC address"""
        # mac = hex(uuid.getnode())[2:]  # Gets MAC address as hex, removes '0x' prefix (old method, led to MAC changing)
        mac = self.get_mac_address("eth0")
        short_id = mac[-4:]  # Takes last 4 characters
        return f"{module_type}_{short_id}"  # e.g., "camera_5e4f"


    def get_mac_address(self, interface="eth0"):
        """Retreive mac address on specified interface, default eth0."""
        try:
            with open(f"/sys/class/net/{interface}/address") as f:
                return f.read().strip().replace(":", "")
        except FileNotFoundError:
            return None


    def get_utc_time(self, timestamp: int):
        if not timestamp:
            timestamp = time.time()
        strtime = datetime.datetime.utcfromtimestamp(timestamp).strftime("%Y%m%d-%H%M%S")
        return strtime


    def get_utc_date(self, timestamp: int):
        strdate = datetime.datetime.utcfromtimestamp(timestamp).strftime("%Y%m%d")
        return strdate


    def _get_version(self) -> str:
        """Get the current saviour version.

        Prefers src/__version__.py (updated by ZIP deploys) over git describe
        (which is stale after a ZIP deploy because .git is excluded from rsync).
        """
        version_file = "/usr/local/src/saviour/src/__version__.py"
        try:
            import re as _re
            with open(version_file) as _f:
                _m = _re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', _f.read())
                if _m:
                    vers = _m.group(1)
                    self.logger.info(f"Retrieved version: {vers}")
                    return vers
        except Exception:
            pass
        try:
            s = subprocess.run(
                ["git", "-c", "safe.directory=/usr/local/src/saviour", "describe", "--tags"],
                cwd="/usr/local/src/saviour",
                capture_output=True,
                text=True,
                check=True
            )
            vers = s.stdout.strip()
            self.logger.info(f"Retrieved version: {vers}")
        except subprocess.CalledProcessError as e:
            vers = "UNKNOWN_VERSION"
            self.logger.error(f"Error getting saviour version: {e.stderr.strip()}")
        return vers


    def _parse_version(self, long_version: str) -> str:
        # Take a long version e.g. v0.1.6-6-g15d6e731 and convert to 0.1.6
        parts = long_version.split(".")
        if parts[0].startswith("v"):
            v1 = parts[0][1]
        else:
            v1 = parts[0]

        v2 = parts[1]

        v3 = parts[2].split("-")[0]

        vers = f"{v1}.{v2}.{v3}"

        return vers
