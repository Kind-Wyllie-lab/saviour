import ipaddress
from unittest.mock import MagicMock

from src.controller import network_overview as no
from src.controller.ping_monitor import PingMonitor, _thin, ping_once
from src.shared.netlink import LinkStats, read_link

NOW = 1_800_000_000

LEASES = f"""\
{NOW + 3600} 2c:cf:67:23:47:03 10.0.0.156 microphone-module-4703 01:2c:cf:67:23:47:03
{NOW + 3600} 2c:cf:67:aa:bb:cc 10.0.0.160 saviour-unprov-bbcc *
{NOW + 3600} d8:3a:dd:11:22:33 10.0.0.161 raspberrypi *
{NOW + 3600} 58:ef:68:7e:e3:11 10.0.0.155 MVM-BIOQ-3051 01:58:ef:68:7e:e3:11
{NOW - 60} 2c:cf:67:1a:29:04 10.0.0.190 apa-arduino-module-2904 *
{NOW + 3600} 9a:00:00:00:00:01 10.0.0.170 * *
0 aa:bb:cc:dd:ee:ff 10.0.0.171 printer *
duid 00:01:00:01:aa
garbage line
"""

CONF = """\
# dnsmasq configuration for SAVIOUR local network
interface=eth0
bind-interfaces
dhcp-range=10.0.0.128,10.0.0.255,255.255.0.0,12h
dhcp-option=3,10.0.0.1
dhcp-leasefile=/var/lib/misc/dnsmasq.leases
dhcp-authoritative
"""

NEIGH = """\
10.0.0.199 lladdr 98:fe:54:25:0f:cc REACHABLE
10.0.0.156 lladdr 2c:cf:67:23:47:03 DELAY
10.0.0.155 lladdr 58:ef:68:7e:e3:11 REACHABLE
10.0.0.20 lladdr 00:11:32:00:00:01 STALE
10.0.0.212 FAILED
169.254.29.221 lladdr 58:ef:68:7e:e3:11 STALE
fe80::1 lladdr 58:ef:68:7e:e3:11 STALE
"""

NET = ipaddress.IPv4Network("10.0.0.0/16")


class TestParsers:
    def test_leases(self):
        leases = no.parse_leases(LEASES)
        assert len(leases) == 7
        mic = leases[0]
        assert mic == {"expiry": NOW + 3600, "mac": "2c:cf:67:23:47:03",
                       "ip": "10.0.0.156", "hostname": "microphone-module-4703",
                       "client_id": "01:2c:cf:67:23:47:03"}
        assert leases[5]["hostname"] is None
        assert leases[6]["expiry"] == 0

    def test_conf(self):
        conf = no.parse_dnsmasq_conf(CONF)
        assert conf["interface"] == "eth0"
        assert (conf["range_start"], conf["range_end"]) == ("10.0.0.128", "10.0.0.255")
        assert conf["netmask"] == "255.255.0.0"
        assert conf["lease_time"] == "12h"
        assert conf["gateway"] == "10.0.0.1"
        assert conf["authoritative"] is True

    def test_conf_tagged_range_and_named_option(self):
        conf = no.parse_dnsmasq_conf(
            "dhcp-range=set:lan,192.168.1.50,192.168.1.99,1h\n"
            "dhcp-option=option:router,192.168.1.1\n")
        assert conf["range_start"] == "192.168.1.50"
        assert conf["lease_time"] == "1h"
        assert conf["gateway"] == "192.168.1.1"

    def test_neigh_ipv4_only(self):
        neigh = no.parse_neigh(NEIGH)
        assert neigh["10.0.0.212"] == {"mac": None, "state": "FAILED"}
        assert neigh["10.0.0.199"]["mac"] == "98:fe:54:25:0f:cc"
        assert "fe80::1" not in neigh

    def test_ip_addr(self):
        data = [
            {"ifname": "lo", "addr_info": []},
            {"ifname": "eth0", "operstate": "UP", "address": "2c:cf:67:59:d3:c0",
             "addr_info": [{"family": "inet", "local": "10.0.0.1", "prefixlen": 16},
                           {"family": "inet6", "local": "fe80::1", "prefixlen": 64}]},
        ]
        assert no.parse_ip_addr(data) == [{
            "name": "eth0", "operstate": "UP", "mac": "2c:cf:67:59:d3:c0",
            "ipv4": ["10.0.0.1/16"]}]

    def test_ip_addr_drops_tailnet(self):
        data = [
            {"ifname": "tailscale0", "addr_info": [
                {"family": "inet", "local": "100.114.1.2", "prefixlen": 32}]},
            {"ifname": "wg0", "addr_info": [  # tailnet address on another name
                {"family": "inet", "local": "100.80.0.9", "prefixlen": 32}]},
            {"ifname": "wlan0", "addr_info": [
                {"family": "inet", "local": "10.126.18.7", "prefixlen": 16}]},
        ]
        assert [i["name"] for i in no.parse_ip_addr(data)] == ["wlan0"]


