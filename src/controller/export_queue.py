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

Queue state is persisted to QUEUE_FILE so in-progress and pending exports
survive a controller restart.  On startup, any entries that were mid-flight
(active) when the controller went down are re-queued from attempt 1; pending
entries keep their attempt count.

Configurable via controller config key:
    export.max_concurrent_exports  (default: 2)

Author: Andrew SG
"""

import json
import logging
import os
import threading


QUEUE_FILE = "/var/lib/saviour/controller/export_queue.json"


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

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Load persisted queue and dispatch any waiting entries.

        Call after self.facade has been set (i.e. from controller.start()).
        """
        with self._lock:
            self._load()
            if self._queue:
                self.logger.info(
                    f"Resuming {len(self._queue)} export(s) carried over from previous run"
                )
                self._dispatch_next()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def enqueue(self, module_id: str, export_path: str) -> None:
        """Add a module to the export queue and dispatch if capacity allows."""
        with self._lock:
            self._queue.append((module_id, export_path, 1))
            self.logger.info(
                f"Export queued for {module_id} → {export_path}. "
                f"Queue depth: {len(self._queue)}, active: {len(self._active)}"
            )
            self._dispatch_next()
            self._save()

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
            self._save()

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
            self._save()

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

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

    def _save(self) -> None:
        """Persist current queue state to disk atomically.

        Must be called while self._lock is held.  Active entries are saved
        alongside pending ones so they can be re-queued on the next startup
        if the controller goes down before their ack arrives.
        """
        try:
            data = {
                "queue": [
                    {"module_id": mid, "export_path": path, "attempt": attempt}
                    for mid, path, attempt in self._queue
                ],
                "active": [
                    {"module_id": mid, "export_path": path, "attempt": attempt}
                    for mid, (path, attempt) in self._active_meta.items()
                ],
            }
            os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
            tmp = QUEUE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, QUEUE_FILE)
        except Exception as e:
            self.logger.warning(f"Failed to persist export queue: {e}")

    def _load(self) -> None:
        """Restore queue from disk.

        Must be called while self._lock is held.  Active entries from the
        previous run are re-queued (attempt preserved) since we cannot know
        whether the module completed the export before the controller went down.
        """
        if not os.path.exists(QUEUE_FILE):
            return
        try:
            with open(QUEUE_FILE) as f:
                data = json.load(f)
            restored = 0
            for entry in data.get("queue", []):
                self._queue.append((
                    entry["module_id"], entry["export_path"], entry["attempt"]
                ))
                restored += 1
            for entry in data.get("active", []):
                self._queue.append((
                    entry["module_id"], entry["export_path"], entry["attempt"]
                ))
                restored += 1
            if restored:
                self.logger.info(
                    f"Restored {restored} export queue entr{'y' if restored == 1 else 'ies'} "
                    f"from {QUEUE_FILE}"
                )
        except Exception as e:
            self.logger.warning(f"Failed to load persisted export queue: {e}")
