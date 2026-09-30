"""
Tests for binding controller services to the LAN (eth0) only and for the
wlan0/WAN firewall status check (v1.0 roadmap B2).
"""

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.controller.communication import Communication
from src.controller.controller import resolve_listen_host
from src.controller.firewall_status import firewall_status, untrusted_interfaces

# ── resolve_listen_host ──────────────────────────────────────────────────────

@pytest.mark.parametrize(("listen_on", "expected"), [
    ("lan", "10.0.0.1"),
    (None, "10.0.0.1"),
    ("", "10.0.0.1"),
    ("all", "*"),
    ("0.0.0.0", "*"),
    ("*", "*"),
    ("192.168.1.5", "192.168.1.5"),
])
def test_resolve_listen_host(listen_on, expected):
    assert resolve_listen_host(listen_on, "10.0.0.1") == expected


def test_zmq_sockets_bind_to_the_given_host():
    with patch("src.controller.communication.zmq.Context") as ctx, \
         patch("src.controller.communication.threading.Thread"):
        sockets = [MagicMock(), MagicMock()]
        ctx.return_value.socket.side_effect = sockets
        Communication(bind_host="10.0.0.1")

    sockets[0].bind.assert_called_once_with("tcp://10.0.0.1:5555")
    sockets[1].bind.assert_called_once_with("tcp://10.0.0.1:5556")


def test_zmq_default_still_binds_every_interface():
    with patch("src.controller.communication.zmq.Context") as ctx, \
         patch("src.controller.communication.threading.Thread"):
        sockets = [MagicMock(), MagicMock()]
        ctx.return_value.socket.side_effect = sockets
        Communication()

    sockets[0].bind.assert_called_once_with("tcp://*:5555")


# ── firewall_status ──────────────────────────────────────────────────────────

_HOOKED_V4 = [
    "-P INPUT ACCEPT",
    "-N SAVIOUR-WLAN-IN",
    "-A INPUT -i wlan0 -j SAVIOUR-WLAN-IN",
    "-A SAVIOUR-WLAN-IN -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT",
    "-A SAVIOUR-WLAN-IN -j DROP",
]


def _runner(v4=None, v6=None, v6_fails=False):
    """Fake subprocess.run for `iptables -S` / `ip6tables -S`."""
    def run(cmd, **_kw):
        tool = cmd[0]
        if tool == "ip6tables" and v6_fails:
            return SimpleNamespace(returncode=3, stdout="", stderr="no ipv6")
        lines = v4 if tool == "iptables" else v6
        if lines is None:
            raise FileNotFoundError(tool)
        return SimpleNamespace(returncode=0, stdout="\n".join(lines) + "\n", stderr="")
    return run


def _no_config(tmp_path):
    return str(tmp_path / "missing_config")


def test_ok_when_no_untrusted_interface_exists(tmp_path):
    status = firewall_status(run=_runner(v4=[], v6=[]),
                             iface_exists=lambda i: False,
                             config_file=_no_config(tmp_path))
    assert status["ok"] is True


def test_ok_when_wlan0_is_hooked_on_both_families(tmp_path):
    v6 = list(_HOOKED_V4)
    status = firewall_status(run=_runner(v4=_HOOKED_V4, v6=v6),
                             iface_exists=lambda i: i == "wlan0",
                             config_file=_no_config(tmp_path))
    assert status["ok"] is True
    assert "wlan0" in status["detail"]


def test_exposed_when_wlan0_exists_but_nothing_is_hooked(tmp_path):
    bare = ["-P INPUT ACCEPT"]
    status = firewall_status(run=_runner(v4=bare, v6=bare),
                             iface_exists=lambda i: i == "wlan0",
                             config_file=_no_config(tmp_path))
    assert status["ok"] is False
    assert status["exposed"] == ["wlan0 (iptables)", "wlan0 (ip6tables)"]
    assert "saviour-config --apply-firewall" in status["detail"]


def test_exposed_when_ipv6_alone_is_missing(tmp_path):
    status = firewall_status(run=_runner(v4=_HOOKED_V4, v6=["-P INPUT ACCEPT"]),
                             iface_exists=lambda i: i == "wlan0",
                             config_file=_no_config(tmp_path))
    assert status["ok"] is False
    assert status["exposed"] == ["wlan0 (ip6tables)"]


def test_chain_without_final_drop_counts_as_exposed(tmp_path):
    no_drop = [line for line in _HOOKED_V4 if not line.endswith("-j DROP")]
    status = firewall_status(run=_runner(v4=no_drop, v6_fails=True),
                             iface_exists=lambda i: i == "wlan0",
                             config_file=_no_config(tmp_path))
    assert status["ok"] is False


def test_unusable_ip6tables_is_skipped_not_exposed(tmp_path):
    status = firewall_status(run=_runner(v4=_HOOKED_V4, v6_fails=True),
                             iface_exists=lambda i: i == "wlan0",
                             config_file=_no_config(tmp_path))
    assert status["ok"] is True


def test_unknown_when_no_iptables_at_all(tmp_path):
    def run(cmd, **_kw):
        raise subprocess.TimeoutExpired(cmd, 5)
    status = firewall_status(run=run, iface_exists=lambda i: True,
                             config_file=_no_config(tmp_path))
    assert status["ok"] is None


def test_wan_interface_from_config_is_also_checked(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("ROLE=controller\nWAN_INTERFACE=eth1\n", encoding="utf-8")
    assert untrusted_interfaces(str(cfg)) == ["wlan0", "eth1"]

    status = firewall_status(run=_runner(v4=_HOOKED_V4, v6_fails=True),
                             iface_exists=lambda i: i in ("wlan0", "eth1"),
                             config_file=str(cfg))
    assert status["ok"] is False
    assert status["exposed"] == ["eth1 (iptables)"]


@pytest.mark.parametrize("wan", ["eth0", "wlan0", "lo", ""])
def test_trusted_or_duplicate_wan_interface_is_ignored(tmp_path, wan):
    cfg = tmp_path / "config"
    cfg.write_text(f"WAN_INTERFACE={wan}\n", encoding="utf-8")
    assert untrusted_interfaces(str(cfg)) == ["wlan0"]
