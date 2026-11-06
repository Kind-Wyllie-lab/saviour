"""
saviour-config's --prepare-module-type / --set-module-type (the module
command change_module_type), run in a bash harness like test_ssh_access.py:
the real functions against a temp variants tree and config file, with
`dpkg` and `apt-get` replaced by shims and apply_from_config stubbed.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "saviour-config"
FUNCS = ("log", "is_installed", "read_config_value", "read_variant_value",
         "_module_type_valid", "prepare_module_type", "set_module_type")
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None or sys.platform == "win32",
                                reason="needs bash")

SHIMS = {
    # Installed packages are the lines of $SHIM_LOG/installed.
    "dpkg": 'grep -qx "$2" "$SHIM_LOG/installed" 2>/dev/null\n',
    # apt-get install: fails while $SHIM_LOG/offline exists, else "installs".
    "apt-get": """echo "$*" >> "$SHIM_LOG/apt"
[ "$1" = update ] && exit 0
[ -f "$SHIM_LOG/offline" ] && exit 100
shift; for p in "$@"; do [ "$p" = -y ] || echo "$p" >> "$SHIM_LOG/installed"; done
""",
}


def _extract(src: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}\(\) \{{[^\n]*\n.*?^\}}\n", src, re.S | re.M)
    assert m, f"{name}() not found in saviour-config"
    return m.group(0)


@pytest.fixture
def rig(tmp_path):
    (tmp_path / "bin").mkdir()
    for name, body in SHIMS.items():
        p = tmp_path / "bin" / name
        p.write_text("#!/bin/bash\n" + body, encoding="utf-8", newline="\n")
        p.chmod(0o755)
    variants = tmp_path / "repo" / "src" / "modules" / "variants"
    for t, pkgs in (("camera", ""), ("rfid", "python3-pymysql"), ("template", "")):
        (variants / t).mkdir(parents=True)
        (variants / t / "variant.conf").write_text(
            f'NAME="{t}"\nAPT_PACKAGES="{pkgs}"\n', encoding="utf-8")
    (tmp_path / "config").write_text("ROLE=module\nTYPE=camera\nDEVICE_IP=\n",
                                     encoding="utf-8")
    return tmp_path


def _run(rig: Path, body: str) -> subprocess.CompletedProcess:
    src = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    funcs = "\n".join(_extract(src, f) for f in FUNCS)
    harness = f"""set -uo pipefail
export PATH='{rig / "bin"}':"$PATH" SHIM_LOG='{rig}'
DIR='{rig / "repo"}'
CONFIG_FILE='{rig / "config"}'
LOG='{rig / "log"}'
{funcs}
apply_from_config() {{ echo "APPLIED $(read_config_value "$CONFIG_FILE" TYPE)"; }}
{body}
"""
    script = rig / "harness.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")
    return subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=30)


def _type(rig):
    return re.search(r"^TYPE=(.*)$", (rig / "config").read_text(), re.M).group(1)


def test_prepare_installs_missing_packages_and_changes_nothing(rig):
    r = _run(rig, "prepare_module_type rfid; echo rc=$?")
    assert "rc=0" in r.stdout and "Installing python3-pymysql" in r.stdout
    assert "python3-pymysql" in (rig / "installed").read_text()
    assert _type(rig) == "camera"


def test_prepare_offline_fails_cleanly(rig):
    (rig / "offline").write_text("")
    r = _run(rig, "prepare_module_type rfid; echo rc=$?")
    assert "rc=2" in r.stdout and "nothing changed" in r.stderr
    assert "update" in (rig / "apt").read_text()         # retried after an update
    assert _type(rig) == "camera"


def test_prepare_offline_is_fine_when_nothing_is_missing(rig):
    (rig / "offline").write_text("")
    (rig / "installed").write_text("python3-pymysql\n")
    r = _run(rig, "prepare_module_type rfid; echo rc=$?")
    assert "rc=0" in r.stdout and not (rig / "apt").exists()


@pytest.mark.parametrize("bad, why", [
    ("camera", "Already a camera module"),
    ("template", "Unknown module type"),
    ("nope", "Unknown module type"),
    ("../x", "Unknown module type"),
    ("", "Unknown module type"),
])
def test_prepare_refuses_bad_types(rig, bad, why):
    r = _run(rig, f"prepare_module_type '{bad}'; echo rc=$?")
    assert "rc=1" in r.stdout and why in r.stderr


def test_prepare_refuses_a_controller(rig):
    (rig / "config").write_text("ROLE=controller\nTYPE=basic\n", encoding="utf-8")
    r = _run(rig, "prepare_module_type rfid; echo rc=$?")
    assert "rc=1" in r.stdout and "not a module" in r.stderr


def test_set_writes_type_then_applies(rig):
    r = _run(rig, "set_module_type rfid; echo rc=$?")
    assert "APPLIED rfid" in r.stdout and "rc=0" in r.stdout
    assert _type(rig) == "rfid"
    assert "DEVICE_IP=" in (rig / "config").read_text()      # other keys kept


def test_set_leaves_type_alone_when_prepare_fails(rig):
    (rig / "offline").write_text("")
    r = _run(rig, "set_module_type rfid; echo rc=$?")
    assert "rc=2" in r.stdout and "APPLIED" not in r.stdout
    assert _type(rig) == "camera"


def test_hardware_scan_json(rig):
    """--hardware-scan's JSON from hardware_scan's lines (the probes stubbed)."""
    src = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    funcs = "\n".join(_extract(src, f) for f in ("_hw_suggest", "_hw_line",
                                                   "hardware_scan_json"))
    body = f"""HW_REPORT=""; HW_SUGGEST=""; HW_ITEMS=""
{funcs}
hardware_scan() {{
    HW_ITEMS=""; HW_SUGGEST=""
    _hw_line "CSI camera" "imx477"; _hw_suggest camera habitat_camera camera
    _hw_line "AudioMoth" "none found"
    _hw_line "eth0" "up, 1000 Mb/s"
}}
hardware_scan_json
"""
    r = _run(rig, body)
    import json
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["suggested"] == ["camera", "habitat_camera"]          # de-duplicated
    assert out["items"] == [
        {"label": "CSI camera", "value": "imx477", "present": True, "system": False},
        {"label": "AudioMoth", "value": "none found", "present": False, "system": False},
        {"label": "eth0", "value": "up, 1000 Mb/s", "present": True, "system": True}]
