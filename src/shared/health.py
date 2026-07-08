"""Canonical health snapshot schema shared between module (producer) and controller (consumer).

Adding a field here keeps both sides in sync automatically: the module's get_health()
and the controller's update_module_health() both reference this class, so a typo or
missing field is caught at import time rather than silently producing None in the UI.
"""

from __future__ import annotations
from dataclasses import dataclass, fields, asdict
from typing import Optional


@dataclass
class ModuleHealthSnapshot:
    """Health metrics reported by a module in each heartbeat."""

    timestamp:       float          = 0.0
    cpu_temp:        Optional[float] = None
    cpu_usage:       Optional[float] = None
    memory_usage:    Optional[float] = None
    memory_total_gb: Optional[float] = None
    uptime:          float           = 0.0
    disk_space:      Optional[float] = None
    disk_used_gb:    Optional[float] = None
    disk_total_gb:   Optional[float] = None
    ptp4l_offset_ns: Optional[float] = None
    ptp4l_freq:      Optional[float] = None
    phc2sys_offset_ns:  Optional[float] = None
    phc2sys_freq:    Optional[float] = None
    recording:       bool            = False
    version:         Optional[str]   = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def field_names(cls) -> frozenset:
        return frozenset(f.name for f in fields(cls))

    @classmethod
    def from_dict(cls, d: dict) -> ModuleHealthSnapshot:
        """Build a snapshot from an incoming dict, ignoring unknown keys."""
        known = cls.field_names()
        return cls(**{k: v for k, v in d.items() if k in known})
