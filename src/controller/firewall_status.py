"""
Is the controller's wlan0/WAN firewall actually in effect?

saviour-config's configure_firewall() hooks a default-deny SAVIOUR-WLAN-IN
chain onto INPUT for each untrusted interface (wlan0, plus WAN_INTERFACE
when it isn't eth0), for iptables and ip6tables. Nothing at runtime used to
confirm that had happened -- a controller on eduroam whose rules were never
applied (or were lost) looked exactly like a protected one. This checks the
live rules so the controller can log/alert at startup and report it in
controller health (System page).

Only interfaces that exist are checked; with none present (the usual
single-homed controller) the result is ok. A family whose tool is missing
or unusable is reported as unknown rather than exposed, mirroring
configure_firewall(), which skips such a family.
"""

import os
import subprocess
from collections.abc import Callable

CHAIN = "SAVIOUR-WLAN-IN"
CONFIG_FILE = "/etc/saviour/config"
_TRUSTED = {"eth0", "wlan0", "lo", ""}


def untrusted_interfaces(config_file: str = CONFIG_FILE) -> list[str]:
    """wlan0 always, plus WAN_INTERFACE when set to something that isn't
    eth0/wlan0/lo -- same rule as saviour-config's _fw_untrusted_interfaces."""
    ifaces = ["wlan0"]
    try:
        with open(config_file, encoding="utf-8") as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                if key == "WAN_INTERFACE" and value.strip() not in _TRUSTED:
                    ifaces.append(value.strip())
    except OSError:
        pass
    return ifaces


def _rules(tool: str, run: Callable) -> list[str] | None:
    """`<tool> -S` output lines, or None if the tool is missing/unusable."""
    try:
        out = run([tool, "-S"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.splitlines()


def firewall_status(run: Callable = subprocess.run,
                    iface_exists: Callable[[str], bool] | None = None,
                    config_file: str = CONFIG_FILE) -> dict:
    """{"ok": True | False | None, "detail": str, "exposed": [...]}.

    ok=False means an untrusted interface exists and at least one usable
    family has no SAVIOUR-WLAN-IN hook (or the chain has no final DROP).
    ok=None means it couldn't be determined (no usable iptables at all).
    """
    if iface_exists is None:
        def iface_exists(name: str) -> bool:
            return os.path.exists(f"/sys/class/net/{name}")

    present = [i for i in untrusted_interfaces(config_file) if iface_exists(i)]
    if not present:
        return {"ok": True, "detail": "No untrusted interface present", "exposed": []}

    exposed, checked = [], 0
    for tool in ("iptables", "ip6tables"):
        rules = _rules(tool, run)
        if rules is None:
            continue
        checked += 1
        has_drop = f"-A {CHAIN} -j DROP" in rules
        for iface in present:
            hooked = f"-A INPUT -i {iface} -j {CHAIN}" in rules
            if not (hooked and has_drop):
                exposed.append(f"{iface} ({tool})")

    if checked == 0:
        return {"ok": None, "detail": "Could not read firewall rules", "exposed": []}
    if exposed:
        return {
            "ok": False,
            "detail": ("Firewall not in effect on " + ", ".join(exposed)
                       + " - run: sudo saviour-config --apply-firewall"),
            "exposed": exposed,
        }
    return {"ok": True, "detail": "Firewall active on " + ", ".join(present),
            "exposed": []}
