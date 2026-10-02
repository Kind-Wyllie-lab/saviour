"""
Provisioning smoke tests for saviour-config (v1.0 roadmap C5).

saviour-config is a root-only whiptail TUI, so it can't be run whole here.
Instead this extracts the pure config-file functions (log,
read_config_value, write_config, write_provisioned_marker,
apply_from_config) into a harness that keeps the script's own
`set -uo pipefail`, points CONFIG_FILE / PROVISIONED_FILE / LOG at a temp
dir, and stubs run_configuration() with the same `set -e` bracket and final
write_config + write_provisioned_marker tail the real one has.

That's enough to catch the class of bug found 2026-09-30: under pipefail +
set -e a missing optional key (FIREWALL_WLAN_SSH) made read_config_value
return grep's exit 1 and aborted every boot-time --apply before config /
.provisioned were written, leaving saviour.service stopped.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "saviour-config"
FUNCS = ("log", "read_config_value", "write_config",
         "write_provisioned_marker", "apply_from_config")

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _extract(src: str, name: str) -> str:
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", src, re.S | re.M)
    assert m, f"{name}() not found in saviour-config"
    return m.group(0)


def _run(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    src = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "\nset -uo pipefail\n" in src, "harness assumes the script's shell options"
    funcs = "\n".join(_extract(src, f) for f in FUNCS)
    cfg, marker, log = (tmp_path / n for n in ("config", ".provisioned", "log"))
    harness = f"""set -uo pipefail
CONFIG_FILE='{cfg.as_posix()}'
PROVISIONED_FILE='{marker.as_posix()}'
LOG='{log.as_posix()}'
DEVICE_ROLE="" DEVICE_TYPE="" GATEWAY_MODE="" GATEWAY="" WAN_INTERFACE="" DEVICE_IP=""
{funcs}
run_configuration() {{
    set -e
    echo "RECONFIGURED $DEVICE_ROLE $DEVICE_TYPE"
    write_config
    write_provisioned_marker
    echo "COMPLETE"
    set +e
}}
{body}
"""
    script = tmp_path / "harness.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")
    return subprocess.run([BASH, script.as_posix()], capture_output=True,
                          text=True, timeout=30)


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


CONFIG = (
    "ROLE=module\nTYPE=camera\nGATEWAY_MODE=\nGATEWAY=\n"
    "WAN_INTERFACE=\nDEVICE_IP=\n"
)


def test_read_config_value_missing_key_is_not_an_error_under_set_e(tmp_path):
    _write(tmp_path / "config", CONFIG)
    r = _run(tmp_path, """set -e
