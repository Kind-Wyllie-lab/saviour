"""
What a TTL module is for, and what its pins do
(plans/ttl-applications-ephys-sync.md).

``ttl.application`` (``mixed`` / ``ephys_sync`` / ``neurologger_sync`` /
``stimulus_control``) says what a TTL module is used for; each pin can take a
``role`` (``sync``, ``experiment_start``, ``stimulus``, ``marker_in``) that
fixes which modes it may use, plus ``channel`` (the acquisition system's
digital input it is wired to) and a free ``label``. The definitions live in
``ttl_applications.json`` next to this file, which the frontend imports too.

Pure: stdlib only, so the module, the controller and tools can all use it.
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
DEFAULT_APPLICATION: str = DEFINITIONS["default_application"]
CONFIRM_MAX_AGE_DAYS: float = float(DEFINITIONS["confirm_max_age_days"])


def application_of(ttl_config: dict | None) -> str:
    """The module's application from its ``ttl`` config section (unknown or
    missing reads as the default)."""
    app = (ttl_config or {}).get("application") or DEFAULT_APPLICATION
    return app if app in APPLICATIONS else DEFAULT_APPLICATION


def module_application(module: dict | None) -> str | None:
    """A controller-side module dict's application, or None when it isn't a
    TTL module."""
    if not module or module.get("type") != "ttl":
        return None
    return application_of((module.get("config") or {}).get("ttl"))


def label(application: str) -> str:
    return (APPLICATIONS.get(application) or {}).get("label", application)


def pin_role(pin_config: dict | None) -> str:
    """A pin's role; pins without one (or with an unknown one) are
    ``custom``."""
    role = (pin_config or {}).get("role")
    return role if role in ROLES else "custom"


def validate(ttl_config: dict | None) -> list[str]:
    """Problems with the application / pin roles, as sentences. Empty when
    fine. ``mixed`` only checks roles that are set."""
    cfg = ttl_config or {}
    app = application_of(cfg)
    spec = APPLICATIONS[app]
    allowed = spec.get("roles")
    problems = []
    counts: dict[str, int] = {}
    for gpio, pin in sorted((cfg.get("pins") or {}).items(), key=lambda kv: int(kv[0])):
        role = (pin or {}).get("role")
        if not role:
            if allowed is not None:
                problems.append(f"GPIO {gpio} has no role ({label(app)} lines each need one)")
            continue
        if role not in ROLES:
            problems.append(f"GPIO {gpio}: unknown role '{role}'")
            continue
        if allowed is not None and role not in allowed:
            problems.append(f"GPIO {gpio}: {ROLES[role]['label']} isn't used by {label(app)}")
        mode = (pin or {}).get("mode")
        if mode not in ROLES[role]["modes"]:
            problems.append(
                f"GPIO {gpio}: a {ROLES[role]['label'].lower()} must be "
                f"{' or '.join(ROLES[role]['modes'])} (is {mode})")
        counts[role] = counts.get(role, 0) + 1
    for role, n in counts.items():
        cap = ROLES[role].get("max")
        if cap and n > cap:
            problems.append(f"{n} {ROLES[role]['label'].lower()}s; at most {cap}")
    for role in spec.get("requires") or []:
        if not counts.get(role):
            problems.append(f"{label(app)} needs a {ROLES[role]['label'].lower()}")
    return problems


def lines(ttl_config: dict | None, role: str | None = None) -> list[dict]:
    """Pins with a role, as ``{gpio, role, mode, channel, label}``, in GPIO
    order (optionally only one role)."""
    out = []
    for gpio, pin in sorted(((ttl_config or {}).get("pins") or {}).items(),
                            key=lambda kv: int(kv[0])):
        r = pin_role(pin)
        if r == "custom" or (role and r != role):
            continue
        out.append({"gpio": int(gpio), "role": r, "mode": (pin or {}).get("mode"),
                    "channel": (pin or {}).get("channel"),
                    "label": (pin or {}).get("label") or ""})
    return out


def sync_line(ttl_config: dict | None) -> dict | None:
    found = lines(ttl_config, "sync")
    return found[0] if found else None


def wiring_key(line: dict) -> str:
    """What a "seen on the acquisition system" confirmation is tied to:
    any change to the pin, its mode or its channel invalidates it."""
    return f"{line['gpio']}:{line['mode']}:{line.get('channel')}"
