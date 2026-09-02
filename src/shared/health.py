"""Canonical health snapshot schema shared between module (producer) and controller (consumer).

Adding a field here keeps both sides in sync automatically: the module's get_health()
and the controller's update_module_health() both reference this class, so a typo or
missing field is caught at import time rather than silently producing None in the UI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields

# Raspberry Pi `vcgencmd get_throttled` bitmask. Low nibble = happening right
# now; bits 16-19 = has happened at some point since boot (sticky).
_THROTTLE_BITS = {
    0: "under_voltage",
    1: "arm_freq_capped",
    2: "throttled",
    3: "soft_temp_limit",
}


def decode_throttled(value: int | None) -> dict:
    """Split a `get_throttled` bitmask into current vs since-boot flag lists.

    Returns {"now": [...], "since_boot": [...]}. `now` is what matters for an
    alert; `since_boot` is the forensic "did this device ever brown out / get
    hot" record that survives after the condition clears. An empty/zero value
    (or None) yields two empty lists.
    """
    if not value:
        return {"now": [], "since_boot": []}
    now = [name for bit, name in _THROTTLE_BITS.items() if value & (1 << bit)]
    since = [name for bit, name in _THROTTLE_BITS.items() if value & (1 << (bit + 16))]
    return {"now": now, "since_boot": since}


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
    # Raw `vcgencmd get_throttled` bitmask (int), or None if unreadable.
    # Decode with decode_throttled(). Lets the controller/UI flag a module
    # that is browning out or thermally throttling -- the usual root cause of
    # "it went weird then recovered" on a PoE-powered Pi 5.
    throttled:       int | None = None
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
    # Microphone only: rolling % of audio samples clipping (at/near full
    # scale) on the loudest AudioMoth, from the monitoring stream. A coarse
    # "gain too high" data-quality indicator; None for non-mic modules.
    audio_clip_pct:  float | None = None
    # Camera only: rolling % of the frame clipped to white or crushed to
    # black (whichever is worse), from the capture thread. A coarse
    # "exposure/gain is wrong" data-quality indicator; None for non-camera.
    frame_clip_pct:  float | None = None
    # Set by a module whose expected sensor/peripheral hardware wasn't found
    # or failed to initialise (e.g. no camera sensor on the CSI bus, no
    # AudioMoth enumerated) -- a human-readable reason, or None when the
    # module's hardware is present and healthy. The module still boots and
    # registers normally in this state; only recording/streaming that needs
    # the missing hardware is unavailable. Distinct from the module simply
    # never appearing at all (a genuine crash/power-off), which this field
    # can't help with -- see each module's hardware_fault attribute.
    hardware_fault:  str | None = None
    recording:       bool            = False
    # Measured recording output in bytes/second (None unless recording). A
    # reality check against the config-derived estimate in
    # src/shared/data_rate.py -- if they diverge a lot, the config is wrong
    # (or a camera scene is unusually busy / a mic is on FLAC).
    rec_bytes_per_s: float | None = None
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