class TestMerge:
    def _hosts(self, **kw):
        args = dict(
            leases=no.parse_leases(LEASES), neigh=no.parse_neigh(NEIGH),
            modules={
                "microphone_4703": {"name": "Mic", "type": "microphone",
                                    "ip": "10.0.0.156", "online": True},
                "habitat_camera_0fcc": {"name": "Cam", "type": "habitat_camera",
                                        "ip": "10.0.0.199", "online": True},
                "rfid_10ad": {"name": "rfid_10ad", "type": "rfid",
                              "ip": "10.0.0.212", "online": False},
            },
            module_health={"microphone_4703": {
                "last_heartbeat": NOW - 5,
                "link": {"mac": "2C:CF:67:23:47:03", "speed_mbps": 100}}},
            module_history={"microphone_4703": [{"timestamp": NOW - 90},
                                                {"timestamp": NOW - 60},
                                                {"timestamp": NOW - 29}]},
            pings={"10.0.0.155": {"last_ms": 1.2}, "10.0.0.212": {"last_ms": None}},
            controller_ip="10.0.0.1", share_ip="10.0.0.20", network=NET, now=NOW)
        args.update(kw)
        return {h["ip"]: h for h in no.merge_hosts(**args)}

    def test_every_lease_and_neighbour_is_a_host(self):
        hosts = self._hosts()
        # Leases, the static NAS (ARP only) and all modules; link-local dropped.
        assert set(hosts) == {"10.0.0.155", "10.0.0.156", "10.0.0.160", "10.0.0.161",
                              "10.0.0.170", "10.0.0.171", "10.0.0.190", "10.0.0.199",
                              "10.0.0.20", "10.0.0.212"}

    def test_classification(self):
        kinds = {ip: h["kind"] for ip, h in self._hosts().items()}
        assert kinds["10.0.0.156"] == "module"
        assert kinds["10.0.0.199"] == "module"           # no lease, ARP only
        assert kinds["10.0.0.212"] == "module"           # offline module
        assert kinds["10.0.0.20"] == "share"
        assert kinds["10.0.0.160"] == "unprovisioned"
        assert kinds["10.0.0.190"] == "saviour_unregistered"
        assert kinds["10.0.0.161"] == "raspberry_pi"
        assert kinds["10.0.0.155"] == "other"
        assert kinds["10.0.0.170"] == "other"

    def test_lease_states_and_flags(self):
        hosts = self._hosts()
        assert hosts["10.0.0.190"]["lease"]["state"] == "expired"
        assert hosts["10.0.0.171"]["lease"]["state"] == "infinite"
        assert hosts["10.0.0.199"]["lease"] is None
        assert hosts["10.0.0.170"]["randomised"] is True
        assert hosts["10.0.0.161"]["vendor"] == "Raspberry Pi"

    def test_reachability(self):
        hosts = self._hosts()
        assert hosts["10.0.0.155"]["reachable"] is True
        assert hosts["10.0.0.212"]["reachable"] is False
        assert hosts["10.0.0.160"]["reachable"] is False  # lease, no ARP, no ping

    def test_module_detail(self):
        mic = self._hosts()["10.0.0.156"]["module"]
        assert mic["link"]["speed_mbps"] == 100
        assert mic["heartbeat"]["beats"] == 3
        assert mic["heartbeat"]["max_gap_s"] == 31
        assert "mac_mismatch" not in mic

    def test_controller_and_off_subnet_excluded(self):
        hosts = self._hosts(modules={"x": {"ip": "10.0.0.1"},
                                     "y": {"ip": "192.168.5.5"}})
        assert "10.0.0.1" not in hosts and "192.168.5.5" not in hosts

    def test_sorted_by_kind_then_ip(self):
        order = [h["kind"] for h in no.merge_hosts(
            leases=no.parse_leases(LEASES), neigh={}, modules={}, module_health={},
            module_history={}, pings={}, controller_ip="10.0.0.1", share_ip=None,
            network=NET, now=NOW)]
        assert order == sorted(order, key=no.KINDS.index)


def test_dhcp_pool_counts_only_live_leases_in_range():
    pool = no.dhcp_pool(no.parse_dnsmasq_conf(CONF), no.parse_leases(LEASES), NOW)
    assert pool["size"] == 128
    assert pool["in_use"] == 6  # 7 leases, one expired


def test_ping_targets():
    t = no.ping_targets(no.parse_leases(LEASES), no.parse_neigh(NEIGH),
                        ["10.0.0.212", None, "10.0.0.1"], "10.0.0.20", "10.0.0.1", NET)
    assert "10.0.0.1" not in t
    assert "169.254.29.221" not in t
    assert {"10.0.0.212", "10.0.0.20", "10.0.0.199", "10.0.0.190"} <= t


def test_heartbeat_stats_window():
    assert no.heartbeat_stats([NOW - 2000, NOW - 30], NOW) is None
    s = no.heartbeat_stats([NOW - 60, NOW - 30, NOW], NOW)
    assert s == {"mean_gap_s": 30.0, "max_gap_s": 30.0, "jitter_s": 0.0, "beats": 3}


