#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Module Communication Manager

This class is responsible for handling all ZMQ-based messaging between
a module and the controller, including:
- Command subscription from the controller
- Status/data publishing to the controller
- Heartbeat mechanism
- Message handling and routing
"""

import zmq
import threading
import logging
import time
from typing import Callable, Dict, Any, Optional
from zmq.utils.monitor import recv_monitor_message

class Communication:
    def __init__(self,
                 config = None):
        """Initialize the communication manager
        
        Args:
            logger: Logger instance
            module_id: The unique identifier for this module
            config: Configuration manager for retrieving settings
        """
        self.logger = logging.getLogger(__name__)
        self.config = config
        
        self.group = self.config.get("module.group")
        
        # Control flags
        self.command_listener_running = False
        self.last_command = None
        
        # Controller connection info
        self.controller_ip = None
        self.controller_port = None
        
        # Connection state tracking
        self.connection_attempts = 0
        self.max_connection_attempts = self.config.get("network.reconnect_attempts", 5) if config else 5
        self.connection_delay = self.config.get("network.reconnect_delay", 5) if config else 5
        self.last_connection_time = None
        
        # ZeroMQ setup - initialized but not connected
        self.context = zmq.Context()
        self.command_socket = self.context.socket(zmq.DEALER)
        self.status_socket = self.context.socket(zmq.PUB)

        # Command listener thread
        self.command_thread = None

        # Heartbeat ack watchdog
        self._ack_lock = threading.Lock()
        self.last_ack_time = None
        self.consecutive_missed_acks = 0
        self.has_received_ack = False
        self._MISSED_ACK_THRESHOLD = 2

        # Prevents concurrent _force_reconnect threads from racing on cleanup()
        self._reconnect_lock = threading.Lock()

        # Guards command_socket.send() — the initial hello (connect()) and
        # monitor-triggered re-hello (_watch_dealer_monitor()) run on different
        # threads and must not write to the DEALER socket at the same time.
        self._send_lock = threading.Lock()

        # ZMQ-level reconnect watchdog: fires immediately when the DEALER's
        # underlying TCP session re-establishes (e.g. controller process
        # restarted), instead of waiting for the next heartbeat-ack cycle.
        self._monitor_socket = None
        self._monitor_thread = None
        self._monitor_running = False


    def connect(self, controller_ip: str, controller_port: int) -> bool:
        """Connect to the controller's ZMQ sockets
        
        Args:
            controller_ip: IP address of the controller
            controller_port: Port number of the controller
            
        Returns:
            bool: True if connection was successful
        """
        try:
            # Check if already connected to the same controller
            if (self.controller_ip == controller_ip and 
                self.controller_port == controller_port and
                self.command_listener_running):
                self.logger.info("Already connected to this controller")
                return True
            
            # Clean up existing connection if connecting to different controller
            if self.controller_ip and self.controller_ip != controller_ip:
                self.logger.info("Connecting to different controller, cleaning up existing connection")
                self.cleanup()
            
            # Store controller information
            self.controller_ip = controller_ip
            self.controller_port = controller_port
            
            # Get ports from config if available
            if self.config:
                command_port = self.config.get("communication.command_socket_port", 5555)
                status_port = self.config.get("communication.status_socket_port", 5556)
            else:
                command_port = 5555
                status_port = 5556
            
            # Set DEALER identity to module_id before connecting — must be done
            # before the first connect() call; cannot be changed afterwards.
            module_id = self.facade.get_module_id()
            self.command_socket.setsockopt(zmq.IDENTITY, module_id.encode())

            # Attach the ZMQ reconnect monitor before connecting so it can't
            # race the initial EVENT_CONNECTED — _watch_dealer_monitor()
            # skips that first event since the explicit hello below covers it.
            self._start_dealer_monitor()

            # Connect sockets
            self.logger.info(f"Attempting to connect command socket to tcp://{controller_ip}:{command_port}")
            self.command_socket.connect(f"tcp://{controller_ip}:{command_port}")
            self.logger.info(f"Attempting to connect status socket to tcp://{controller_ip}:{status_port}")
            self.status_socket.connect(f"tcp://{controller_ip}:{status_port}")

            # Register with the controller's ROUTER by sending a hello frame.
            # The ROUTER sees our identity (set above) in the envelope and adds
            # us to its routing table so it can send commands back to us.
            self._send_hello()
            self.logger.info(f"Connected to controller command socket at {controller_ip}:{command_port}, status socket at {controller_ip}:{status_port}")
            
            # Reset connection tracking on successful connection
            self.connection_attempts = 0
            self.last_connection_time = time.time()
            
            return True
        except Exception as e:
            self.logger.error(f"Error connecting to controller: {e}")
            self.connection_attempts += 1
            return False


    def _send_hello(self):
        """Send (or re-send) the DEALER registration frame. Safe to call from
        any thread — guarded by _send_lock since the initial connect() and
        the monitor watchdog thread can both call this."""
        with self._send_lock:
            try:
                if self.command_socket:
                    self.command_socket.send(b"hello")
                    self.logger.info("Hello sent to controller ROUTER")
            except Exception as e:
                self.logger.error(f"Error sending hello: {e}")


    def _start_dealer_monitor(self):
        """Attach a ZMQ monitor to the command socket so a transport-level
        reconnect (e.g. the controller process restarted and our DEALER's TCP
        session re-established) is detected immediately, instead of waiting
        up to _MISSED_ACK_THRESHOLD heartbeat cycles for the ack watchdog to
        notice. Only EVENT_CONNECTED is requested — that's the only event we
        act on."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            # Already watching this socket (e.g. re-entered via _attempt_reconnection
            # without an intervening cleanup()) — nothing to do.
            return
        try:
            self._monitor_socket = self.command_socket.get_monitor_socket(zmq.EVENT_CONNECTED)
        except Exception as e:
            self.logger.warning(f"Could not attach command socket monitor: {e}")
            self._monitor_socket = None
            return

        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._watch_dealer_monitor, daemon=True, name="dealer-monitor"
        )
        self._monitor_thread.start()


    def _watch_dealer_monitor(self):
        """Background loop: re-send hello the instant the command socket's
        underlying TCP connection re-establishes. The first EVENT_CONNECTED
        corresponds to the connect() call that started this monitor (hello
        already sent explicitly there) — only later ones mean a reconnect."""
        monitor = self._monitor_socket
        if monitor is None:
            return
        monitor.setsockopt(zmq.RCVTIMEO, 1000)

        seen_first_connect = False
        while self._monitor_running:
            try:
                event = recv_monitor_message(monitor)
            except zmq.Again:
                continue
            except (zmq.ZMQError, zmq.error.ContextTerminated):
                break
            except Exception as e:
                if self._monitor_running:
                    self.logger.debug(f"Dealer monitor stopped: {e}")
                break

            if event.get("event") != zmq.EVENT_CONNECTED:
                continue

            if not seen_first_connect:
                seen_first_connect = True
                continue

            self.logger.warning(
                "Command socket reconnected at the ZMQ transport layer "
                "(controller likely restarted) — re-sending hello immediately"
            )
            self._send_hello()


    def group_changed(self):
        # Group routing is now handled server-side by the controller's ROUTER.
        # The module just needs to stay connected — no subscription changes needed.
        self.group = self.config.get("module.group")

    def subscribe_to_topic(self, topic: str) -> None:
        pass  # No-op: DEALER/ROUTER — controller routes by identity, not topic

    def unsubscribe_from_topic(self, topic: str) -> None:
        pass  # No-op: DEALER/ROUTER — controller routes by identity, not topic


    def start_command_listener(self) -> bool:
        """Start the command listener thread
        
        Returns:
            bool: True if the listener was started successfully
        """
        if self.command_listener_running:
            self.logger.info("Command listener already running")
            return True
        
        if not self.controller_ip:
            self.logger.error("Cannot start command listener: not connected to controller")
            return False
        
        self.command_listener_running = True
        self.command_thread = threading.Thread(target=self.listen_for_commands, daemon=True)
        self.command_thread.start()
        self.logger.info("Command listener thread started")
        return True


    def listen_for_commands(self):
        """Listen for commands from the controller"""
        # DEALER receives a single frame: "<command> <params>" (no topic prefix)
        self.logger.info("Starting command listener thread")

        # Set socket timeout to prevent blocking indefinitely
        self.command_socket.setsockopt(zmq.RCVTIMEO, 5000)  # 5 second timeout

        while self.command_listener_running:
            try:
                command = self.command_socket.recv_string()

                # Intercept heartbeat_ack at the transport layer — no facade dispatch needed
                cmd_type = command.split(' ', 1)[0]
                if cmd_type == "heartbeat_ack":
                    self._on_heartbeat_ack()
                    continue

                # Store the command immediately after parsing
                self.last_command = command
                self.logger.info(f"Stored command: {self.last_command}")

                # Call the command handler
                try:
                    self.facade.handle_command(command)
                except Exception as e:
                    self.logger.error(f"Error handling command: {e}")

            except zmq.Again:
                # Timeout occurred, check if we should still be running
                if not self.command_listener_running:
                    break
                continue
            except Exception as e:
                if self.command_listener_running:  # Only log if we're still supposed to be running
                    self.logger.error(f"Error receiving command: {e}")
                    # Check if this is a connection error and attempt reconnection
                    if "Connection refused" in str(e) or "No route to host" in str(e):
                        self.logger.warning("Connection error detected, will attempt reconnection")
                        self._schedule_reconnection()
                time.sleep(0.1)  # Add small delay to prevent tight loop on error


    def _schedule_reconnection(self):
        """Schedule a reconnection attempt"""
        if self.connection_attempts < self.max_connection_attempts:
            self.connection_attempts += 1
            self.logger.info(f"Scheduling reconnection attempt {self.connection_attempts}/{self.max_connection_attempts} in {self.connection_delay} seconds")
            
            # Schedule reconnection in a separate thread
            def delayed_reconnect():
                time.sleep(self.connection_delay)
                if self.controller_ip and not self.command_listener_running:  # Only reconnect if we have controller info
                    self.logger.info(f"Attempting reconnection {self.connection_attempts}/{self.max_connection_attempts}")
                    self._attempt_reconnection()
            
            threading.Thread(target=delayed_reconnect, daemon=True).start()
        else:
            self.logger.warning(f"Max reconnection attempts ({self.max_connection_attempts}) reached")


    def _attempt_reconnection(self):
        """Attempt to reconnect to the controller"""
        try:
            if self.controller_ip and self.controller_port:
                # Attempt to reconnect
                if self.connect(self.controller_ip, self.controller_port):
                    # Restart command listener
                    if self.start_command_listener():
                        self.logger.info("Reconnection successful")
                    else:
                        self.logger.error("Failed to restart command listener after reconnection")
                else:
                    self.logger.error("Failed to reconnect to controller")
            else:
                self.logger.warning("No controller information available for reconnection")
        except Exception as e:
            self.logger.error(f"Error during reconnection attempt: {e}")


    def _on_heartbeat_ack(self):
        """Record a received heartbeat_ack and reset the missed-ack counter."""
        with self._ack_lock:
            self.last_ack_time = time.time()
            self.consecutive_missed_acks = 0
            if not self.has_received_ack:
                self.logger.info("First heartbeat_ack received — ack watchdog is now active")
                self.has_received_ack = True


    def notify_heartbeat_sent(self):
        """Called by health.py after each heartbeat is sent.
        Increments the missed-ack counter; triggers a force-reconnect after
        _MISSED_ACK_THRESHOLD consecutive misses (only once acks have been seen)."""
        with self._ack_lock:
            if not self.has_received_ack:
                return  # Don't count misses before the channel has ever worked
            self.consecutive_missed_acks += 1
            should_reconnect = self.consecutive_missed_acks >= self._MISSED_ACK_THRESHOLD
            missed = self.consecutive_missed_acks

        if should_reconnect:
            self.logger.warning(
                f"{missed} consecutive heartbeat acks missed — "
                "command channel appears dead, forcing ZMQ reconnect"
            )
            threading.Thread(target=self._force_reconnect, daemon=True).start()


    def _force_reconnect(self):
        """Tear down and recreate ZMQ sockets. Used by the ack watchdog.
        Recording, heartbeats, PTP, and Flask are unaffected."""
        if not self._reconnect_lock.acquire(blocking=False):
            self.logger.info("Force reconnect already in progress — skipping duplicate")
            return

        try:
            with self._ack_lock:
                self.consecutive_missed_acks = 0

            controller_ip = self.controller_ip
            controller_port = self.controller_port

            if not controller_ip:
                self.logger.warning("Force reconnect requested but no controller IP stored — skipping")
                return

            self.logger.info(f"Forcing ZMQ reconnect to {controller_ip}:{controller_port}")
            self.cleanup()

            if self.connect(controller_ip, controller_port):
                if self.start_command_listener():
                    self.logger.info("Force reconnect successful — command channel restored")
                else:
                    self.logger.error("Force reconnect: connected but failed to restart command listener")
            else:
                self.logger.error("Force reconnect: connect() failed")
        finally:
            self._reconnect_lock.release()


    def send_status(self, status_data: Dict[str, Any]) -> None:
        """Send status information to the controller
        
        Args:
            status_data: Dictionary containing status information
        """
        try:
            if not self.status_socket:
                self.logger.warning("Status socket not available")
                return
            
            # Add timestamp and module ID to status data
            status_data['timestamp'] = time.time()
            status_data['module_id'] = self.facade.get_module_id()
            status_data['module_name'] = self.facade.get_module_name()
            
            # Convert to JSON string
            import json
            message = json.dumps(status_data)
            
            # Send status
            self.status_socket.send_string(f"status/{self.facade.get_module_id()} {message}")
            # self.logger.info(f"Status sent: {message}")
            
        except Exception as e:
            self.logger.error(f"Error sending status: {e}")
            # Check if this is a connection error
            if "Connection refused" in str(e) or "No route to host" in str(e):
                self.logger.warning("Connection error while sending status, will attempt reconnection")
                self._schedule_reconnection()


    def cleanup(self):
        """Clean up ZMQ connections"""
        self.logger.info(f"Cleaning up communication manager for module {self.facade.get_module_id()}")

        # Signal the listener thread to stop
        self.command_listener_running = False

        # Stop the dealer reconnect monitor before tearing down the command
        # socket it watches. Bounded by the monitor's 1 s RCVTIMEO poll.
        self._monitor_running = False
        if self._monitor_socket:
            try:
                self._monitor_socket.setsockopt(zmq.LINGER, 0)
                self._monitor_socket.close()
            except Exception as e:
                self.logger.warning(f"Error closing dealer monitor socket: {e}")
            self._monitor_socket = None
        if (self._monitor_thread and self._monitor_thread.is_alive()
                and threading.current_thread() is not self._monitor_thread):
            self._monitor_thread.join(timeout=2.0)
        self._monitor_thread = None

        # Close the command socket first — this unblocks recv_string() in the
        # listener thread immediately (raises ZMQError) rather than waiting for
        # RCVTIMEO (5 s). Without this, context.term() has to wait for the ZMQ
        # I/O thread to release the socket, which can take ~30 s on some hosts.
        if hasattr(self, 'command_socket') and self.command_socket:
            self.logger.info("Setting command socket linger to 0")
            try:
                self.command_socket.setsockopt(zmq.LINGER, 0)
                self.logger.info("Closing command socket")
                self.command_socket.close()
            except Exception as e:
                self.logger.warning(f"Error closing command socket: {e}")
            self.command_socket = None

        # Wait for the listener thread to notice the closed socket and exit.
        # RCVTIMEO is 5 s (see listen_for_commands), so the thread may still be
        # blocked in recv_string() for up to 5 s even after close() — the join
        # timeout must outlast that, or this (almost always) "times out" and we
        # fall through to context.term() below with the thread still running.
        thread_exited = True
        if hasattr(self, 'command_thread') and self.command_thread and self.command_thread.is_alive():
            if threading.current_thread() is self.command_thread:
                # cleanup() was triggered by a command handler (e.g. "shutdown")
                # running inside the listener thread itself — joining here would
                # raise RuntimeError: cannot join current thread. The socket is
                # already closed above, so the thread will exit on its own once
                # this handler returns; nothing left to wait for.
                thread_exited = False
            else:
                self.command_thread.join(timeout=6.0)
                thread_exited = not self.command_thread.is_alive()
                if not thread_exited:
                    self.logger.warning("Command listener thread did not exit within 6 s after socket close")

        # Close status socket
        if hasattr(self, 'status_socket') and self.status_socket:
            self.logger.info("Setting status socket linger to 0")
            try:
                self.status_socket.setsockopt(zmq.LINGER, 0)
                self.logger.info("Closing status socket")
                self.status_socket.close()
            except Exception as e:
                self.logger.warning(f"Error closing status socket: {e}")
            self.status_socket = None

        # Terminate context. term() blocks until every socket from this context
        # is closed — if the listener thread is somehow still stuck (the 6 s
        # join above failed), term() would block forever, deadlocking this
        # method while it holds _reconnect_lock and silently disabling every
        # future reconnect attempt. destroy() forcibly closes any remaining
        # sockets instead of waiting, so we always get unstuck.
        if hasattr(self, 'context') and self.context:
            self.logger.info("Terminating ZMQ context")
            try:
                if thread_exited:
                    self.context.term()
                else:
                    self.context.destroy(linger=0)
            except Exception as e:
                self.logger.warning(f"ZMQ context term error (non-fatal): {e}")
            self.context = None

        # Reset connection state
        self.controller_ip = None
        self.controller_port = None
        self.connection_attempts = 0

        # Recreate context and sockets for the next connect() call
        try:
            self.context = zmq.Context()
            self.command_socket = self.context.socket(zmq.DEALER)
            self.status_socket = self.context.socket(zmq.PUB)
            self.logger.info("ZeroMQ resources cleaned up and recreated")
        except Exception as e:
            self.logger.error(f"Error recreating ZeroMQ resources: {e}")
            self.context = None
            self.command_socket = None
            self.status_socket = None
