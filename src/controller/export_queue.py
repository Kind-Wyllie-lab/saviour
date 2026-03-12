#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Export Queue

Manages controller-led, staggered file export across modules.

When a module finishes a recording segment it signals export_ready to the
controller rather than exporting immediately.  This queue receives those
signals, holds pending exports, and dispatches start_export commands to
modules one at a time (up to max_concurrent at once), waiting for an
export_complete or export_failed acknowledgement before sending the next.

Configurable via controller config key:
    export.max_concurrent_exports  (default: 2)

Author: Andrew SG
"""

import logging
import threading


class ExportQueue:
    def __init__(self, config):
        self.logger = logging.getLogger(__name__)
        self.config = config
        self._lock = threading.Lock()
        self._queue = []          # list of (module_id, export_path)
        self._active = set()      # module_ids currently exporting

    @property
    def _max_concurrent(self) -> int:
        return self.config.get("export.max_concurrent_exports", 2)

    def enqueue(self, module_id: str, export_path: str) -> None:
        """Add a module to the export queue and dispatch if capacity allows."""
        with self._lock:
            self._queue.append((module_id, export_path))
            self.logger.info(
                f"Export queued for {module_id} → {export_path}. "
                f"Queue depth: {len(self._queue)}, active: {len(self._active)}"
            )
            self._dispatch_next()

    def on_export_complete(self, module_id: str) -> None:
        """Call when a module reports export_complete."""
        with self._lock:
            self._active.discard(module_id)
            self.logger.info(
                f"Export complete for {module_id}. "
                f"Queue depth: {len(self._queue)}, active: {len(self._active)}"
            )
            self._dispatch_next()

    def on_export_failed(self, module_id: str) -> None:
        """Call when a module reports export_failed."""
        with self._lock:
            self._active.discard(module_id)
            self.logger.warning(
                f"Export failed for {module_id}. "
                f"Queue depth: {len(self._queue)}, active: {len(self._active)}"
            )
            self._dispatch_next()

    def _dispatch_next(self) -> None:
        """Dispatch as many queued exports as concurrency allows.

        Must be called while self._lock is held.
        """
        while self._queue and len(self._active) < self._max_concurrent:
            module_id, export_path = self._queue.pop(0)
            self._active.add(module_id)
            self.logger.info(
                f"Dispatching start_export to {module_id} → {export_path}"
            )
            self.facade.send_command(
                module_id, "start_export", {"export_path": export_path}
            )
