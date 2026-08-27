#!/usr/bin/env python3
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

import csv
import logging
import socket as _socket
import subprocess
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from src.shared.health import ModuleHealthSnapshot


class _CsvEcho:
    """A no-op "file" that csv.writer can write a single formatted row to --
    write() just returns the row string straight back instead of buffering
    it, so a generator can yield each row as it's produced (the standard
    pattern for streaming CSV out of the csv module, which otherwise only
    knows how to write to a real file-like object)."""
    def write(self, row: str) -> str:
        return row


class Health:
    # How long PTP history samples are retained before being pruned, per
    # module. 8 days comfortably covers "plot the last 24h/week" without
    # unbounded growth over a long unattended deployment (e.g. habitat).
    _PTP_HISTORY_RETENTION_S = 8 * 24 * 3600

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

        # Consecutive heartbeats required from an offline/suspected module before marking
        # it online via the periodic heartbeat path. Probe and ZMQ proof-of-life paths
        # remain immediate since those are deliberate confirmations.
        self._online_heartbeat_threshold = self.config.get("health.online_heartbeat_threshold", 2)

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
        # PTP offset/freq history per module, one entry per heartbeat --
        # module_id -> deque of {timestamp, ptp4l_offset_ns(_min/_max),
        # phc2sys_offset_ns(_min/_max), ptp4l_freq, phc2sys_freq}. Pruned by
        # age (_PTP_HISTORY_RETENTION_S) rather than a fixed entry count, so
        # retention doesn't silently shrink/grow if heartbeat_interval is
        # ever changed. Kept slim (PTP fields only, not the full health
        # snapshot) since this exists specifically to let an operator export
        # and plot fleet-wide PTP sync quality over a long unattended run
        # (e.g. habitat) -- not as a general audit log of every metric.
        self.module_health_history = {}
        self.controller_health = {} # Historical controller health data.

        # Module online/offline states
        self.module_states = {}

        # Control flags
        self.is_monitoring = False
        self.monitor_thread = None

        # Modules explicitly force-offlined (e.g. mDNS goodbye). These must not
        # be re-marked online by stale ZMQ messages or the heartbeat monitor loop;
        # only a fresh mDNS re-discovery clears the flag.
        self._force_offline_ids: set = set()

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
            if module_id in self._force_offline_ids:
                # Stale ZMQ message after an explicit shutdown — ignore it.
                return
            self.module_health[module_id]['last_heartbeat'] = time.time()
            if self.module_health[module_id].get('status') in ('offline', 'suspected'):
                self._mark_module_online(module_id, trigger="ZMQ message received (proof of life)")

    def remove_module(self, module_id: str):
        self._force_offline_ids.discard(module_id)
        if module_id in self.module_health.keys():
            self.module_health.pop(module_id)

    def force_offline(self, module_id: str) -> None:
        """Immediately mark a module offline — used when mDNS goodbye is received.

        Resets last_heartbeat to 0 so the health monitor loop cannot re-mark the
        module online based on a recently-refreshed heartbeat timestamp. The
        _force_offline_ids guard prevents stale ZMQ messages (e.g. streaming_stopped
        delivered after the mDNS goodbye) from undoing the offline state.
        """
        if module_id in self.module_health:
            self._force_offline_ids.add(module_id)
            self.module_health[module_id]['last_heartbeat'] = 0
            self._confirm_module_offline(module_id, 0)


    _PTP_HISTORY_FIELDS = (
        'ptp4l_offset_ns', 'ptp4l_offset_ns_min', 'ptp4l_offset_ns_max',
        'phc2sys_offset_ns', 'phc2sys_offset_ns_min', 'phc2sys_offset_ns_max',
        'ptp4l_freq', 'phc2sys_freq',
    )

    def _record_ptp_sample(self, module_id: str, timestamp: float) -> None:
        """Append one PTP sample for module_id to its history and prune
        anything older than _PTP_HISTORY_RETENTION_S. Reads from
        self.module_health[module_id], which update_module_health has
        already merged the latest heartbeat's fields into by the time this
        is called."""
        health = self.module_health.get(module_id)
        if health is None:
            return
        sample = {'timestamp': timestamp}
        sample.update({field: health.get(field) for field in self._PTP_HISTORY_FIELDS})
        history = self.module_health_history.setdefault(module_id, deque())
        history.append(sample)
        cutoff = timestamp - self._PTP_HISTORY_RETENTION_S
        while history and history[0]['timestamp'] < cutoff:
            history.popleft()

    def export_ptp_history_csv(self, hours: float | None = 24.0):
        """Yields recorded PTP history as CSV text, one row (or the header)
        per yield, oldest first per module -- meant for an operator to plot
        fleet-wide PTP sync quality over an unattended run (e.g. habitat)
        rather than for in-app display. timestamp_utc is ISO 8601 for direct
        use in a plotting tool; timestamp_epoch is kept alongside for exact
        numeric deltas.

        hours: only include samples from the last `hours` hours (default
        24, matching the "plot the last day" use case this was built for).
        None means the entire retained history (up to
        _PTP_HISTORY_RETENTION_S) -- every sample currently buffered, not
        just a recent window.

        A generator rather than a single string so the caller (web.py's
        download route) can stream the response -- at fleet scale over the
        full retention window this can run to tens of MB, and building the
        whole thing in memory first would spike well past that (measured:
        ~106MB transient for a 20-module/8-day export vs. the ~134MB the
        underlying history buffer already holds), for a response nothing
        needs faster than it can be read off the wire anyway."""
        cutoff = time.time() - hours * 3600 if hours is not None else None
        writer = csv.writer(_CsvEcho())
        yield writer.writerow(
            ['module_id', 'timestamp_utc', 'timestamp_epoch', *self._PTP_HISTORY_FIELDS]
        )
        for module_id, history in self.module_health_history.items():
            for sample in history:
                if cutoff is not None and sample['timestamp'] < cutoff:
                    continue
                ts_utc = datetime.fromtimestamp(sample['timestamp'], tz=UTC).isoformat()
                yield writer.writerow([
                    module_id, ts_utc, sample['timestamp'],
                    *(sample.get(field) for field in self._PTP_HISTORY_FIELDS),
                ])


    def update_module_health(self, module_id: str, status_data: dict[str, Any]) -> bool:
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
                    **ModuleHealthSnapshot.from_dict(status_data).to_dict(),
                    'last_heartbeat': now,
                    'status': 'online',
                    'last_ptp_restart': now,
                    'ptp_restarts': 1,
                    'offline_since': None,
                    'suspected_since': None,
                    'probe_count': 0,
                    'last_probe_time': None,
                    'last_confirmed_online': now,
                    'pending_online_count': 0,
                }
            else:
                # Existing module - heartbeat received
                now = time.time()
                self.module_health[module_id]['last_heartbeat'] = now
                prev_status = self.module_health[module_id]['status']
                if prev_status in ('offline', 'suspected'):
                    count = self.module_health[module_id].get('pending_online_count', 0) + 1
                    self.module_health[module_id]['pending_online_count'] = count
                    if count >= self._online_heartbeat_threshold:
                        self._mark_module_online(
                            module_id,
                            trigger=f"heartbeat received ({count} consecutive)"
                        )
                    else:
                        self.logger.info(
                            f"Heartbeat {count}/{self._online_heartbeat_threshold} from "
                            f"{prev_status} module {module_id} — waiting for more before marking online"
                        )

                # Update snapshot fields — only keys present in status_data are touched
                for key in ModuleHealthSnapshot.field_names():
                    if key in status_data:
                        self.module_health[module_id][key] = status_data[key]
                if "last_ptp_restart" not in self.module_health[module_id]:
                    self.module_health[module_id]["last_ptp_restart"] = now
                if "ptp_restarts" not in self.module_health[module_id]:
                    self.module_health[module_id]["ptp_restarts"] = 1

            self._record_ptp_sample(module_id, now)

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
        # Module came back via mDNS — lift any force-offline guard so ZMQ
        # messages and heartbeats can mark it online again.
        self._force_offline_ids.discard(module.id)
        if module.id not in self.module_health:
            self.logger.info(f"Discovered new module {module.id}, adding to health tracking")
            now = time.time()
            self.module_health[module.id] = {
                **ModuleHealthSnapshot().to_dict(),
                'timestamp': now,
                'last_heartbeat': 0,  # No heartbeat yet
                'status': 'offline',  # Start as offline until first heartbeat
                'offline_since': now,
                'suspected_since': None,
                'probe_count': 0,
                'last_probe_time': None,
                'last_confirmed_online': None,
                'last_ptp_restart': now,
                'ptp_restarts': 1,
            }


    def module_id_changed(self, old_module_id, new_module_id):
        # Move the module data to the new key
        self.module_health[new_module_id] = self.module_health.pop(old_module_id)
        if old_module_id in self.module_health_history:
            self.module_health_history[new_module_id] = self.module_health_history.pop(old_module_id)


    """Get methods"""
    def get_module_health_history(self, module_id: str, limit: int | None = None) -> list[dict[str, Any]]:
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


    def get_module_health(self, module_id: str | None = None) -> dict:
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


    def get_health_summary(self) -> dict[str, Any]:
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
            metrics = ['cpu_usage', 'memory_usage', 'cpu_temp', 'ptp4l_offset_ns', 'ptp4l_freq']
            for metric in metrics:
                values = []
                for module_id in online_modules:
                    if module_id in self.module_health and metric in self.module_health[module_id]:
                        value = self.module_health[module_id][metric]
                        if value is not None:
                            values.append(value)
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
            ptp_sync = self.module_health[module_id]["ptp4l_offset_ns"]
            if ptp_sync is None:
                return None
            max_ptp_sync = max(max_ptp_sync, abs(ptp_sync))
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
                    if status in ('offline', 'suspected') and module_id not in self._force_offline_ids:
                        self._mark_module_online(module_id, trigger="heartbeat received")

                elif time_diff <= self.heartbeat_timeout:
                    # In the suspicion window
                    if status == 'online':
                        self._enter_suspicion(module_id, time_diff)
                    elif status == 'suspected':
                        last_probe = self.module_health[module_id].get('last_probe_time') or 0
                        if current_time - last_probe >= self.probe_interval:
                            self._probe_module(module_id)

                # Hard timeout exceeded — confirm offline
                elif status in ('online', 'suspected'):
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
        ptp  = health.get('ptp4l_offset_ns')

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
        health['pending_online_count'] = 0

        suspected_since = health.get('suspected_since')
        suspected_ago = (now - suspected_since) if suspected_since else None
        probe_count = health.get('probe_count', 0)

        last_hb = health.get('last_heartbeat', 0)
        last_hb_str = time.strftime('%H:%M:%S', time.localtime(last_hb)) if last_hb else 'never'

        cpu  = health.get('cpu_usage')
        temp = health.get('cpu_temp')
        mem  = health.get('memory_usage')
        disk = health.get('disk_space')
        ptp  = health.get('ptp4l_offset_ns')

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
        health['pending_online_count'] = 0

        self.logger.info(
            f"Module {module_id} is back online (was {prev_status} for {duration_str})\n"
            f"  Recovery triggered by: {trigger}"
        )

        try:
            self.facade.on_status_change(module_id, 'online')
        except Exception as e:
            self.logger.error(f"Error in status change callback: {e}")


    # ----- PTP auto-restart tuning ---------------------------------------------
    # Offset above which a module's PTP servo is considered broken enough to
    # justify a restart. Deliberately AT the recording gate (50us), not below
    # it: a transient 10-20us excursion is harmless to a recording (frames
    # carry their own wall-clock timestamps and analyse_framesync.py detrends
    # slow drift), whereas a restart *guarantees* a multi-second sync loss and
    # a minutes-long reconvergence. Found 2026-08-27: the old 10us threshold,
    # combined with a function-scoped `reset_flag` bug (one bad module dragged
    # every other module into a restart) and a backoff counter that only ever
    # ratcheted up, turned this watchdog into a self-sustaining fleet-wide
    # restart loop on a 16-camera habitat rig.
    _PTP_OFFSET_RESTART_NS = 50_000
    # Consecutive over-threshold checks before acting — rides out a single
    # noisy sample (e.g. an export burst crossing a non-PTP switch).
    _PTP_BREACH_COUNT_TO_RESTART = 3
    # After issuing a restart, leave the module alone this long: ptp4l/phc2sys
    # need minutes to re-lock and their offset/freq legitimately spike during
    # that window — counting it as "still broken" is what sustained the loop.
    _PTP_RESTART_GRACE_SECS = 600
    # Under threshold continuously for this long → decay the restart backoff
    # counter back to 1, so a module that recovers stops being on a 32-min
    # restart cadence forever.
    _PTP_HEALTHY_RESET_SECS = 900
    # Frequency magnitude is not a meaningful health signal (settled hardware
    # sits at 20-70k ppb; see the camera-framesync notes) and a post-restart
    # transient routinely blows past any fixed limit — so freq is logged for
    # visibility but never triggers a restart on its own.
    _PTP_FREQ_WARN_PPB = 500_000
    # A PTP restart mid-recording guarantees a multi-second gap, forces a
    # segment boundary and a FrameSync re-lock — worse for the data than a
    # large-but-bounded offset, which per-frame timestamps + analyse_framesync
    # recover in post. So while a module is recording the watchdog only
    # restarts on a *catastrophic* sustained offset (the data is worthless
    # anyway); anything between the normal gate and this is logged, not acted
    # on. Set health.ptp_no_restart_while_recording=false to revert to gating
    # a recording module on the plain offset threshold.
    _PTP_NO_RESTART_WHILE_RECORDING = True
    _PTP_RECORDING_OVERRIDE_NS = 1_000_000

    def _check_ptp_health(self):
        """Per-module PTP watchdog.

        Asks a module to restart ptp4l/phc2sys only when its offset is
        *sustained* above the recording gate, with a grace period after each
        restart and a backoff counter that actually recovers. A module that is
        currently recording is held to a much higher (catastrophic-only) bar —
        see _PTP_RECORDING_OVERRIDE_NS.

        Every module is judged entirely on its own state — a breach on one
        module must never cause a restart on another. (Pre-2026-08-27 the
        `reset_flag` was function-scoped, so the first over-threshold module in
        iteration order pulled every other module past its backoff window into
        a restart too, which is what made the restarts look fleet-wide.)
        """
        now = time.time()
        offset_limit = self.config.get(
            "health.ptp_offset_restart_ns", self._PTP_OFFSET_RESTART_NS)
        breach_limit = self.config.get(
            "health.ptp_breach_count_to_restart", self._PTP_BREACH_COUNT_TO_RESTART)
        grace_secs = self.config.get(
            "health.ptp_restart_grace_secs", self._PTP_RESTART_GRACE_SECS)
        healthy_reset_secs = self.config.get(
            "health.ptp_healthy_reset_secs", self._PTP_HEALTHY_RESET_SECS)
        no_restart_recording = self.config.get(
            "health.ptp_no_restart_while_recording",
            self._PTP_NO_RESTART_WHILE_RECORDING)
        recording_override_ns = self.config.get(
            "health.ptp_recording_override_ns", self._PTP_RECORDING_OVERRIDE_NS)

        for module in list(self.module_health.keys()):
            h = self.module_health[module]

            o4 = h.get("ptp4l_offset_ns")
            op = h.get("phc2sys_offset_ns")
            worst = max(
                abs(o4) if o4 is not None else 0,
                abs(op) if op is not None else 0,
            )

            for label, val in (("ptp4l_freq", h.get("ptp4l_freq")),
                               ("phc2sys_freq", h.get("phc2sys_freq"))):
                if val is not None and abs(val) > self._PTP_FREQ_WARN_PPB:
                    self.logger.warning(
                        f"{label} very high for {module}: {val} ppb "
                        f"(logged only — not restarting on frequency alone)")

            try:
                recording = bool(self.facade.is_module_recording(module))
            except Exception:
                recording = False

            restart_limit = offset_limit
            if recording and no_restart_recording:
                restart_limit = recording_override_ns
                if offset_limit < worst <= recording_override_ns:
                    self.logger.warning(
                        f"{module} PTP offset {worst}ns over {offset_limit}ns "
                        f"but module is recording — not restarting (recoverable "
                        f"via per-frame timestamps); would restart above "
                        f"{recording_override_ns}ns")

            over = worst > restart_limit

            # Don't re-judge a module that was just restarted — it is expected
            # to be out of spec while the servo re-locks.
            if now - h.get("last_ptp_restart", 0) < grace_secs:
                continue

            if not over:
                h["ptp_breach_count"] = 0
                healthy_since = h.get("ptp_healthy_since")
                if healthy_since is None:
                    h["ptp_healthy_since"] = now
                elif (now - healthy_since > healthy_reset_secs
                      and h.get("ptp_restarts", 1) > 1):
                    self.logger.info(
                        f"{module} PTP within threshold for "
                        f">{healthy_reset_secs // 60} min — resetting restart backoff")
                    h["ptp_restarts"] = 1
                continue

            # Over threshold — require it to persist before acting.
            h["ptp_healthy_since"] = None
            h["ptp_breach_count"] = h.get("ptp_breach_count", 0) + 1
            self.logger.warning(
                f"PTP offset over {restart_limit}ns for {module}: "
                f"ptp4l={o4}ns phc2sys={op}ns "
                f"(breach {h['ptp_breach_count']}/{breach_limit})")
            if h["ptp_breach_count"] < breach_limit:
                continue

            backoff = (2 ** h.get("ptp_restarts", 1)) * 60
            if now - h.get("last_ptp_restart", 0) > backoff:
                self.logger.info(
                    f"Telling {module} to restart_ptp "
                    f"(offset over threshold for {breach_limit} consecutive checks)")
                h["last_ptp_restart"] = now
                h["ptp_restarts"] = min(5, h.get("ptp_restarts", 1) + 1)
                h["ptp_breach_count"] = 0
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
                self.module_health[module_id]['pending_online_count'] = 0

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