def test_read_system_survives_missing_everything(tmp_path):
    run = MagicMock(side_effect=OSError("no ip"))
    out = no.read_system(run=run, conf_path=str(tmp_path / "nope.conf"),
                         lease_path=str(tmp_path / "nope.leases"))
    assert out["leases"] == [] and out["interfaces"] == [] and out["neigh"] == {}
    assert out["conf_found"] is False


def test_build_overview_marks_interface_roles():
    system = {
        "conf": no.parse_dnsmasq_conf(CONF), "conf_found": True,
        "leases": no.parse_leases(LEASES), "neigh": {},
        "interfaces": [
            {"name": "eth0", "operstate": "UP", "mac": "x", "ipv4": ["10.0.0.1/16"]},
            {"name": "wlan0", "operstate": "UP", "mac": "y", "ipv4": ["10.126.18.7/16"]},
        ],
        "default_routes": [{"dev": "wlan0", "gateway": "10.126.255.254"}],
        "rig_iface": "eth0",
    }
    out = no.build_overview(system=system, modules={}, module_health={},
                            module_history={}, pings={},
                            share={"kind": "controller", "ip": "10.0.0.1"},
                            controller_link=None, firewall=None, now=NOW)
    roles = {i["name"]: i["role"] for i in out["interfaces"]}
    assert roles == {"eth0": "rig", "wlan0": "uplink"}
    assert out["controller"]["network"] == "10.0.0.0/16"
    assert out["counts"]["unprovisioned"] == 1
    assert sum(out["counts"].values()) == len(out["hosts"])


# --------------------------------------------------------------------------- #
# PingMonitor                                                                 #
# --------------------------------------------------------------------------- #

def test_ping_once_parses_rtt_and_failures():
    ok = MagicMock(return_value=MagicMock(
        returncode=0, stdout="64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0.231 ms"))
    assert ping_once("10.0.0.2", run=ok) == 0.231
    bad = MagicMock(return_value=MagicMock(returncode=1, stdout=""))
    assert ping_once("10.0.0.2", run=bad) is None
    assert ping_once("10.0.0.2", run=MagicMock(side_effect=OSError)) is None


def test_ping_monitor_stats_and_pruning():
    clock = [1000.0]
    replies = {"a": 1.0, "b": None}
    targets = ["a", "b"]
    mon = PingMonitor(lambda: targets, pinger=lambda ip: replies[ip],
                      clock=lambda: clock[0], history_s=100)
    for _ in range(4):
        mon.round()
        clock[0] += 10
    replies["a"] = None
    mon.round()
    snap = mon.snapshot(window_s=1000)
    assert snap["a"]["loss_pct"] == 20.0
    assert snap["a"]["last_ms"] is None
    assert snap["a"]["last_reply_at"] == 1030.0
    assert snap["b"]["loss_pct"] == 100.0 and snap["b"]["median_ms"] is None
    # A target dropped from the list ages out of history.
    targets[:] = ["a"]
    clock[0] += 200
    mon.round()
    assert "b" not in mon.snapshot()


def test_thin_keeps_loss_visible():
    h = [(float(t), 1.0) for t in range(100)]
    h[50] = (50.0, None)
    thinned = _thin(h, 10)
    assert len(thinned) == 10
    assert any(r is None for _, r in thinned)


# --------------------------------------------------------------------------- #
# netlink                                                                     #
# --------------------------------------------------------------------------- #

def _fake_iface(root, **vals):
    d = root / "eth0"
    (d / "statistics").mkdir(parents=True)
    base = {"address": "2c:cf:67:00:00:01", "operstate": "up", "speed": "1000",
            "duplex": "full", "carrier_changes": "2", "mtu": "1500"}
    stats = {"rx_errors": "0", "tx_errors": "0", "rx_dropped": "5",
             "tx_dropped": "0", "rx_bytes": "1000", "tx_bytes": "2000"}
    for k, v in {**base, **vals}.items():
        if k in stats:
            stats[k] = v
        else:
            (d / k).write_text(v + "\n")
    for k, v in {**stats, **{k: v for k, v in vals.items() if k in stats}}.items():
        (d / "statistics" / k).write_text(v + "\n")
    return d


def test_read_link(tmp_path):
    _fake_iface(tmp_path, speed="-1")
    link = read_link("eth0", str(tmp_path))
    assert link["speed_mbps"] is None  # -1 = no carrier
    assert link["rx_dropped"] == 5 and link["duplex"] == "full"
    assert read_link("wlan9", str(tmp_path)) is None


def test_link_stats_window_and_reset(tmp_path):
    d = _fake_iface(tmp_path)
    t = [0.0]
    ls = LinkStats("eth0", str(tmp_path), window_s=100, clock=lambda: t[0])
    assert ls.sample()["rx_dropped_1h"] == 0
    t[0] = 50
    (d / "statistics" / "rx_dropped").write_text("9\n")
    s = ls.sample()
    assert s["rx_dropped_1h"] == 4 and s["window_s"] == 50
    # Counter went backwards (driver reset): window restarts, no negative delta.
    t[0] = 60
    (d / "statistics" / "rx_dropped").write_text("1\n")
    s = ls.sample()
    assert s["rx_dropped_1h"] == 0 and s["window_s"] == 0
