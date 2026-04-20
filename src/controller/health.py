#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Health Monitor

Handles health monitoring for all modules in the habitat system, including:
- Module health status tracking
- Heartbeat monitoring
- Online/offline/suspected status detection
- Health data processing
- Historical health data tracking

Author: Andrew SG
Created: ?
"""

import time
import threading
import logging
import subprocess
import socket as _socket
from collections import deque
from typing import Dict, Any, Optional, List

class Health:
    def __init__(self, config):
        """Initialize the health monitor

        Args:
            heartbeat_interval: Interval between health checks in seconds
            heartbeat_timeout: Time in seconds before marking a module as offline
        """

        self.logger = logging.getLogger(__name__)
        self.config = config
        self.heartbeat_interval = self.config.get("health.heartbeat_interval", 30)
        self.heartbeat_timeout = self.config.get("health.heartbeat_timeout", 90)
        self.suspicion_timeout = self.config.get("health.suspicion_timeout", 60)
        self.probe_interval = self.config.get("health.probe_interval", 15)
        self.max_probe_attempts = self.config.get("health.max_probe_attempts", 3)
        self.monitor_interval = 30

        # Ensure the suspicion window is meaningful — if suspicion_timeout >= heartbeat_timeout
        # (e.g. the active config has heartbeat_timeout=60 from before this feature existed)
        # the two-phase logic collapses: every module skips probing and goes straight to
        # confirmed-offline. Auto-adjust to 2/3 of heartbeat_timeout in that case.
        if self.suspicion_timeout >= self.heartbeat_timeout:
            adjusted = max(1, int(self.heartbeat_timeout * 2 / 3))
            self.logger.warning(
                f"health.suspicion_timeout ({self.suspicion_timeout}s) >= "
                f"health.heartbeat_timeout ({self.heartbeat_timeout}s) — suspicion window "
                f"would be zero. Auto-adjusting suspicion_timeout to {adjusted}s."
            )
            self.suspicion_timeout = adjusted

        # Health data storage
        self.module_health = {}  # Current health data. module_id as primary key.
        self.module_health_history = {}  # Historical health data
        self.controller_health = {} # Historical controller health data.

        # Module online/offline states
        self.module_states = {}

        # Control flags
        self.is_monitoring = False
        self.monitor_thread = None

        self.logger.info(
            f"Initialised health monitor with heartbeat interval {self.heartbeat_interval}s, "
            f"timeout {self.heartbeat_timeout}s, suspicion threshold {self.suspicion_timeout}s."
        )


    """Modify module health records"""
    def touch_heartbeat(self, module_id: str) -> None:
        """Record that a message was received from module_id without updating metrics.

        Any ZMQ message (cmd_ack, recording_started, etc.) proves the module is
        reachable.  Updating last_heartbeat here prevents the suspicion/offline
        timer firing on a module that is busy recording and missed a periodic
        heartbeat send.

        If the module is currently offline or suspected (e.g. because the controller
        just restarted and hasn't received a full heartbeat payload yet), any ZMQ
        proof-of-life is enough to bring it back online immediately rather than
        waiting up to heartbeat_interval seconds for the next scheduled heartbeat.
        """
        if module_id in self.module_health:
            self.module_health[module_id]['last_heartbeat'] = time.time()
            if self.module_health[module_id].get('status') in ('offline', 'suspected'):
                self._mark_module_online(module_id, trigger="ZMQ message received (proof of life)")

    def remove_module(self, module_id: str):
        if module_id in self.module_health.keys():
            self.module_health.pop(module_id)


    def update_module_health(self, module_id: str, status_data: Dict[str, Any]) -> bool:
        """
        Update health data for a specific module

        Args:
            module_id: ID of the module
            status_data: Dictionary containing health metrics

        Returns:
            bool: True if update was successful
        """
        try:
            was_new_module = module_id not in self.module_health
            if was_new_module:
                # New module - create full health record
                now = time.time()
                self.module_health[module_id] = {
                    'timestamp': now,
                    'last_heartbeat': now,
                    'status': 'online',
                    'cpu_temp': status_data.get('cpu_temp', 0),
                    'cpu_usage': status_data.get('cpu_usage', 0),
                    'memory_usage': status_data.get('memory_usage', 0),
                    'uptime': status_data.get('uptime', 0),
                    'disk_space': status_data.get('disk_space', 0),
                    'ptp4l_offset': status_data.get('ptp4l_offset'),
                    'ptp4l_freq': status_data.get('ptp4l_freq'),
                    'phc2sys_offset': status_data.get('phc2sys_offset'),
                    'phc2sys_freq': status_data.get('phc2sys_freq'),
                    'last_ptp_restart': now,
                    'ptp_restarts': 1,
                    'offline_since': None,
                    'suspected_since': None,
                    'probe_count': 0,
                    'last_probe_time': None,
                    'last_confirmed_online': now,
                }
            else:
                # Existing module - heartbeat received
                now = time.time()
                self.module_health[module_id]['last_heartbeat'] = now
                prev_status = self.module_health[module_id]['status']
                if prev_status in ('offline', 'suspected'):
                    self.module_health[module_id]['status'] = 'online'
                    self.module_health[module_id]['offline_since'] = None
                    self.module_health[module_id]['suspected_since'] = None
                    self.module_health[module_id]['probe_count'] = 0
                    self.module_health[module_id]['last_probe_time'] = None
                    self.module_health[module_id]['last_confirmed_online'] = now
                    self.facade.on_status_change(module_id, "online")

                # Update other metrics if provided
                if 'cpu_temp' in status_data:
                    self.module_health[module_id]['cpu_temp'] = status_data['cpu_temp']
                if 'cpu_usage' in status_data:
                    self.module_health[module_id]['cpu_usage'] = status_data['cpu_usage']
                if 'memory_usage' in status_data:
                    self.module_health[module_id]['memory_usage'] = status_data['memory_usage']
                if 'uptime' in status_data:
                    self.module_health[module_id]['uptime'] = status_data['uptime']
                if 'disk_space' in status_data:
                    self.module_health[module_id]['disk_space'] = status_data['disk_space']
                if 'ptp4l_offset' in status_data:
                    self.module_health[module_id]['ptp4l_offset'] = status_data['ptp4l_offset']
                if 'ptp4l_freq' in status_data:
                    self.module_health[module_id]['ptp4l_freq'] = status_data['ptp4l_freq']
                if 'phc2sys_offset' in status_data:
                    self.module_health[module_id]['phc2sys_offset'] = status_data['phc2sys_offset']
                if 'phc2sys_freq' in status_data:
                    self.module_health[module_id]['phc2sys_freq'] = status_data['phc2sys_freq']
                if "last_ptp_restart" not in self.module_health[module_id]:
                    self.module_health[module_id]["last_ptp_restart"] = now
                if "ptp_restarts" not in self.module_health[module_id]:
                    self.module_health[module_id]["ptp_restarts"] = 1

            if was_new_module:
                self.logger.info(f"New module {module_id} added to health tracking")

            return True

        except Exception as e:
            self.logger.error(f"Error updating health for module {module_id}: {e}")
            return False


    def module_discovery(self, module):
        """Receive a discovered module from the network manager.
        Ensures health tracking is aware of the module.
        """
        self.logger.info(f"Received discovered module from Network: {module}")
        if module.id not in self.module_health:
            self.logger.info(f"Discovered new module {module.id}, adding to health tracking")
            now = time.time()
            self.module_health[module.id] = {
                'timestamp': now,
                'last_heartbeat': 0,  # No heartbeat yet
                'status': 'offline',  # Start as offline until first heartbeat
                'cpu_temp': None,
                'cpu_usage': None,
                'memory_usage': None,
                'uptime': None,
                'disk_space': None,
                'ptp4l_offset': None,
                'ptp4l_freq': None,
                'phc2sys_offset': None,
                'phc2sys_freq': None,
                'offline_since': now,
                'suspected_since': None,
                'probe_count': 0,
                'last_probe_time': None,
                'last_confirmed_online': None,
            }


    def module_id_changed(self, old_module_id, new_module_id):
        # Move the module data to the new key
        self.module_health[new_module_id] = self.module_health.pop(old_module_id)
        if old_module_id in self.module_health_history:
            self.module_health_history[new_module_id] = self.module_health_history.pop(old_module_id)


    """Get methods"""
    def get_module_health_history(self, module_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get historical health data for a specific module

        Args:
            module_id: ID of the module
            limit: Optional limit on number of historical records to return

        Returns:
            List of historical health records, most recent first
        """
        if module_id not in self.module_health_history:
            return []

        history = list(self.module_health_history[module_id])
        if limit:
            history = history[-limit:]
        return history


    def get_module_health(self, module_id: Optional[str] = None) -> Dict:
        """
        Get health data for a specific module or all modules

        Args:
            module_id: Specific module ID, or None for all modules

        Returns:
            Dictionary containing health data
        """
        if module_id:
            return self.module_health.get(module_id, {})
        return self.module_health.copy()


    def get_offline_modules(self) -> list:
        """
        Get list of modules that are currently offline

        Returns:
            List of module IDs that are offline
        """
        return [
            module_id for module_id, health in self.module_health.items()
            if health['status'] == 'offline'
        ]

    def get_online_modules(self) -> list:
        """
        Get list of modules that are currently online

        Returns:
            List of module IDs that are online
        """
        return [
            module_id for module_id, health in self.module_health.items()
            if health['status'] == 'online'
        ]


    def get_health_summary(self) -> Dict[str, Any]:
        """
        Get a summary of overall system health

        Returns:
            Dictionary with health statistics
        """
        online_modules = self.get_online_modules()
        offline_modules = self.get_offline_modules()

        # Calculate average health metrics across all online modules
        avg_metrics = {}
        if online_modules:
            metrics = ['cpu_usage', 'memory_usage', 'cpu_temp', 'ptp4l_offset', 'ptp4l_freq']
            for metric in metrics:
                values = []
                for module_id in online_modules:
                    if module_id in self.module_health and metric in self.module_health[module_id]:
                        values.append(self.module_health[module_id][metric])
                if values:
                    avg_metrics[f'avg_{metric}'] = sum(values) / len(values)

        return {
            'total_modules': len(self.module_health),
            'online_modules': len(online_modules),
            'offline_modules': len(offline_modules),
            'online_module_ids': online_modules,
            'offline_module_ids': offline_modules,
            'average_metrics': avg_metrics
        }


    def get_ptp_sync(self) -> int:
        max_ptp_sync = 0
        for module_id in self.module_health:
            ptp_sync = self.module_health[module_id]["ptp4l_offset"]
            if not ptp_sync:
                return None
            if abs(ptp_sync) > max_ptp_sync:
                max_ptp_sync = abs(ptp_sync)
        return int(max_ptp_sync)


    """Health Methods"""
    def monitor_health(self):
        """Monitor the health of all modules (runs in separate thread)"""
        self.logger.info("Starting health monitor thread")
        cycle_count = 0
        while self.is_monitoring:
            current_time = time.time()
            cycle_count += 1

            # Log every 5 cycles (~2.5 min with 30s interval) to confirm thread is alive
            if cycle_count % 5 == 0:
                # self.logger.info(f"Monitor cycle {cycle_count}: monitoring {len(self.module_health)} modules")
                pass

            for module_id in list(self.module_health.keys()):
                last_heartbeat = self.module_health[module_id]['last_heartbeat']
                time_diff = current_time - last_heartbeat
                status = self.module_health[module_id]['status']

                if time_diff <= self.suspicion_timeout:
                    # Recent heartbeat — module is healthy
                    if status in ('offline', 'suspected'):
                        self._mark_module_online(module_id, trigger="heartbeat received")

                elif time_diff <= self.heartbeat_timeout:
                    # In the suspicion window
                    if status == 'online':
                        self._enter_suspicion(module_id, time_diff)
                    elif status == 'suspected':
                        last_probe = self.module_health[module_id].get('last_probe_time') or 0
                        if current_time - last_probe >= self.probe_interval:
                            self._probe_module(module_id)

                else:
                    # Hard timeout exceeded — confirm offline
                    if status in ('online', 'suspected'):
                        self._confirm_module_offline(module_id, time_diff)

            # Check PTP health periodically
            if cycle_count % 2 == 0:
                self._check_ptp_health()

            time.sleep(self.monitor_interval)


    def _enter_suspicion(self, module_id: str, time_diff: float):
        """Transition a module to suspected-offline state and kick off first probe."""
        now = time.time()
        health = self.module_health[module_id]
        health['status'] = 'suspected'
        health['suspected_since'] = now
        health['probe_count'] = 0
        health['last_probe_time'] = None

        last_hb = health.get('last_heartbeat', 0)
        last_hb_str = time.strftime('%H:%M:%S', time.localtime(last_hb)) if last_hb else 'never'

        cpu  = health.get('cpu_usage')
        temp = health.get('cpu_temp')
        mem  = health.get('memory_usage')
        disk = health.get('disk_space')
        ptp  = health.get('ptp4l_offset')

        cpu_str  = f"{cpu}%"   if cpu  is not None else "N/A"
        temp_str = f"{temp}°C" if temp is not None else "N/A"
        mem_str  = f"{mem}%"   if mem  is not None else "N/A"
        disk_str = f"{disk}%"  if disk is not None else "N/A"
        ptp_str  = f"{ptp}µs"  if ptp  is not None else "N/A"

        self.logger.warning(
            f"Module {module_id} has not sent a heartbeat for {time_diff:.0f}s "
            f"(suspicion threshold: {self.suspicion_timeout}s, hard timeout: {self.heartbeat_timeout}s)\n"
            f"  Last heartbeat: {last_hb_str}\n"
            f"  Last known metrics: CPU {cpu_str}  {temp_str}  MEM {mem_str}  DISK {disk_str}  PTP {ptp_str}\n"
            f"  Initiating probe sequence..."
        )
        self._probe_module(module_id)


    def _probe_module(self, module_id: str) -> dict:
        """Probe a suspected-offline module.

        Returns dict with keys: ping (bool), tcp_port (bool), status_cmd_sent (bool)
        """
        now = time.time()
        health = self.module_health[module_id]
        health['probe_count'] = health.get('probe_count', 0) + 1
        health['last_probe_time'] = now
        probe_n = health['probe_count']

        module_ip = self.facade.get_module_ip(module_id)

        # Check 1 — ICMP ping (list args avoids shell injection)
        try:
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '2', module_ip],
                capture_output=True
            )
            ping_ok = result.returncode == 0
        except Exception as e:
            self.logger.error(f"Ping error for {module_id}: {e}")
            ping_ok = False

        # Check 2 — TCP port 22 (SSH)
        tcp_ok = self._check_tcp_port(module_ip)

        # Check 3 — Send get_status command (only if ping succeeded)
        status_sent = False
        if ping_ok:
            try:
                self.facade.send_command(module_id, 'get_status', {})
                status_sent = True
            except Exception as e:
                self.logger.error(f"Could not send get_status to {module_id}: {e}")

        ping_str = "OK" if ping_ok else "FAILED"
        tcp_str  = "OPEN" if tcp_ok else "CLOSED"
        self.logger.info(
            f"Probing {module_id} (attempt {probe_n}/{self.max_probe_attempts})"
            f" — ping: {ping_str} — TCP port 22: {tcp_str}"
            f" — get_status: {'sent' if status_sent else 'not sent (ping failed)'}"
        )

        result_dict = {'ping': ping_ok, 'tcp_port': tcp_ok, 'status_cmd_sent': status_sent}

        # After max attempts with no response, confirm offline
        if probe_n >= self.max_probe_attempts and not ping_ok and not tcp_ok:
            last_hb = health.get('last_heartbeat', 0)
            self._confirm_module_offline(module_id, now - last_hb)

        return result_dict


    def _check_tcp_port(self, ip: str, port: int = 22, timeout: float = 2.0) -> bool:
        """Check if a TCP port is open."""
        try:
            with _socket.create_connection((ip, port), timeout=timeout):
                return True
        except OSError:
            return False


    def _confirm_module_offline(self, module_id: str, time_diff: float):
        """Confirm a module is definitively offline and fire callbacks."""
        now = time.time()
        health = self.module_health[module_id]

        # Avoid double-firing if already set to offline
        if health['status'] == 'offline':
            return

        health['status'] = 'offline'
        health['offline_since'] = now

        suspected_since = health.get('suspected_since')
        suspected_ago = (now - suspected_since) if suspected_since else None
        probe_count = health.get('probe_count', 0)

        last_hb = health.get('last_heartbeat', 0)
        last_hb_str = time.strftime('%H:%M:%S', time.localtime(last_hb)) if last_hb else 'never'

        cpu  = health.get('cpu_usage')
        temp = health.get('cpu_temp')
        mem  = health.get('memory_usage')
        disk = health.get('disk_space')
        ptp  = health.get('ptp4l_offset')

        cpu_str  = f"{cpu}%"   if cpu  is not None else "N/A"
        temp_str = f"{temp}°C" if temp is not None else "N/A"
        mem_str  = f"{mem}%"   if mem  is not None else "N/A"
        disk_str = f"{disk}%"  if disk is not None else "N/A"
        ptp_str  = f"{ptp}µs"  if ptp  is not None else "N/A"

        suspected_str = f"{suspected_ago:.0f}s ago" if suspected_ago is not None else "N/A"

        self.logger.error(
            f"Module {module_id} confirmed offline after {time_diff:.0f}s silence\n"
            f"  Probe attempts: {probe_count}  Suspected since: {suspected_str}\n"
            f"  Last seen: {last_hb_str}  Last metrics: CPU {cpu_str}  {temp_str}  MEM {mem_str}  DISK {disk_str}  PTP {ptp_str}"
        )

        try:
            self.facade.on_status_change(module_id, 'offline')
        except Exception as e:
            self.logger.error(f"Error in status change callback: {e}")


    def _mark_module_online(self, module_id: str, trigger: str = "heartbeat received"):
        """Mark a module as back online after being offline or suspected."""
        now = time.time()
        health = self.module_health[module_id]
        prev_status = health['status']

        since = health.get('offline_since') if prev_status == 'offline' else health.get('suspected_since')
        duration = (now - since) if since else None
        duration_str = f"{duration:.0f}s" if duration is not None else "unknown duration"

        health['status'] = 'online'
        health['offline_since'] = None
        health['suspected_since'] = None
        health['probe_count'] = 0
        health['last_probe_time'] = None
        health['last_confirmed_online'] = now

        self.logger.info(
            f"Module {module_id} is back online (was {prev_status} for {duration_str})\n"
            f"  Recovery triggered by: {trigger}"
        )

        try:
            self.facade.on_status_change(module_id, 'online')
        except Exception as e:
            self.logger.error(f"Error in status change callback: {e}")


    def _check_ptp_health(self):
        """
        Check received PTP stats and reset PTP if necessary
        """
        reset_flag = False
        for module in self.module_health:
            # TODO: Consider putting all ptp params in a nested dict here that we could loop through e.g. for param in self.module_health[module]["ptp"]:
            if self.module_health[module]["ptp4l_freq"] is not None:
                if abs(self.module_health[module]["ptp4l_freq"]) > 100000:
                    self.logger.warning(f"ptp4l_freq offset too high for module {module}: {self.module_health[module]['ptp4l_freq']}")
                    reset_flag = True
            if self.module_health[module]["phc2sys_freq"] is not None:
                if abs(self.module_health[module]["phc2sys_freq"]) > 100000:
                    self.logger.warning(f"phc2sys_freq offset too high for module {module}: {self.module_health[module]['phc2sys_freq']}")
                    reset_flag = True
            if self.module_health[module]["ptp4l_offset"] is not None:
                if abs(self.module_health[module]["ptp4l_offset"]) > 10000:
                    self.logger.warning(f"ptp4l_offset too high for module {module}: {self.module_health[module]['ptp4l_offset']}")
                    reset_flag = True
            if self.module_health[module]["phc2sys_offset"] is not None:
                if abs(self.module_health[module]["phc2sys_offset"]) > 10000:
                    self.logger.warning(f"phc2sys_offset too high for module {module}: {self.module_health[module]['phc2sys_offset']}")
                    reset_flag = True
            if reset_flag == True:
                if (time.time() - self.module_health[module]["last_ptp_restart"]) > (2**self.module_health[module]["ptp_restarts"]) * 60: # Exponential backoff? Sort of.
                    self.logger.info(f"Telling {module} to restart_ptp")
                    self.module_health[module]["last_ptp_restart"] = time.time()
                    self.module_health[module]["ptp_restarts"] += 1
                    if self.module_health[module]["ptp_restarts"] >= 5:
                        self.module_health[module]["ptp_restarts"] = 5
                    self.facade.send_command(module, "restart_ptp", {})


    def start_monitoring(self):
        """Start the health monitoring thread"""
        if self.is_monitoring:
            self.logger.warning("Health monitoring is already running")
            return

        self.is_monitoring = True
        self.monitor_thread = threading.Thread(target=self.monitor_health, daemon=True)
        self.monitor_thread.start()
        self.logger.info(f"Started health monitoring with {self.heartbeat_interval}s interval")


    def stop_monitoring(self):
        """Stop the health monitoring thread"""
        self.is_monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        self.logger.info("Stopped health monitoring")


    def clear_all_health(self):
        """Clear all health data"""
        self.module_health.clear()
        self.module_health_history.clear()
        self.logger.info("Cleared all health data")


    def mark_module_offline(self, module_id: str, reason: str = "Communication test failed"):
        """Mark a module as offline due to communication failure

        Args:
            module_id: ID of the module to mark offline
            reason: Reason for marking the module offline
        """
        if module_id in self.module_health:
            if self.module_health[module_id]['status'] != 'offline':
                self.logger.warning(f"Module {module_id} marked offline: {reason}")
                self.module_health[module_id]['status'] = 'offline'
                self.module_health[module_id]['offline_since'] = time.time()

                try:
                    self.facade.on_status_change(module_id, 'offline')
                except Exception as e:
                    self.logger.error(f"Error in status change callback: {e}")
            else:
                self.logger.info(f"Module {module_id} already offline: {reason}")
        else:
            self.logger.warning(f"Attempted to mark unknown module {module_id} as offline: {reason}")


    def module_rediscovered(self, module_id: str) -> None:
        if module_id in self.module_health:
            if self.module_health[module_id]["status"] in ("offline", "suspected"):
                self._probe_module(module_id)


    def handle_communication_test_response(self, module_id: str, success: bool):
        """Handle communication test response from a module

        Args:
            module_id: ID of the module that responded
            success: Whether the communication test was successful
        """
        if module_id in self.module_health:
            if success:
                # Communication test successful - ensure module is marked online
                if self.module_health[module_id]['status'] != 'online':
                    self.logger.info(f"Module {module_id} communication test successful - marking online")
                    self._mark_module_online(module_id)
                else:
                    self.logger.info(f"Module {module_id} communication test successful - already online")
            else:
                # Communication test failed - mark module as offline
                self.mark_module_offline(module_id, "Communication test failed")
        else:
            self.logger.warning(f"Communication test response from unknown module {module_id}")
