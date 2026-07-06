#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Communication Manager

The communication manager is responsible for handling all ZMQ-based messaging between
the controller and modules, including:
- Command routing to modules (ROUTER socket — replaces PUB)
- Status/data subscription from modules
- Message routing and handling

Author: Andrew SG
Created: ?
"""

import zmq
import threading
import logging
import time
from typing import Callable, Dict, Any, Set
import json

class Communication:
    def __init__(self,
                 status_callback: Callable[[str, str], None] = None,
                 data_callback: Callable[[str, str], None] = None):
        """Initialize the communication manager"""
        self.logger = logging.getLogger(__name__)
        self.is_running = True
        self.status_callback = None
        self.data_callback = None

        # Tracks module IDs whose DEALER sockets have registered via "hello"
        self._connected_dealers: Set[str] = set()
        self._dealers_lock = threading.Lock()

        # ZeroMQ setup
        self.context = zmq.Context()

        # ROUTER for sending commands to modules.
        # ROUTER_HANDOVER=1 lets a reconnecting DEALER with the same identity
        # immediately take over without the old connection blocking it.
        self.command_socket = self.context.socket(zmq.ROUTER)
        self.command_socket.setsockopt(zmq.ROUTER_HANDOVER, 1)
        self.command_socket.bind("tcp://*:5555")

        # SUB for receiving status updates from modules
        self.status_socket = self.context.socket(zmq.SUB)
        self.status_socket.subscribe("status/")
        self.status_socket.subscribe("data/")
        self.status_socket.bind("tcp://*:5556")

        # Poller watches both sockets so we never block on one while the other has data
        self.poller = zmq.Poller()
        self.poller.register(self.status_socket, zmq.POLLIN)
        self.poller.register(self.command_socket, zmq.POLLIN)

        # Start the zmq listener thread
        self.listener_thread = threading.Thread(target=self.listen_for_updates, daemon=True)
        self.listener_thread.start()

        self.register_callbacks(status_callback, data_callback)

    def register_callbacks(self, status_callback: Callable[[str, str], None], data_callback: Callable[[str, str], None]):
        """Register callbacks for status and data updates"""
        self.status_callback = status_callback
        self.data_callback = data_callback

    def send_command(self, module_id: str, command: str, params: Dict) -> None:
        """Send a command to a specific module, or to all connected modules if module_id='all'."""
        if not params:
            params = {}
        payload = f"{command} {json.dumps(params)}"

        if module_id == "all":
            with self._dealers_lock:
                targets = list(self._connected_dealers)
            for target in targets:
                self._send_to_dealer(target, payload)
        else:
            self._send_to_dealer(module_id, payload)

    def _send_to_dealer(self, module_id: str, payload: str) -> None:
        """Send a payload string to a specific DEALER by identity."""
        with self._dealers_lock:
            if module_id not in self._connected_dealers:
                self.logger.warning(f"Cannot send to {module_id}: not in connected dealers")
                return
        try:
            self.command_socket.send_multipart([
                module_id.encode(),
                payload.encode(),
            ])
            self.logger.info(f"Command sent to {module_id}: {payload} at {time.time()}")
        except Exception as e:
            self.logger.error(f"Error sending command to {module_id}: {e}")

    def remove_dealer(self, module_id: str) -> None:
        """Remove a dealer from the connected set (called when a module goes offline)."""
        with self._dealers_lock:
            self._connected_dealers.discard(module_id)
        self.logger.info(f"Dealer removed: {module_id}")

    def listen_for_updates(self):
        """Listen for status updates from modules and registration hellos from dealers."""
        while self.is_running:
            try:
                socks = dict(self.poller.poll(timeout=100))

                if self.status_socket in socks:
                    message = self.status_socket.recv_string()
                    topic, data = message.split(' ', 1)
                    if topic.startswith('status/'):
                        self.handle_status_update(topic, data)
                    elif topic.startswith('data/'):
                        self.logger.info("Received a zmq data/ message")

                if self.command_socket in socks:
                    self._handle_dealer_message()

            except zmq.error.ContextTerminated:
                break
            except Exception as e:
                if self.is_running:
                    self.logger.error(f"Error handling update: {e}")

    def _handle_dealer_message(self):
        """Handle an incoming frame from a DEALER (module).

        ROUTER prepends the sender identity, so we receive [identity, payload].
        Currently the only message modules send is the 'hello' registration frame.
        """
        try:
            frames = self.command_socket.recv_multipart()
            if len(frames) < 2:
                self.logger.warning(f"Unexpected ROUTER frame count: {len(frames)}")
                return
            identity = frames[0].decode()
            payload = frames[1].decode()

            if payload == "hello":
                with self._dealers_lock:
                    self._connected_dealers.add(identity)
                self.logger.info(f"Dealer registered: {identity}")
            else:
                self.logger.debug(f"Unexpected message from dealer {identity}: {payload}")
        except Exception as e:
            self.logger.error(f"Error handling dealer message: {e}")

    def handle_status_update(self, topic: str, data: str):
        """Handle a status update from a module, and pass it to the callback"""
        if self.status_callback:
            self.status_callback(topic, data)

    def handle_data_update(self, topic: str, data: str):
        """Handle a data update from a module, and pass it to the callback"""
        if self.data_callback:
            self.data_callback(topic, data)

    def cleanup(self):
        """Clean up ZMQ connections"""
        self.logger.info("Cleaning up controller communication manager...")

        self.is_running = False

        if self.listener_thread and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=2)

        try:
            if hasattr(self, 'command_socket'):
                self.logger.info("Closing command socket")
                self.command_socket.setsockopt(zmq.LINGER, 1000)
                self.command_socket.close()
            if hasattr(self, 'status_socket'):
                self.logger.info("Closing status socket")
                self.status_socket.setsockopt(zmq.LINGER, 1000)
                self.status_socket.close()
            if hasattr(self, 'context'):
                self.logger.info("Terminating ZeroMQ context")
                self.context.term()
        except Exception as e:
            self.logger.error(f"Error during ZeroMQ cleanup: {e}")

        self.logger.info("Controller communication manager cleanup complete")
