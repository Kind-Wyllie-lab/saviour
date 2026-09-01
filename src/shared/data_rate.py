"""Estimated recording data-rate per module, derived from config alone.

Modules stream to the export share and delete their local copy, so local free
space is only a risk when a module *generates* data faster than it can export
it (backlog grows until the local disk hits its floor). And the share itself
fills at the *sum* of every recording module's rate. Both questions need a
per-module MB/min figure that can be known before recording even starts —
which is what this computes from the synced config.

These are deliberately worst-case ceilings (camera: the H.264 target bitrate,
which a busy scene approaches; microphone: uncompressed PCM, though FLAC on
disk is smaller and content-dependent). Plan headroom against the ceiling.
"""

from __future__ import annotations

# Rough per-frame overhead of the timestamp CSV sidecar a camera writes
# alongside the video (one row per frame, ~120 bytes).
_CAMERA_CSV_BYTES_PER_FRAME = 120

# A small non-zero nominal for event-log module types (TTL/RFID/arduino):
# their CSV output is unbounded in principle but tiny in practice.
_EVENT_LOG_BYTES_PER_S = 512.0


def _cfg(config: dict, dotted: str, default=None):
    """Read a dotted key from either a nested dict or a flat dotted-key dict."""
    if dotted in config:
        return config[dotted]
    node = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def estimate_recording_bytes_per_s(
    module_type: str, config: dict
) -> tuple[float | None, str]:
    """Return (bytes_per_second, human_note) for one module's recording output.

    bytes_per_second is None when the type is unknown / not a recorder.
    """
    if not module_type:
        return None, "unknown module type"
    t = module_type.lower()
    config = config if isinstance(config, dict) else {}

    if "camera" in t:
        bitrate_mb = _cfg(config, "camera.bitrate_mb", 2) or 2
        fps = _cfg(config, "camera.fps", 30) or 30
        video = float(bitrate_mb) * 1_000_000 / 8
        csv = float(fps) * _CAMERA_CSV_BYTES_PER_FRAME
        note = f"H.264 ~{bitrate_mb} Mbit/s + timestamp CSV"
        if "habitat" in t:
            note += " (motion-triggered — actual is much lower)"
        return video + csv, note

    if "microphone" in t:
        sample_rate = _cfg(config, "audiomoth.sample_rate", 192000) or 192000
        labels = _cfg(config, "audiomoth_labels", {}) or {}
        n = max(1, len(labels)) if isinstance(labels, dict) else 1
        # PCM_16 = 2 bytes/sample, mono
        bytes_per_s = float(n) * float(sample_rate) * 2
        assumed = (" (assuming 1 AudioMoth — none configured)"
                   if n == 1 and not labels else "")
        note = (f"{n}× AudioMoth PCM {float(sample_rate) / 1000:.0f} kHz/16-bit"
                f"{assumed}; FLAC on disk is smaller")
        return bytes_per_s, note

    if t in ("ttl", "rfid", "apa_arduino", "arduino", "sound"):
        return _EVENT_LOG_BYTES_PER_S, "event log CSV — negligible"

    return None, f"no estimate for type '{module_type}'"


def bytes_per_s_to_mb_per_min(bps: float | None) -> float | None:
    if bps is None:
        return None
    return bps * 60 / 1_000_000


def runway_minutes(free_mb: float | None, mb_per_min: float | None) -> float | None:
    """How long `free_mb` lasts at `mb_per_min`. None if the rate is ~0 or
    inputs are missing (i.e. space is effectively not the constraint)."""
    if not free_mb or not mb_per_min or mb_per_min <= 0:
        return None
    return free_mb / mb_per_min
