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
