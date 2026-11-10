"""Network overview: one read-only snapshot of the rig network for System → Network.

The controller is the rig's DHCP server (dnsmasq on eth0, written by
``saviour-config``), so its lease file lists **every** device that asked it
for an address, not just registered modules: unprovisioned or re-imaged Pis,
the user's PC, the NAS, a stray device on the PTP switch. This merges those
leases with the kernel neighbour (ARP) table, which also catches statically
addressed hosts, the registered modules, and the export share, then classifies
each host and attaches ping RTT (``PingMonitor``), module link counters
(``ModuleHealthSnapshot.link``) and heartbeat regularity.

Parsing and merging are pure functions over text/dicts so they test without a
controller; ``read_system()`` is the only part that touches the OS.
"""

from __future__ import annotations

import ipaddress
import itertools
import json
import re
import subprocess
import time
from collections.abc import Iterable, Mapping
from typing import Any

DNSMASQ_CONF = "/etc/dnsmasq.conf"
DEFAULT_LEASEFILE = "/var/lib/misc/dnsmasq.leases"
RIG_IFACE = "eth0"
HEARTBEAT_WINDOW_S = 900

# Raspberry Pi Ltd / Raspberry Pi Trading MAC prefixes (IEEE MA-L).
RPI_OUIS = frozenset({
    "b8:27:eb", "dc:a6:32", "e4:5f:01", "d8:3a:dd", "28:cd:c1", "2c:cf:67",
    "88:a2:9e",
})
SEEN_NEIGH_STATES = frozenset({"REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT"})

# Host kinds, in display order.
KINDS = ("module", "share", "saviour_unregistered", "unprovisioned",
         "raspberry_pi", "other")

_MODULE_HOSTNAME_RE = re.compile(r"-module-[0-9a-f]{4}$|^saviour-", re.I)


# --------------------------------------------------------------------------- #
# Parsers                                                                     #
# --------------------------------------------------------------------------- #

def parse_leases(text: str) -> list[dict]:
    """dnsmasq lease file → [{expiry, mac, ip, hostname, client_id}].

    Line format: ``<expiry epoch|0> <mac> <ip> <hostname|*> <client-id|*>``;
    expiry 0 means an infinite lease. IPv6 ``duid`` lines are skipped.
    """
    out = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0] == "duid":
            continue
        try:
            expiry = int(parts[0])
            ipaddress.IPv4Address(parts[2])
        except ValueError:
            continue
        out.append({
            "expiry": expiry,
            "mac": parts[1].lower(),
            "ip": parts[2],
            "hostname": None if parts[3] == "*" else parts[3],
            "client_id": None if len(parts) < 5 or parts[4] == "*" else parts[4],
        })
    return out


def parse_dnsmasq_conf(text: str) -> dict:
    """The bits of dnsmasq.conf the page shows: interface, DHCP pool, lease
    time, the gateway it hands out (option 3), lease file path."""
    conf: dict[str, Any] = {"interface": None, "range_start": None, "range_end": None,
                            "netmask": None, "lease_time": None, "gateway": None,
                            "dns": None, "leasefile": None, "authoritative": False}
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key == "interface":
            conf["interface"] = val
        elif key == "dhcp-range":
            fields = [f.strip() for f in val.split(",")]
            # Optional leading tag/set: fields aren't addresses; drop them.
            while fields and not _is_ipv4(fields[0]):
                fields.pop(0)
            ips = [f for f in fields if _is_ipv4(f)]
            if ips:
                conf["range_start"] = ips[0]
            if len(ips) > 1:
                conf["range_end"] = ips[1]
            if len(ips) > 2:
                conf["netmask"] = ips[2]
            rest = [f for f in fields if not _is_ipv4(f)]
            if rest:
                conf["lease_time"] = rest[-1]
        elif key == "dhcp-option":
            opt, _, v = val.partition(",")
            opt = opt.replace("option:", "")
            if opt in ("3", "router"):
                conf["gateway"] = v or None
            elif opt in ("6", "dns-server"):
                conf["dns"] = v or None
        elif key == "dhcp-leasefile":
            conf["leasefile"] = val
        elif key == "dhcp-authoritative":
            conf["authoritative"] = True
    return conf


