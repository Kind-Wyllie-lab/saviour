"""saviour-status: log parsing and rendering from canned API data."""

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "saviour-status"


@pytest.fixture(scope="module")
def ss():
    loader = importlib.machinery.SourceFileLoader("saviour_status", str(_PATH))
    spec = importlib.util.spec_from_loader("saviour_status", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_parse_ptp4l_takes_last_offset_line(ss):
    lines = [
        "ptp4l[1.0]: master offset        -27 s2 freq  -61006 path delay      2735",
        "ptp4l[2.0]: master offset       -104 s1 freq  -61091 path delay      2741",
        "ptp4l[3.0]: port 1 (eth0): some other line",
    ]
    assert ss.parse_ptp4l(lines) == {
        "offset_ns": -104, "state": "s1", "freq": -61091, "path_delay_ns": 2741}
    assert ss.parse_ptp4l(["nothing here"]) is None


def test_parse_phc2sys(ss):
    line = "phc2sys[9.1]: CLOCK_REALTIME phc offset  -12 s2 freq  +5396 delay 512"
    assert ss.parse_phc2sys([line]) == {"offset_ns": -12, "state": "s2",
                                        "freq": 5396}
    assert ss.parse_phc2sys(["phc2sys[1]: failed to step clock"]) is None


def test_gm_role(ss):
    assert ss.gm_role(["... assuming the grand master role"]) == "grandmaster"
    assert ss.gm_role(["port 1: UNCALIBRATED to SLAVE on MASTER_CLOCK_SELECTED"]
                      ) == "slave"
    assert ss.gm_role([]) is None


def test_formatters(ss):
    assert ss.fmt_ns(None) == "-"
    assert ss.fmt_ns(131) == "131ns"
    assert ss.fmt_ns(-2500) == "-2.5µs"
    assert ss.fmt_duration(59) == "0m"
    assert ss.fmt_duration(3 * 3600 + 5 * 60) == "3h05m"
    assert ss.fmt_duration(2 * 86400 + 3600) == "2d1h"


def test_render_controller_flags_offline_and_version_skew(ss, monkeypatch):
    monkeypatch.setattr(ss, "service_states",
                        lambda names: {n: "active" for n in names})
    monkeypatch.setattr(ss, "journal_tail", lambda *a, **k: [])
    monkeypatch.setattr(ss, "interfaces", lambda: [("eth0", "10.0.0.1/16")])
    monkeypatch.setattr(ss, "read_leases", lambda: [
        {"ip": "10.0.0.20", "mac": "aa", "hostname": "cam", "expires": 0},
        {"ip": "10.0.0.99", "mac": "bb", "hostname": "laptop", "expires": 0}])
    monkeypatch.setattr(ss, "ping", lambda ip: True)
    data = {
        "state": {"version": "v1", "recording": False,
                  "ptp": {"worst_offset_ns": 100, "start_gate_ns": 50000,
                          "synced": True},
                  "sessions": {"total": 0}, "ui_clients": {}},
        "modules": {
            "camera_aaaa": {"online": True, "ip": "10.0.0.20", "type": "camera",
                            "name": "top", "status": "WAITING", "version": "v1"},
            "ttl_bbbb": {"online": False, "ip": "10.0.0.21", "type": "ttl",
                         "name": "", "status": "WAITING", "version": "v0"},
        },
        "health": {"camera_aaaa": {"ptp4l_offset_ns": 80, "cpu_usage": 10.0,
                                   "cpu_temp": 50.0, "disk_space": 20.0}},
    }
    text = "\n".join(ss.render_controller({"TYPE": "basic"}, data))
    cam = next(line for line in text.splitlines() if "camera_aaaa" in line)
    assert "idle" in cam and "80ns" in cam and "50°C" in cam
    ttl = next(line for line in text.splitlines() if "ttl_bbbb" in line)
    assert "offline" in ttl and "v0" in ttl
    # Leases that are modules aren't repeated; the rest are listed.
    assert "laptop" in text and "OTHER DHCP CLIENTS" in text
    assert text.count("10.0.0.20") == 1


def test_render_controller_without_api(ss, monkeypatch):
    monkeypatch.setattr(ss, "service_states", lambda names: {n: "failed"
                                                             for n in names})
    monkeypatch.setattr(ss, "journal_tail", lambda *a, **k: [])
    monkeypatch.setattr(ss, "interfaces", lambda: [])
    monkeypatch.setattr(ss, "read_leases", lambda: [])
    text = "\n".join(ss.render_controller(
        {"TYPE": "basic"}, {"api_error": "API unreachable"}))
    assert "API unreachable" in text and "failed" in text
