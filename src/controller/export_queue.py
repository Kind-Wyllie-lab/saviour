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
    MAX_RETRIES = 3

    def __init__(self, config):
        self.logger = logging.getLogger(__name__)
        self.config = config
        self._lock = threading.Lock()
        self._queue = []          # list of (module_id, export_path, attempt)
        self._active = set()      # module_ids currently exporting
        # Tracks (export_path, attempt) for each active module so we can
        # re-enqueue on failure without the caller needing to pass it back.
        self._active_meta: dict = {}

    @property
    def _max_concurrent(self) -> int:
        return self.config.get("export.max_concurrent_exports", 2)

    def enqueue(self, module_id: str, export_path: str) -> None:
        """Add a module to the export queue and dispatch if capacity allows."""
        with self._lock:
            self._queue.append((module_id, export_path, 1))
            self.logger.info(
                f"Export queued for {module_id} → {export_path}. "
                f"Queue depth: {len(self._queue)}, active: {len(self._active)}"
            )
            self._dispatch_next()

    def on_export_complete(self, module_id: str) -> None:
        """Call when a module reports export_complete."""
        with self._lock:
            self._active.discard(module_id)
            self._active_meta.pop(module_id, None)
            self.logger.info(
                f"Export complete for {module_id}. "
                f"Queue depth: {len(self._queue)}, active: {len(self._active)}"
            )
            self._dispatch_next()

    def on_export_failed(self, module_id: str) -> None:
        """Call when a module reports export_failed.

        Re-enqueues the export up to MAX_RETRIES times before giving up,
        so a transient NAS outage does not silently lose data.
        """
        with self._lock:
            self._active.discard(module_id)
            meta = self._active_meta.pop(module_id, None)
            if meta:
                export_path, attempt = meta
                if attempt < self.MAX_RETRIES:
                    self.logger.warning(
                        f"Export failed for {module_id} (attempt {attempt}/{self.MAX_RETRIES}) "
                        f"— re-queuing. Queue depth: {len(self._queue)}"
                    )
                    self._queue.append((module_id, export_path, attempt + 1))
                else:
                    self.logger.error(
                        f"Export failed for {module_id} after {self.MAX_RETRIES} attempts "
                        f"— giving up on {export_path}"
                    )
            else:
                self.logger.warning(
                    f"Export failed for {module_id} (no retry metadata). "
                    f"Queue depth: {len(self._queue)}, active: {len(self._active)}"
                )
            self._dispatch_next()

    def _dispatch_next(self) -> None:
        """Dispatch as many queued exports as concurrency allows.

        Must be called while self._lock is held.
        """
        while self._queue and len(self._active) < self._max_concurrent:
            module_id, export_path, attempt = self._queue.pop(0)
            self._active.add(module_id)
            self._active_meta[module_id] = (export_path, attempt)
            self.logger.info(
                f"Dispatching start_export to {module_id} → {export_path} "
                f"(attempt {attempt}/{self.MAX_RETRIES})"
            )
            self.facade.send_command(
                module_id, "start_export", {"export_path": export_path}
            )