def parse_neigh(text: str) -> dict[str, dict]:
    """``ip neigh show dev <if>`` → {ip: {mac, state}} (IPv4 only)."""
    out = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if not parts or not _is_ipv4(parts[0]):
            continue
        mac = parts[parts.index("lladdr") + 1].lower() if "lladdr" in parts else None
        out[parts[0]] = {"mac": mac, "state": parts[-1].upper()}
    return out


# Tailscale's CGNAT range. The tailnet is the owner's private machines, so it
# is kept out of this payload entirely (never reaches the browser).
_TAILNET = ipaddress.IPv4Network("100.64.0.0/10")


def _is_tailnet(iface: dict) -> bool:
    name = iface.get("ifname") or ""
    if name.startswith(("tailscale", "ts")):
        return True
    return any(a.get("family") == "inet" and _is_ipv4(a.get("local", ""))
               and ipaddress.IPv4Address(a["local"]) in _TAILNET
               for a in iface.get("addr_info", []))


def parse_ip_addr(data: list[dict]) -> list[dict]:
    """``ip -j addr show`` → [{name, operstate, mac, ipv4: ["a.b.c.d/nn"]}].
    Drops lo and any tailnet interface (see _TAILNET)."""
    out = []
    for iface in data or []:
        name = iface.get("ifname")
        if not name or name == "lo" or _is_tailnet(iface):
            continue
        out.append({
            "name": name,
            "operstate": iface.get("operstate"),
            "mac": iface.get("address"),
            "ipv4": [f"{a['local']}/{a['prefixlen']}" for a in iface.get("addr_info", [])
                     if a.get("family") == "inet" and a.get("local")],
        })
    return out


def _is_ipv4(s: str) -> bool:
    try:
        ipaddress.IPv4Address(s)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Classification / merge                                                      #
# --------------------------------------------------------------------------- #

def mac_info(mac: str | None) -> dict:
    """Vendor hint from the MAC: Raspberry Pi by OUI; a locally administered
    address (2nd hex digit 2/6/A/E) is a randomised/private MAC, which is what
    phones and most laptops send on Wi-Fi and some on Ethernet."""
    if not mac or len(mac) < 8:
        return {"vendor": None, "randomised": False}
    mac = mac.lower()
    randomised = mac[1] in "26ae"
    vendor = "Raspberry Pi" if mac[:8] in RPI_OUIS else None
    return {"vendor": vendor, "randomised": randomised}


def heartbeat_stats(timestamps: Iterable[float], now: float,
                    window_s: float = HEARTBEAT_WINDOW_S) -> dict | None:
    """Mean / max / spread of the gaps between heartbeats in the window."""
    ts = sorted(t for t in timestamps if t is not None and t >= now - window_s)
    gaps = [b - a for a, b in itertools.pairwise(ts)]
    if not gaps:
        return None
    mean = sum(gaps) / len(gaps)
    sd = (sum((g - mean) ** 2 for g in gaps) / len(gaps)) ** 0.5
    return {"mean_gap_s": round(mean, 2), "max_gap_s": round(max(gaps), 2),
            "jitter_s": round(sd, 2), "beats": len(ts)}


def _ip_key(ip: str):
    try:
        return int(ipaddress.IPv4Address(ip))
    except ValueError:
        return 0


