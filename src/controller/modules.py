#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Module State Manager

Responsible for tracking the state of all connected SAVIOUR modules, including
their online/recording status and configuration sync state.

Config sync model:
  Each module has a `true_config` (last confirmed state reported by the module)
  and a `target_config` (what the controller intends the module to be configured
  as). A `ConfigSyncStatus` tracks whether these are in agreement:

    UNKNOWN  - No config received from module yet
    SYNCED   - true_config matches target_config (or no target has been set)
    PENDING  - A set_config command has been sent; awaiting confirmation
    FAILED   - The module confirmed a config that does not match target_config

Author: Andrew SG
"""

import logging
import time
import threading
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Dict, Any, List, Optional, Tuple

from src.controller.models import Module, ModuleStatus


# ---------------------------------------------------------------------------
# Config sync types
# ---------------------------------------------------------------------------

class ConfigSyncStatus(StrEnum):
    UNKNOWN = "UNKNOWN"   # No config received yet
    SYNCED  = "SYNCED"    # true_config == target_config (or no target set)
    PENDING = "PENDING"   # set_config sent, awaiting module confirmation
    FAILED  = "FAILED"    # Module confirmed a config that differs from target


@dataclass
class ModuleConfigState:
    """Holds both sides of a module's config plus the current sync status."""
    true_config:   Dict[str, Any] = field(default_factory=dict)
    target_config: Dict[str, Any] = field(default_factory=dict)
    status:        ConfigSyncStatus = ConfigSyncStatus.UNKNOWN
    pending_since: float = 0.0
    diffs:         List[Tuple] = field(default_factory=list)  # [(path, true_val, target_val)]


# ---------------------------------------------------------------------------
# Modules
# ---------------------------------------------------------------------------

