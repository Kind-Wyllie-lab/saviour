"""
What each TTL pin is used for, and what a TTL module syncs with
(plans/ttl-applications-ephys-sync.md).

Each pin has a *use* (its ``role``), which follows from its mode: a sync line
(``pseudorandom``), an experiment marker (``experiment_marker``: active for
the whole recording), a protocol output (``scheduled``), plus the less common
start pulse, event input, regular pulses, clock and manual output. A sync
line can say which system it feeds (``ttl.pins.<gpio>.system``:
``open_ephys`` / ``neurologger``), plus ``channel`` (that system's input it is
wired to); every pin has a free ``label``.

The module's *application* (``ephys_sync`` / ``neurologger_sync`` /
``mixed``) is worked out from its sync line's system; rig check, the session
record and the New Session "External sync" choice use it. Older configs set
``ttl.application`` directly and a pin ``role``: a sync line without a
system still takes its system from such an application.

The definitions live in ``ttl_applications.json`` next to this file, which the
frontend imports too. Pure: stdlib only, so the module, the controller and
tools can all use it.
"""

from __future__ import annotations

import json
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "ttl_applications.json")

with open(_PATH, encoding="utf-8") as _f:
    DEFINITIONS: dict = json.load(_f)

APPLICATIONS: dict = DEFINITIONS["applications"]
ROLES: dict = DEFINITIONS["roles"]
SYNC_SYSTEMS: dict = DEFINITIONS["sync_systems"]
USES: list = DEFINITIONS["uses"]
DEFAULT_APPLICATION: str = DEFINITIONS["default_application"]
CONFIRM_MAX_AGE_DAYS: float = float(DEFINITIONS["confirm_max_age_days"])
# What a session can be synchronised with ("External sync" on New Session):
# {kind: {label, application, system, description}}.
EXTERNAL_SYNC: dict = DEFINITIONS.get("external_sync", {})
_ROLE_OF_MODE: dict = {m: r for r, spec in ROLES.items() for m in spec["modes"]}


def external_sync_kind(value: object = None, legacy_ephys: bool = False) -> str | None:
    """A session's external sync kind (``"ephys"``, ``"neurologger"``) or
    None. ``legacy_ephys``: the older boolean ``ephys`` flag. Raises
    ValueError for an unknown kind."""
    if value in (None, "", "none", False):
        return "ephys" if legacy_ephys else None
    if value not in EXTERNAL_SYNC:
        raise ValueError(f"external_sync must be one of: none, {', '.join(EXTERNAL_SYNC)}")
    return str(value)


def sync_application(kind: str | None) -> str | None:
    """The TTL application that drives external sync ``kind``."""
    return (EXTERNAL_SYNC.get(kind) or {}).get("application") if kind else None


def pin_role(pin_config: dict | None) -> str:
    """A pin's use, from its mode (``custom`` for an unknown mode)."""
    mode = (pin_config or {}).get("mode")
    return _ROLE_OF_MODE.get(mode if mode is not None else "None", "custom")


def _sorted_pins(ttl_config: dict | None) -> list:
    return sorted(((ttl_config or {}).get("pins") or {}).items(),
                  key=lambda kv: int(kv[0]))


def pin_system(pin_config: dict | None, ttl_config: dict | None = None) -> str | None:
    """The system a sync line feeds (a ``SYNC_SYSTEMS`` key) or None. A sync
    line without one takes it from an older config's ``ttl.application``."""
    if pin_role(pin_config) != "sync":
        return None
    pin = pin_config or {}
    if "system" in pin:   # "" = none; an unknown name is reported by validate()
        return pin["system"] if pin["system"] in SYNC_SYSTEMS else None
    legacy = (ttl_config or {}).get("application")
    return (APPLICATIONS.get(legacy) or {}).get("system")


def application_of(ttl_config: dict | None) -> str:
    """The module's application, from its sync line's system."""
    for _gpio, pin in _sorted_pins(ttl_config):
        system = pin_system(pin, ttl_config)
        if system:
            return SYNC_SYSTEMS[system]["application"]
    return DEFAULT_APPLICATION


def module_application(module: dict | None) -> str | None:
    """A controller-side module dict's application, or None when it isn't a
    TTL module."""
    if not module or module.get("type") != "ttl":
        return None
    return application_of((module.get("config") or {}).get("ttl"))


def label(application: str) -> str:
    return (APPLICATIONS.get(application) or {}).get("label", application)


def validate(ttl_config: dict | None) -> list[str]:
    """Problems with the pins' uses, as sentences. Empty when fine."""
    problems = []
    counts: dict[str, int] = {}
    for gpio, pin in _sorted_pins(ttl_config):
        role = pin_role(pin)
        counts[role] = counts.get(role, 0) + 1
        system = (pin or {}).get("system")
        if role == "sync" and system and system not in SYNC_SYSTEMS:
            problems.append(f"GPIO {gpio}: unknown sync system '{system}'")
    for role, n in counts.items():
        cap = (ROLES.get(role) or {}).get("max")
        if cap and n > cap:
            problems.append(f"{n} {ROLES[role]['label'].lower()}s; at most {cap}")
    return problems


def lines(ttl_config: dict | None, role: str | None = None) -> list[dict]:
    """Every pin with a known use, as ``{gpio, role, mode, channel, label,
    system}``, in GPIO order (optionally only one use)."""
    out = []
    for gpio, pin in _sorted_pins(ttl_config):
        r = pin_role(pin)
        if r == "custom" or (role and r != role):
            continue
        out.append({"gpio": int(gpio), "role": r, "mode": (pin or {}).get("mode"),
                    "channel": (pin or {}).get("channel"),
                    "label": (pin or {}).get("label") or "",
                    "system": pin_system(pin, ttl_config)})
    return out


def sync_line(ttl_config: dict | None) -> dict | None:
    found = lines(ttl_config, "sync")
    return found[0] if found else None


def wiring_key(line: dict) -> str:
    """What a "seen on the acquisition system" confirmation is tied to:
    any change to the pin, its mode or its channel invalidates it."""
    return f"{line['gpio']}:{line['mode']}:{line.get('channel')}"