def merge_hosts(*, leases: list[dict], neigh: Mapping[str, dict],
                modules: Mapping[str, Mapping[str, Any]],
                module_health: Mapping[str, Mapping[str, Any]],
                module_history: Mapping[str, Iterable[Mapping[str, Any]]],
                pings: Mapping[str, dict], controller_ip: str | None,
                share_ip: str | None, network: ipaddress.IPv4Network | None,
                now: float) -> list[dict]:
    """One row per IP on the rig network (controller excluded), classified.

    ``modules``: id → {name, type, ip, online, status, version}.
    """
    hosts: dict[str, dict] = {}

    def row(ip: str) -> dict:
        if ip not in hosts:
            hosts[ip] = {"ip": ip, "mac": None, "hostname": None, "lease": None,
                         "neigh_state": None, "module": None}
        return hosts[ip]

    def on_rig(ip: str | None) -> bool:
        if not ip or ip == controller_ip or not _is_ipv4(ip):
            return False
        return network is None or ipaddress.IPv4Address(ip) in network

    for lease in leases:
        if not on_rig(lease["ip"]):
            continue
        r = row(lease["ip"])
        # Two leases for one IP: keep the later-expiring (0 = infinite wins).
        prev = r["lease"]
        if prev is None or lease["expiry"] == 0 or (
                prev["expiry"] != 0 and lease["expiry"] > prev["expiry"]):
            r["mac"] = lease["mac"]
            r["hostname"] = lease["hostname"]
            r["lease"] = {
                "expiry": lease["expiry"],
                "state": ("infinite" if lease["expiry"] == 0
                          else "active" if lease["expiry"] > now else "expired"),
            }

    for ip, n in neigh.items():
        if not on_rig(ip):
            continue
        if n["state"] not in SEEN_NEIGH_STATES and ip not in hosts:
            continue  # a FAILED probe to an address nobody holds
        r = row(ip)
        r["neigh_state"] = n["state"]
        r["mac"] = r["mac"] or n.get("mac")

    for mid, m in modules.items():
        ip = m.get("ip")
        if not on_rig(ip):
            continue
        r = row(ip)
        health = module_health.get(mid, {}) or {}
        link = health.get("link") or None
        r["module"] = {
            "id": mid, "name": m.get("name") or mid, "type": m.get("type"),
            "online": bool(m.get("online")), "status": m.get("status"),
            "version": m.get("version"), "link": link,
            "last_heartbeat": health.get("last_heartbeat"),
            "heartbeat": heartbeat_stats(
                (s.get("timestamp") for s in module_history.get(mid, ())), now),
        }
        if link and link.get("mac"):
            r["mac"] = r["mac"] or link["mac"].lower()
            if r["mac"] != link["mac"].lower():
                r["module"]["mac_mismatch"] = link["mac"].lower()

    out = []
    for ip, r in hosts.items():
        info = mac_info(r["mac"])
        hn = (r["hostname"] or "").lower()
        if r["module"]:
            kind = "module"
        elif share_ip and ip == share_ip:
            kind = "share"
        elif hn.startswith("saviour-unprov"):
            kind = "unprovisioned"
        elif _MODULE_HOSTNAME_RE.search(hn):
            kind = "saviour_unregistered"
        elif info["vendor"] == "Raspberry Pi":
            kind = "raspberry_pi"
        else:
            kind = "other"
        ping = pings.get(ip)
        seen = (r["neigh_state"] in SEEN_NEIGH_STATES
                or bool(ping and ping.get("last_ms") is not None))
        out.append({**r, **info, "kind": kind, "ping": ping, "reachable": seen})

    out.sort(key=lambda h: (KINDS.index(h["kind"]), _ip_key(h["ip"])))
    return out


def dhcp_pool(conf: Mapping[str, Any], leases: list[dict], now: float) -> dict | None:
    start, end = conf.get("range_start"), conf.get("range_end")
    if not (start and end):
        return None
    a, b = _ip_key(start), _ip_key(end)
    size = b - a + 1
    active = {lease["ip"] for lease in leases
              if a <= _ip_key(lease["ip"]) <= b
              and (lease["expiry"] == 0 or lease["expiry"] > now)}
    return {"start": start, "end": end, "size": size, "in_use": len(active),
            "lease_time": conf.get("lease_time"), "netmask": conf.get("netmask"),
            "gateway": conf.get("gateway"), "dns": conf.get("dns"),
            "authoritative": conf.get("authoritative", False)}