class Modules:
    """
    Single source of truth for all connected module state, including config.

    Replaces the previous split between `Modules` and `ModuleConfigs`.
    """

    # How long a module stays in READY/NOT_READY before reverting to DEFAULT
    READY_TIMEOUT_SECS = 120

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # Core module registry
        self._modules: Dict[str, Module] = {}

        # Config state registry – keyed by module_id
        self._config_states: Dict[str, ModuleConfigState] = {}

        # Background thread: revert READY/NOT_READY modules that have timed out
        self._ready_timeout_thread = threading.Thread(
            target=self._ready_timeout_checker,
            daemon=True,
            name="ready-timeout-checker",
        )

        # Set by controller after construction
        self.facade = None


    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self) -> None:
        self.logger.info("Starting Modules manager")
        self._ready_timeout_thread.start()


    # -----------------------------------------------------------------------
    # Module registry – add / remove / update identity
    # -----------------------------------------------------------------------

    def add_module(self, module: Module) -> None:
        self._modules[module.id] = module
        if module.id not in self._config_states:
            self._config_states[module.id] = ModuleConfigState()
        self.logger.info(f"Module registered: {module.id} (name={module.name})")


    def remove_module(self, module_id: str) -> None:
        self._modules.pop(module_id, None)
        self._config_states.pop(module_id, None)
        self.broadcast_updated_modules()


    def module_discovery(self, module: Module) -> None:
        """Called by Network when zeroconf reports a new or updated module."""
        self.logger.info(f"Adding new module {module.id}")
        action = "Updating" if module.id in self._modules else "Adding new"
        self.logger.info(f"{action} module {module.id}")
        self.add_module(module)
        self.broadcast_updated_modules()


    def module_rediscovered(self, module_id: str) -> None:
        """Called when zeroconf re-discovers a module previously marked offline."""
        if module_id not in self._modules:
            self.logger.warning(f"Rediscovery notice for unknown module {module_id} – ignoring")
            return
        self.logger.info(f"Module {module_id} rediscovered – marking online")
        self._modules[module_id].online = True
        self._modules[module_id].status = ModuleStatus.DEFAULT
        self.broadcast_updated_modules()


    def module_id_changed(self, old_id: str, new_id: str) -> None:
        if old_id in self._modules:
            self._modules[new_id] = self._modules.pop(old_id)
        if old_id in self._config_states:
            self._config_states[new_id] = self._config_states.pop(old_id)
        self.broadcast_updated_modules()


    def module_ip_changed(self, module_id: str, new_ip: str) -> None:
        if module_id not in self._modules:
            self.logger.warning(f"module_ip_changed called for unknown module {module_id}")
            return
        self._modules[module_id].ip = new_ip
        self.broadcast_updated_modules()


    # -----------------------------------------------------------------------
    # Status / health notifications
    # -----------------------------------------------------------------------

    def check_status(self, module_id: str, status_data: dict) -> None:
        """Reconcile recording state from a heartbeat payload."""
        module = self._modules.get(module_id)
        if module is None:
            return

        is_recording = status_data.get("recording")
        if is_recording is True and module.status != ModuleStatus.RECORDING:
            module.status = ModuleStatus.RECORDING
        elif is_recording is False and module.status == ModuleStatus.RECORDING:
            module.status = ModuleStatus.DEFAULT


    def notify_module_online_update(self, module_id: str, online: bool) -> None:
        module = self._modules.get(module_id)
        if module is None:
            return
        changed = module.online != online
        module.online = online
        if not online:
            module.status = ModuleStatus.OFFLINE
        elif changed:
            module.status = ModuleStatus.DEFAULT
        self.broadcast_updated_modules()


    def notify_module_readiness_update(self, module_id: str, ready: bool, message: str) -> None:
        module = self._modules.get(module_id)
        if module is None:
            return
        module.ready_message = message
        if ready:
            if module.status != ModuleStatus.RECORDING:
                module.status = ModuleStatus.READY
                module.ready_time = time.time()
        else:
            module.status = ModuleStatus.NOT_READY
        self.broadcast_updated_modules()


    def notify_recording_started(self, module_id: str, data: dict) -> None:
        if not data.get("recording"):
            self.logger.warning(
                f"recording_started received from {module_id} but recording=False – ignoring"
            )
            return
        if module_id in self._modules:
            self._modules[module_id].status = ModuleStatus.RECORDING
        self.broadcast_updated_modules()


    def notify_recording_stopped(self, module_id: str, data: dict) -> None:
        if module_id in self._modules:
            self._modules[module_id].status = ModuleStatus.DEFAULT
        self.broadcast_updated_modules()


    # -----------------------------------------------------------------------
    # Config management – the heart of the new design
    # -----------------------------------------------------------------------

    def received_module_config(self, module_id: str, config: dict) -> None:
        """
        Called when the module responds to a `get_config` or echoes config back
        after a successful `set_config`.

        Updates `true_config`, compares against `target_config`, and resolves
        the sync status accordingly.
        """
        state = self._get_or_create_config_state(module_id)
        state.true_config = config

        # Also keep Module.config in sync for convenience (e.g. serialisation)
        if module_id in self._modules:
            self._modules[module_id].config = config
            self._update_module_name(module_id)

        # Resolve pending status if we were waiting for confirmation
        if state.target_config:
            public_true = self._filter_private_keys(config)
            diffs = self._diff_dicts(public_true, state.target_config)
            state.diffs = diffs
            if diffs:
                self.logger.warning(
                    f"Config mismatch for {module_id} after update: {diffs}"
                )
                state.status = ConfigSyncStatus.FAILED
            else:
                state.status = ConfigSyncStatus.SYNCED
        else:
            # No target set yet – treat initial fetch as synced baseline
            state.target_config = config
            state.status = ConfigSyncStatus.SYNCED

        self.broadcast_updated_modules()


    def set_target_module_config(self, module_id: str, config: dict) -> None:
        """
        Called when the frontend submits a new config for a module (before the
        command is sent to the module). Records intent and marks status PENDING.
        """
        state = self._get_or_create_config_state(module_id)

        # Warn if submitted config is missing keys relative to current true config
        if state.true_config:
            missing = set(state.true_config.keys()) - set(config.keys())
            if missing:
                self.logger.warning(
                    f"set_target_module_config for {module_id}: "
                    f"submitted config missing top-level keys: {missing}"
                )

        state.target_config = config
        state.status = ConfigSyncStatus.PENDING
        state.pending_since = time.time()
        self.broadcast_updated_modules()


    def handle_set_config_failed(self, module_id: str, reason: str = "") -> None:
        """Called when the module explicitly rejects a set_config command."""
        state = self._config_states.get(module_id)
        if state:
            state.status = ConfigSyncStatus.FAILED
            self.logger.error(
                f"set_config failed for {module_id}: {reason or 'unknown reason'}"
            )
        self.broadcast_updated_modules()


    def get_config_sync_status(self, module_id: str) -> ConfigSyncStatus:
        state = self._config_states.get(module_id)
        return state.status if state else ConfigSyncStatus.UNKNOWN


    # -----------------------------------------------------------------------
    # Getters
    # -----------------------------------------------------------------------

    def get_modules(self) -> Dict[str, Any]:
        """Return all modules serialised to dicts, ready for the frontend."""
        return self._serialise_modules()

    def get_module_configs(self) -> Dict[str, Any]:
        """Return config state for all modules, keyed by module_id."""
        result = {}
        for module_id, state in self._config_states.items():
            result[module_id] = {
                'true_config': state.true_config,
                'target_config': state.target_config,
                'status': state.status.value,
                'diffs': state.diffs,
            }
        return result


    def get_modules_by_target(self, target: str) -> Dict[str, Any]:
        """
        Resolve a target string to a dict of modules.
        Target may be "all", a group name, or a specific module_id.
        """
        if not target:
            self.logger.error("get_modules_by_target called with empty target")
            return {}

        if target.lower() == "all":
            return self.get_modules()

        result = {}
        for module_id, module in self._modules.items():
            if module_id == target:
                return {module_id: asdict(module)}
            if module.group == target:
                result[module_id] = asdict(module)
        return result


    def is_module_recording(self, module_id: str) -> bool:
        module = self._modules.get(module_id)
        return module is not None and module.status == ModuleStatus.RECORDING


    # -----------------------------------------------------------------------
    # Broadcast
    # -----------------------------------------------------------------------

    def broadcast_updated_modules(self) -> None:
        """Push current module state (including config sync status) to the frontend."""
        if self.facade:
            self.facade.push_module_update_to_frontend(self._serialise_modules())


    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _get_or_create_config_state(self, module_id: str) -> ModuleConfigState:
        if module_id not in self._config_states:
            self._config_states[module_id] = ModuleConfigState()
        return self._config_states[module_id]


    def _update_module_name(self, module_id: str) -> None:
        """Sync Module.name from the 'module.name' key in its config."""
        config = self._modules[module_id].config
        name = config.get("module", {}).get("name", "").strip()
        self._modules[module_id].name = name if name else module_id


    def _serialise_modules(self) -> Dict[str, Dict[str, Any]]:
        """
        Convert _modules to plain dicts, augmenting each with config sync state
        so the frontend has everything it needs in one payload.
        """
        result = {}
        for module_id, module in self._modules.items():
            m = asdict(module)
            # StrEnum serialises fine, but be explicit for clarity
            m["status"] = module.status.value

            # Attach config sync state
            state = self._config_states.get(module_id)
            if state:
                m["config_sync_status"] = state.status.value
                m["config_diffs"] = state.diffs
            else:
                m["config_sync_status"] = ConfigSyncStatus.UNKNOWN.value
                m["config_diffs"] = []

            result[module_id] = m
        return result


    def _ready_timeout_checker(self) -> None:
        """Background thread: revert READY/NOT_READY modules that have timed out."""
        while True:
            now = time.time()
            for module_id, module in list(self._modules.items()):
                if module.status in (ModuleStatus.READY, ModuleStatus.NOT_READY):
                    if now - module.ready_time > self.READY_TIMEOUT_SECS:
                        self.logger.info(
                            f"{module_id} timed out from {module.status} – reverting to DEFAULT"
                        )
                        module.status = ModuleStatus.DEFAULT
                        self.broadcast_updated_modules()
            time.sleep(5)


    @staticmethod
    def _diff_dicts(a: dict, b: dict, path: str = "") -> List[Tuple]:
        """
        Recursively diff two dicts. Returns a list of (path, value_in_a, value_in_b)
        tuples for every key that differs.
        """
        diffs = []
        for key in set(a) | set(b):
            new_path = f"{path}.{key}" if path else key
            if key not in a:
                diffs.append((new_path, None, b[key]))
            elif key not in b:
                diffs.append((new_path, a[key], None))
            else:
                val_a, val_b = a[key], b[key]
                if isinstance(val_a, dict) and isinstance(val_b, dict):
                    diffs.extend(Modules._diff_dicts(val_a, val_b, new_path))
                elif val_a != val_b:
                    diffs.append((new_path, val_a, val_b))
        return diffs

    @staticmethod
    def _filter_private_keys(obj: dict) -> dict:
        if not isinstance(obj, dict):
            return obj
        filtered = {}
        for k, v in obj.items():
            if k.startswith('_'):
                continue
            filtered_v = Modules._filter_private_keys(v) if isinstance(v, dict) else v
            # Drop keys whose entire value was private children
            if isinstance(filtered_v, dict) and len(filtered_v) == 0:
                continue
            filtered[k] = filtered_v
        return filtered