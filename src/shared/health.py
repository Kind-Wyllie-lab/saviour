"""Canonical health snapshot schema shared between module (producer) and controller (consumer).

Adding a field here keeps both sides in sync automatically: the module's get_health()
and the controller's update_module_health() both reference this class, so a typo or
missing field is caught at import time rather than silently producing None in the UI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass
class ModuleHealthSnapshot:
    """Health metrics reported by a module in each heartbeat."""

    timestamp:       float          = 0.0
    cpu_temp:        float | None = None
    cpu_usage:       float | None = None
    memory_usage:    float | None = None
    memory_total_gb: float | None = None
    uptime:          float           = 0.0
    disk_space:      float | None = None
    disk_used_gb:    float | None = None
    disk_total_gb:   float | None = None
    ptp4l_offset_ns: float | None = None
    ptp4l_freq:      float | None = None
    phc2sys_offset_ns:  float | None = None
    phc2sys_freq:    float | None = None
    # Min/max over the last heartbeat_interval seconds (from the module's own
    # 1s-resolution PTP buffer), not just the single instantaneous sample
    # above -- so a transient spike that recovers between heartbeats is still
    # visible in fleet-level PTP history, regardless of heartbeat interval.
    ptp4l_offset_ns_min:   float | None = None
    ptp4l_offset_ns_max:   float | None = None
    phc2sys_offset_ns_min: float | None = None
    phc2sys_offset_ns_max: float | None = None
    recording:       bool            = False
    version:         str | None   = None

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