def ping_targets(leases: list[dict], neigh: Mapping[str, dict],
                 module_ips: Iterable[str | None], share_ip: str | None,
                 controller_ip: str | None,
                 network: ipaddress.IPv4Network | None) -> set[str]:
    ips = {lease["ip"] for lease in leases}
    ips |= {ip for ip, n in neigh.items() if n["state"] in SEEN_NEIGH_STATES}
    ips |= {ip for ip in module_ips if ip}
    if share_ip:
        ips.add(share_ip)
    return {ip for ip in ips if _is_ipv4(ip) and ip != controller_ip
            and (network is None or ipaddress.IPv4Address(ip) in network)}


def rig_network(interfaces: list[dict], iface: str = RIG_IFACE
                ) -> tuple[str | None, ipaddress.IPv4Network | None]:
    for i in interfaces:
        if i["name"] == iface and i["ipv4"]:
            ifc = ipaddress.IPv4Interface(i["ipv4"][0])
            return str(ifc.ip), ifc.network
    return None, None


# --------------------------------------------------------------------------- #
# OS reads                                                                    #
# --------------------------------------------------------------------------- #

def _read_text(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _run(args: list[str], run=subprocess.run) -> str:
    try:
        res = run(args, capture_output=True, text=True, timeout=5)
        return res.stdout if res.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def read_system(run=subprocess.run, conf_path: str = DNSMASQ_CONF,
                lease_path: str | None = None) -> dict:
    """Everything this page reads from the OS, as raw-ish data. Cheap: two
    file reads and three ``ip`` calls (no root needed)."""
    conf = parse_dnsmasq_conf(_read_text(conf_path))
    leasefile = lease_path or conf.get("leasefile") or DEFAULT_LEASEFILE
    iface = conf.get("interface") or RIG_IFACE
    try:
        addr = json.loads(_run(["ip", "-j", "addr", "show"], run) or "[]")
    except ValueError:
        addr = []
    try:
        routes = json.loads(_run(["ip", "-j", "route", "show", "default"], run) or "[]")
    except ValueError:
        routes = []
    return {
        "conf": conf,
        "conf_found": bool(conf.get("range_start") or conf.get("interface")),
        "leases": parse_leases(_read_text(leasefile)),
        "neigh": parse_neigh(_run(["ip", "neigh", "show", "dev", iface], run)),
        "interfaces": parse_ip_addr(addr),
        "default_routes": [{"dev": r.get("dev"), "gateway": r.get("gateway")}
                           for r in routes],
        "rig_iface": iface,
        "dhcp_release_available": dhcp_release_available(),
    }


# --------------------------------------------------------------------------- #
# Releasing a stale lease                                                     #
# --------------------------------------------------------------------------- #

class LeaseReleaseError(ValueError):
    """A release refused before anything was sent; the message is for the user."""


def dhcp_release_available(which=None) -> bool:
    import shutil
    return (which or shutil.which)("dhcp_release") is not None


def release_lease(ip: str, mac: str, *, system: Mapping[str, Any],
                  protected_ips: Iterable[str], pinger, reread,
                  run=subprocess.run, which=None, settle_s: float = 1.0,
                  sleep=time.sleep) -> dict:
    """Drop one DHCP lease via ``dhcp_release`` (dnsmasq-utils).

    dnsmasq keeps leases in memory and rewrites the file itself, so editing the
    lease file under it doesn't stick; ``dhcp_release`` sends a DHCPRELEASE on
    the client's behalf and dnsmasq removes the lease and rewrites the file.

    Refuses unless the lease is current for exactly this ip+mac, the address
    isn't a registered module or the export share (``protected_ips``), and the
    host doesn't answer ping or ARP right now: releasing an address a device is
    still using would let dnsmasq hand it to a second device. A device that
    comes back later just asks for a new lease.

    ``reread()`` returns fresh ``read_system()`` output, to confirm the lease
    is gone. Returns {"ok", "ip", "mac", "hostname", "detail"}.
    """
    mac = (mac or "").lower()
    if not _is_ipv4(ip or ""):
        raise LeaseReleaseError(f"Not an IPv4 address: {ip!r}")
    if not dhcp_release_available(which):
        raise LeaseReleaseError(
            "dhcp_release isn't installed on the controller "
            "(sudo apt install dnsmasq-utils, or run mend.sh)")
    lease = next((lease for lease in system.get("leases", [])
                  if lease["ip"] == ip and lease["mac"] == mac), None)
    if lease is None:
        raise LeaseReleaseError(f"No lease for {ip} / {mac} (already gone?)")
    if ip in set(protected_ips):
        raise LeaseReleaseError(
            f"{ip} belongs to a registered module or the export share")
    neigh = system.get("neigh", {}).get(ip)
    if neigh and neigh.get("state") in ("REACHABLE", "DELAY", "PROBE", "PERMANENT"):
        raise LeaseReleaseError(
            f"{ip} is still on the network (ARP {neigh['state'].lower()}). "
            "Unplug or power off the device first, or it will just renew.")
    if any(pinger(ip) is not None for _ in range(2)):
        raise LeaseReleaseError(
            f"{ip} answers ping. Unplug or power off the device first, "
            "or it will just renew.")

    iface = system.get("rig_iface") or RIG_IFACE
    args = ["dhcp_release", iface, ip, mac]
    if lease.get("client_id"):
        args.append(lease["client_id"])
    try:
        res = run(args, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "ip": ip, "mac": mac, "hostname": lease["hostname"],
                "detail": f"dhcp_release failed to run: {e}"}
    if res.returncode != 0:
        return {"ok": False, "ip": ip, "mac": mac, "hostname": lease["hostname"],
                "detail": f"dhcp_release exited {res.returncode}: "
                          f"{(res.stderr or res.stdout).strip()[:200]}"}
    sleep(settle_s)  # dnsmasq rewrites the lease file asynchronously
    still = any(lease["ip"] == ip and lease["mac"] == mac
                for lease in reread().get("leases", []))
    return {"ok": not still, "ip": ip, "mac": mac, "hostname": lease["hostname"],
            "detail": ("Lease released" if not still else
                       "dhcp_release ran but dnsmasq still lists the lease "
                       "(client-id mismatch?)")}


def build_overview(*, system: Mapping[str, Any], modules: Mapping[str, Mapping[str, Any]],
                   module_health: Mapping[str, Mapping[str, Any]],
                   module_history: Mapping[str, Iterable[Mapping[str, Any]]],
                   pings: Mapping[str, dict], share: Mapping[str, Any],
                   controller_link: dict | None, firewall: dict | None,
                   now: float | None = None) -> dict:
    now = time.time() if now is None else now
    interfaces = system.get("interfaces", [])
    rig_iface = system.get("rig_iface", RIG_IFACE)
    controller_ip, network = rig_network(interfaces, rig_iface)
    share_ip = share.get("ip") if share.get("kind") == "nas" else None
    hosts = merge_hosts(
        leases=system.get("leases", []), neigh=system.get("neigh", {}),
        modules=modules, module_health=module_health,
        module_history=module_history, pings=pings,
        controller_ip=controller_ip, share_ip=share_ip, network=network, now=now)
    counts = {k: 0 for k in KINDS}
    for h in hosts:
        counts[h["kind"]] += 1
    default_routes = system.get("default_routes", [])
    for i in interfaces:
        i["default_route"] = next(
            (r["gateway"] for r in default_routes if r["dev"] == i["name"]), None)
        i["role"] = ("rig" if i["name"] == rig_iface
                     else "uplink" if i["default_route"] else None)
    return {
        "generated_at": now,
        "controller": {
            "ip": controller_ip,
            "network": str(network) if network else None,
            "iface": rig_iface,
            "link": controller_link,
        },
        "interfaces": interfaces,
        "dhcp": dhcp_pool(system.get("conf", {}), system.get("leases", []), now),
        "dhcp_conf_found": system.get("conf_found", False),
        "share": dict(share),
        "firewall": firewall,
        "hosts": hosts,
        "counts": counts,
        "dhcp_release_available": system.get("dhcp_release_available", False),
    }