v=$(read_config_value "$CONFIG_FILE" FIREWALL_WLAN_SSH)
echo "missing=[$v] type=[$(read_config_value "$CONFIG_FILE" TYPE)]"
""")
    assert r.returncode == 0, r.stderr
    assert "missing=[] type=[camera]" in r.stdout


def test_apply_reconfigures_when_declared_config_differs(tmp_path):
    """The failure mode from 2026-09-30: a hand-edited config with no
    .provisioned marker must run all the way through and write both files."""
    _write(tmp_path / "config", CONFIG)
    r = _run(tmp_path, "apply_from_config")
    assert r.returncode == 0, r.stderr
    assert "RECONFIGURED module camera" in r.stdout
    assert "COMPLETE" in r.stdout
    assert (tmp_path / ".provisioned").read_text() == CONFIG
    assert (tmp_path / "config").read_text() == CONFIG


def test_apply_is_a_noop_when_marker_matches(tmp_path):
    _write(tmp_path / "config", CONFIG)
    _write(tmp_path / ".provisioned", CONFIG)
    r = _run(tmp_path, "apply_from_config")
    assert r.returncode == 0, r.stderr
    assert "RECONFIGURED" not in r.stdout
    assert "nothing to do" in (tmp_path / "log").read_text()


def test_apply_after_type_change_reprovisions(tmp_path):
    _write(tmp_path / "config", CONFIG.replace("TYPE=camera", "TYPE=ttl"))
    _write(tmp_path / ".provisioned", CONFIG)
    r = _run(tmp_path, "apply_from_config")
    assert r.returncode == 0, r.stderr
    assert "RECONFIGURED module ttl" in r.stdout
    assert "TYPE=ttl" in (tmp_path / ".provisioned").read_text()


def test_write_config_preserves_hand_added_break_glass_key(tmp_path):
    _write(tmp_path / "config", CONFIG + "FIREWALL_WLAN_SSH=yes\n")
    r = _run(tmp_path, "apply_from_config")
    assert r.returncode == 0, r.stderr
    assert "FIREWALL_WLAN_SSH=yes" in (tmp_path / "config").read_text()


def test_apply_with_no_role_does_nothing(tmp_path):
    _write(tmp_path / "config", "ROLE=none\nTYPE=\n")
    r = _run(tmp_path, "apply_from_config")
    assert r.returncode == 0, r.stderr
    assert "RECONFIGURED" not in r.stdout
    assert not (tmp_path / ".provisioned").exists()


# --- PTP units: follow a grandmaster clock jump (2026-10-02) ---------------

def _ptp_function(name: str) -> str:
    src = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", src, re.S | re.M)
    assert m, f"{name}() not found in saviour-config"
    return m.group(0)


def test_module_ptp_units_step_on_large_offsets():
    """A fresh controller served its stale boot date, then NTP moved it a
    month forward; ptp4l/phc2sys only step on their first update by default,
    so every module slewed at the 6.4% limit with a month-wrong clock."""
    body = _ptp_function("configure_ptp_timereceiver")
    assert re.search(r"ExecStart=/usr/sbin/ptp4l .*--step_threshold=1\.0", body)
    assert re.search(r"ExecStart=/usr/sbin/phc2sys .* -S 1\.0", body)


def test_grandmaster_steps_phc_and_keeps_ntp_on():
    body = _ptp_function("configure_ptp_timetransmitter")
    assert re.search(r"ExecStart=/usr/sbin/phc2sys -a -r -r .* -S 1\.0", body)
    assert "timedatectl set-ntp true" in body


def test_mend_rewrites_units_missing_the_step_threshold():
    mend = (REPO / "mend.sh").read_text(encoding="utf-8")
    assert 'grep -q -- " -S 1.0" /etc/systemd/system/phc2sys.service' in mend


def test_service_unit_has_systemd_watchdog():
    """2026-10-02: a camera module froze with the GIL held and systemd, seeing
    a live process, never restarted it."""
    body = _ptp_function("configure_service")
    assert re.search(r"^WatchdogSec=\d+$", body, re.M)
    assert re.search(r"^NotifyAccess=main$", body, re.M)
    assert re.search(r"^Restart=always$", body, re.M)


# --- run_configuration ordering: a failed frontend build must not abort ----
# 2026-10-02: no Node.js on fresh controllers, so `npm install` failed under
# run_configuration's set -e -- before set_own_ip -- leaving the controller
# with no eth0 address, no DHCP, no .provisioned marker, and
# saviour-provision re-running (and failing) on every boot.

def _run_configuration(tmp_path: Path, build_ok: bool) -> subprocess.CompletedProcess:
    src = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    defined = set(re.findall(r"^([a-z_][a-z0-9_]*)\(\) \{", src, re.M))
    body = _extract(src, "run_configuration").replace(
        "/var/lib/saviour", (tmp_path / "varlib").as_posix())
    called = {w for w in re.findall(r"\b([a-z_][a-z0-9_]*)\b", body)
              if w in defined and w not in ("run_configuration", "log", "read_config_value")}
    stubs = "\n".join(f'{f}() {{ echo "CALLED {f}"; }}' for f in sorted(called))
    stubs += f"\nbuild_frontend() {{ echo 'CALLED build_frontend'; return {0 if build_ok else 1}; }}"
    cfg, marker, log = (tmp_path / n for n in ("config", ".provisioned", "log"))
    harness = f"""set -uo pipefail
CONFIG_FILE='{cfg.as_posix()}'
PROVISIONED_FILE='{marker.as_posix()}'
LOG='{log.as_posix()}'
DIR='{tmp_path.as_posix()}'
DEVICE_ROLE=controller DEVICE_TYPE=basic GATEWAY_MODE=none GATEWAY="" WAN_INTERFACE="" DEVICE_IP=10.0.0.1/16
{_extract(src, "log")}
{_extract(src, "read_config_value")}
{stubs}
systemctl() {{ echo "CALLED systemctl $*"; }}
{body}
run_configuration && echo "RUN_OK"
"""
    return subprocess.run([BASH, "-c", harness], capture_output=True, text=True,
                          timeout=60)


def test_failed_frontend_build_does_not_abort_controller_configuration(tmp_path):
    r = _run_configuration(tmp_path, build_ok=False)
    out = r.stdout
    assert "RUN_OK" in out, r.stdout + r.stderr
    for step in ("set_own_ip", "configure_dhcp_server", "configure_service",
                 "write_config", "write_provisioned_marker"):
        assert f"CALLED {step}" in out, step
    assert "CALLED warn_frontend_build_failed" in out
    # networking and the service unit come before the (optional) web UI build
    assert out.index("CALLED set_own_ip") < out.index("CALLED build_frontend")
    assert out.index("CALLED configure_service") < out.index("CALLED build_frontend")


def test_controller_ip_is_applied_even_without_a_role_change(tmp_path):
    """A controller image re-run on a new network kept its old address."""
    # bytes, not write_text: on Windows that writes CRLF, and ROLE would read
    # back as "controller\r" (a spurious role change)
    (tmp_path / ".provisioned").write_bytes(b"ROLE=controller\nTYPE=basic\n")
    r = _run_configuration(tmp_path, build_ok=True)
    assert "RUN_OK" in r.stdout, r.stdout + r.stderr
    assert "CALLED set_own_ip" in r.stdout
    assert "CALLED configure_dhcp_server" in r.stdout
