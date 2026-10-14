"""
SSH certificate access (plans/ssh-certificate-auth.md).

- saviour-config's `--apply-ssh` logic, run in a bash harness like
  test_saviour_config.py: the real functions with SSHD_DIR / SSH_SRC_DIR in a
  temp dir and `sshd`, `systemctl` and `curl` replaced by shims (the real
  `ssh-keygen` validates keys).
- tools/ssh_ca.py against the real `ssh-keygen`: init, sign, revoke.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "saviour-config"
FUNCS = ("log", "get_current_config", "_ssh_targets", "_ssh_valid_ca",
         "_ssh_valid_krl", "_ssh_valid_mode", "_ssh_fetch", "_ssh_auth_dropin",
         "_ssh_stage", "_ssh_reload", "_ssh_remove_installed", "_ssh_same",
         "_ssh_effective_passwords", "apply_ssh_access", "push_ssh_access",
         "_ssh_require_controller", "_ssh_set_source", "_ssh_cert_logins",
         "_ssh_set_login_mode", "_ssh_controller_ip", "ssh_controller_key",
         "ssh_set_controller_cert")

BASH = shutil.which("bash")
KEYGEN = shutil.which("ssh-keygen")
pytestmark = pytest.mark.skipif(BASH is None or KEYGEN is None,
                                reason="needs bash and ssh-keygen")

SHIMS = {
    # sshd -t passes unless SSHD_T_RC says otherwise. -T reports what our
    # login-mode drop-in says, or SSHD_T_PW (another drop-in winning).
    "sshd": """#!/bin/bash
if [ "$1" = "-T" ]; then
    pw=yes
    grep -qs "PasswordAuthentication no" "$SHIM_LOG/etc_ssh/sshd_config.d/01-saviour-auth.conf" && pw=no
    echo "passwordauthentication ${SSHD_T_PW:-$pw}"
    exit 0
fi
exit "${SSHD_T_RC:-0}"
""",
    # Certificate logins the guard looks for: $SHIM_LOG/journal, if present.
    "journalctl": """#!/bin/bash
cat "$SHIM_LOG/journal" 2>/dev/null
exit 0
""",
    "systemctl": """#!/bin/bash
echo "$*" >> "$SHIM_LOG/systemctl"
case "$1" in is-active) [ -n "${VNC_ON:-}" ] ;; esac
""",
    # Serves files from $SHIM_LOG/served/<basename>; CURL_DOWN=1 = unreachable.
    "curl": """#!/bin/bash
out="" url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift ;;
        -w|-m) shift ;;
        -*) ;;
        *) url="$1" ;;
    esac
    shift
done
echo "$url" >> "$SHIM_LOG/curl"
if [ -n "${CURL_DOWN:-}" ]; then printf 000; exit 7; fi
f="$SHIM_LOG/served/$(basename "$url")"
if [ -f "$f" ]; then cp "$f" "$out"; printf 200; else printf 404; fi
""",
}


def _extract(src: str, name: str) -> str:
    m = re.search(rf"^{re.escape(name)}\(\) \{{[^\n]*\n.*?^\}}\n", src, re.S | re.M)
    assert m, f"{name}() not found in saviour-config"
    return m.group(0)


@pytest.fixture
def rig(tmp_path):
    for d in ("bin", "etc_ssh", "src", "served"):
        (tmp_path / d).mkdir()
    for name, body in SHIMS.items():
        p = tmp_path / "bin" / name
        p.write_text(body, encoding="utf-8", newline="\n")
        p.chmod(0o755)
    subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-C", "test CA",
                    "-f", str(tmp_path / "ca")], check=True)
    return tmp_path


def _run(rig: Path, role: str, body: str, **env) -> subprocess.CompletedProcess:
    src = SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n")
    funcs = "\n".join(_extract(src, f) for f in FUNCS)
    (rig / "config").write_text(f"ROLE={role}\nTYPE=x\n", encoding="utf-8", newline="\n")
    p = lambda name: (rig / name).as_posix()  # noqa: E731 -- local shorthand
    # PATH is colon-separated, so Git Bash on Windows needs /c/... not C:/...
    bin_dir = re.sub(r"^([A-Za-z]):/", lambda m: f"/{m[1].lower()}/", p("bin"))
    harness = f"""set -uo pipefail
export PATH='{bin_dir}':"$PATH" SHIM_LOG='{rig.as_posix()}'
CONFIG_FILE='{p("config")}'
LOG='{p("log")}'
SSH_SRC_DIR='{p("src")}'
SSHD_DIR='{p("etc_ssh")}'
SSHD_BIN='{p("bin/sshd")}'
SSH_PRINCIPALS="saviour-admin saviour-controller"
CONTROLLER_IP_FILE='{p("controller_ip")}'
SSH_CTL_KEY='{p("src")}/controller_key'
SSH_CLIENT_DIR='{p("ssh_client")}'
{funcs}
push_ssh_access() {{ echo "PUSHED"; }}
{body}
"""
    script = rig / "harness.sh"
    script.write_text(harness, encoding="utf-8", newline="\n")
    return subprocess.run([BASH, script.as_posix()], capture_output=True, text=True,
                          timeout=60, env={**_base_env(), **env})


def _base_env():
    import os
    return dict(os.environ)


def _installed(rig: Path) -> dict:
    e = rig / "etc_ssh"
    return {k: (e / v) for k, v in {
        "ca": "saviour_user_ca.pub", "krl": "saviour_revoked_keys.krl",
        "principals": "auth_principals/pi",
        "dropin": "sshd_config.d/50-saviour-ca.conf"}.items()}


def _reloads(rig: Path) -> int:
    f = rig / "systemctl"
    return f.read_text().count("reload") if f.exists() else 0


# --------------------------------------------------------------------------- #
# saviour-config --apply-ssh                                                  #
# --------------------------------------------------------------------------- #

def test_controller_installs_its_ca_then_is_idempotent(rig):
    shutil.copy(rig / "ca.pub", rig / "src" / "user_ca.pub")
    r = _run(rig, "controller", "apply_ssh_access; echo rc=$?")
    assert "rc=0" in r.stdout and "applied" in r.stdout, r.stdout + r.stderr
    f = _installed(rig)
    assert f["ca"].read_bytes() == (rig / "ca.pub").read_bytes()
    assert f["krl"].read_bytes() == b""                 # nothing revoked yet
    assert f["principals"].read_text().split() == ["saviour-admin", "saviour-controller"]
    dropin = f["dropin"].read_text()
    assert f"TrustedUserCAKeys {f['ca'].as_posix()}" in dropin
    assert f"RevokedKeys {f['krl'].as_posix()}" in dropin
    assert "AuthorizedPrincipalsFile" in dropin and "auth_principals/%u" in dropin
    assert _reloads(rig) == 1

    r = _run(rig, "controller", "apply_ssh_access; echo rc=$?")
    assert "rc=0" in r.stdout and "up to date" in r.stdout
    assert _reloads(rig) == 1                            # no needless reload


def test_failed_sshd_check_restores_the_previous_config(rig):
    f = _installed(rig)
    f["dropin"].parent.mkdir(parents=True)
    f["dropin"].write_text("# old\n")
    shutil.copy(rig / "ca.pub", rig / "src" / "user_ca.pub")
    r = _run(rig, "controller", "apply_ssh_access; echo rc=$?", SSHD_T_RC="1")
    assert "rc=1" in r.stdout and "previous one was kept" in r.stderr
    assert f["dropin"].read_text() == "# old\n"
    assert not f["ca"].exists() and not f["krl"].exists()
    assert _reloads(rig) == 0


def test_clearing_the_ca_removes_certificate_trust(rig):
    shutil.copy(rig / "ca.pub", rig / "src" / "user_ca.pub")
    _run(rig, "controller", "apply_ssh_access")
    (rig / "src" / "user_ca.pub").unlink()
    r = _run(rig, "controller", "apply_ssh_access; echo rc=$?")
    assert "rc=0" in r.stdout and "removed" in r.stdout
    assert not any(p.exists() for p in _installed(rig).values())


def test_invalid_ca_changes_nothing(rig):
    (rig / "src" / "user_ca.pub").write_text("not a key\n")
    r = _run(rig, "controller", "apply_ssh_access; echo rc=$?")
    assert "rc=1" in r.stdout
    assert not any(p.exists() for p in _installed(rig).values())


def test_module_fetches_from_the_recorded_controller(rig):
    (rig / "controller_ip").write_text("10.0.5.1\n")
    shutil.copy(rig / "ca.pub", rig / "served" / "user_ca.pub")    # no KRL served
    r = _run(rig, "module", "apply_ssh_access; echo rc=$?")
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert "http://10.0.5.1:5000/ssh/user_ca.pub" in (rig / "curl").read_text()
    f = _installed(rig)
    assert f["ca"].read_bytes() == (rig / "ca.pub").read_bytes()
    assert f["krl"].read_bytes() == b""


def test_module_with_controller_unreachable_keeps_what_it_has(rig):
    shutil.copy(rig / "ca.pub", rig / "served" / "user_ca.pub")
    _run(rig, "module", "apply_ssh_access")
    before = {k: p.read_bytes() for k, p in _installed(rig).items()}
    r = _run(rig, "module", "apply_ssh_access; echo rc=$?", CURL_DOWN="1")
    assert "rc=1" in r.stdout and "left unchanged" in r.stdout
    assert {k: p.read_bytes() for k, p in _installed(rig).items()} == before
    assert "10.0.0.1" in (rig / "curl").read_text()      # default with no IP file


def test_module_drops_trust_when_the_controller_has_no_ca(rig):
    shutil.copy(rig / "ca.pub", rig / "served" / "user_ca.pub")
    _run(rig, "module", "apply_ssh_access")
    (rig / "served" / "user_ca.pub").unlink()
    r = _run(rig, "module", "apply_ssh_access; echo rc=$?")
    assert "rc=0" in r.stdout
    assert not any(p.exists() for p in _installed(rig).values())


def test_rejects_a_krl_that_is_not_one(rig):
    shutil.copy(rig / "ca.pub", rig / "src" / "user_ca.pub")
    (rig / "src" / "revoked_keys.krl").write_text("serial: 3\n")   # a spec, not a KRL
    r = _run(rig, "controller", "apply_ssh_access; echo rc=$?")
    assert "rc=1" in r.stdout
    assert not _installed(rig)["dropin"].exists()


def test_set_source_is_controller_only_and_none_clears_both(rig):
    r = _run(rig, "module", f"_ssh_set_source user_ca.pub '{(rig / 'ca.pub').as_posix()}'; echo rc=$?")
    assert "rc=1" in r.stdout and "Only a controller" in r.stderr

    r = _run(rig, "controller", f"_ssh_set_source user_ca.pub '{(rig / 'ca.pub').as_posix()}'; echo rc=$?")
    assert "rc=0" in r.stdout and "PUSHED" in r.stdout
    assert (rig / "src" / "user_ca.pub").exists() and _installed(rig)["ca"].exists()

    (rig / "src" / "revoked_keys.krl").write_bytes(b"")
    r = _run(rig, "controller", "_ssh_set_source user_ca.pub none; echo rc=$?")
    assert "rc=0" in r.stdout
    assert not (rig / "src" / "user_ca.pub").exists()
    assert not (rig / "src" / "revoked_keys.krl").exists()
    assert not _installed(rig)["dropin"].exists()


# --------------------------------------------------------------------------- #
# tools/ssh_ca.py                                                             #
# --------------------------------------------------------------------------- #

def _tool(*args):
    return subprocess.run([sys.executable, str(REPO / "tools" / "ssh_ca.py"), *args],
                          capture_output=True, text=True, timeout=60)


def _keygen_out(*args):
    return subprocess.run([KEYGEN, *args], capture_output=True, text=True)


def test_ca_tool_signs_logs_and_revokes(tmp_path):
    ca = tmp_path / "ca" / "saviour_ca"
    r = _tool("init", "--ca", str(ca), "--no-passphrase")
    assert r.returncode == 0, r.stderr
    assert _tool("init", "--ca", str(ca), "--no-passphrase").returncode != 0  # no overwrite

    user = tmp_path / "id_ed25519"
    subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(user)], check=True)
    r = _tool("sign", "--ca", str(ca), "--id", "alice", str(user) + ".pub")
    assert r.returncode == 0, r.stderr
    cert = tmp_path / "id_ed25519-cert.pub"
    info = _keygen_out("-L", "-f", str(cert)).stdout
    assert "saviour-admin" in info and "Serial: 1" in info and "alice-" in info

    r = _tool("sign", "--ca", str(ca), "--id", "desk", "--principal", "saviour-controller",
              "--source-address", "10.0.0.1/32", "--valid", "+52w", str(user) + ".pub")
    assert r.returncode == 0, r.stderr
    info = _keygen_out("-L", "-f", str(cert)).stdout
    assert "Serial: 2" in info and "source-address 10.0.0.1/32" in info

    log = (tmp_path / "ca" / "saviour_ca.log.csv").read_text().splitlines()
    assert len(log) == 3 and log[0].startswith("issued,serial,key_id")

    assert _tool("sign", "--ca", str(ca), "--id", "x", str(user)).returncode != 0  # private key

    assert _tool("revoke", "--ca", str(ca), "--serial", "2").returncode == 0
    krl = tmp_path / "ca" / "saviour_ca.krl"
    assert krl.read_bytes().startswith(b"SSHKRL")
    assert _keygen_out("-Q", "-f", str(krl), str(cert)).returncode != 0      # serial 2: revoked
    # Adding a second entry updates rather than replaces the list.
    assert _tool("revoke", "--ca", str(ca), "--serial", "1").returncode == 0
    _tool("sign", "--ca", str(ca), "--id", "bob", str(user) + ".pub")         # serial 3
    assert _keygen_out("-Q", "-f", str(krl), str(cert)).returncode == 0       # not revoked


# --------------------------------------------------------------------------- #
# login modes (phase 3)                                                       #
# --------------------------------------------------------------------------- #

CERT_LOGIN = ("Oct 14 16:56:01 host sshd-session[1]: Accepted publickey for pi from "
              "10.0.0.155 port 1 ssh2: RSA-CERT SHA256:x ID andrew-20261014-s1 (serial 1) CA ...\n")


def _with_ca(rig):
    shutil.copy(rig / "ca.pub", rig / "src" / "user_ca.pub")


def _auth(rig):
    return _installed(rig)["dropin"].parent / "01-saviour-auth.conf"


def test_mode_keys_turns_passwords_off_after_a_certificate_login(rig):
    _with_ca(rig)
    (rig / "journal").write_text(CERT_LOGIN)
    r = _run(rig, "controller", "_ssh_set_login_mode keys; echo rc=$?")
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert "password login no" in r.stdout and "PUSHED" in r.stdout
    auth = _auth(rig).read_text()
    assert "PasswordAuthentication no" in auth and "AuthorizedKeysFile" not in auth
    assert (rig / "src" / "login_mode").read_text().strip() == "keys"


def test_mode_certs_also_ignores_authorized_keys(rig):
    _with_ca(rig)
    (rig / "journal").write_text(CERT_LOGIN)
    _run(rig, "controller", "_ssh_set_login_mode certs")
    assert "AuthorizedKeysFile none" in _auth(rig).read_text()


def test_guard_refuses_without_a_certificate_login_unless_forced(rig):
    _with_ca(rig)
    r = _run(rig, "controller", "_ssh_set_login_mode keys; echo rc=$?")
    assert "rc=1" in r.stdout and "nobody has logged in" in r.stderr
    assert not _auth(rig).exists() and not (rig / "src" / "login_mode").exists()
    r = _run(rig, "controller", "_ssh_set_login_mode keys --force; echo rc=$?")
    assert "rc=0" in r.stdout and _auth(rig).exists()


def test_guard_refuses_without_a_ca_even_with_logins(rig):
    (rig / "journal").write_text(CERT_LOGIN)
    r = _run(rig, "controller", "_ssh_set_login_mode certs; echo rc=$?")
    assert "rc=1" in r.stdout and "no CA" in r.stderr


def test_back_to_any_removes_the_dropin(rig):
    _with_ca(rig)
    (rig / "journal").write_text(CERT_LOGIN)
    _run(rig, "controller", "_ssh_set_login_mode keys")
    r = _run(rig, "controller", "_ssh_set_login_mode any; echo rc=$?")
    assert "rc=0" in r.stdout and not _auth(rig).exists()
    assert not (rig / "src" / "login_mode").exists()
    assert _installed(rig)["ca"].exists()                 # trust stays


def test_clearing_the_ca_turns_passwords_back_on(rig):
    _with_ca(rig)
    (rig / "journal").write_text(CERT_LOGIN)
    _run(rig, "controller", "_ssh_set_login_mode certs")
    r = _run(rig, "controller", "_ssh_set_source user_ca.pub none; echo rc=$?")
    assert "rc=0" in r.stdout
    assert not _auth(rig).exists() and not (rig / "src" / "login_mode").exists()


def test_warns_when_another_setting_keeps_passwords_on(rig):
    """Like cloud-init's 50-cloud-init.conf if it sorted first: the mode is
    installed but sshd -T still says yes, so it must not report success."""
    _with_ca(rig)
    (rig / "src" / "login_mode").write_text("keys\n")
    r = _run(rig, "controller", "apply_ssh_access; echo rc=$?", SSHD_T_PW="yes")
    assert "rc=1" in r.stdout and "still allows passwords" in r.stderr


def test_module_takes_the_mode_from_its_controller(rig):
    shutil.copy(rig / "ca.pub", rig / "served" / "user_ca.pub")
    (rig / "served" / "login_mode").write_text("certs\n")
    r = _run(rig, "module", "apply_ssh_access; echo rc=$?")
    assert "rc=0" in r.stdout and "login mode certs" in r.stdout
    assert "AuthorizedKeysFile none" in _auth(rig).read_text()
    (rig / "served" / "login_mode").unlink()               # controller back to any
    r = _run(rig, "module", "apply_ssh_access; echo rc=$?")
    assert "rc=0" in r.stdout and not _auth(rig).exists()


def test_unknown_mode_changes_nothing(rig):
    shutil.copy(rig / "ca.pub", rig / "served" / "user_ca.pub")
    (rig / "served" / "login_mode").write_text("nopasswords\n")
    r = _run(rig, "module", "apply_ssh_access; echo rc=$?")
    assert "rc=1" in r.stdout and not _installed(rig)["dropin"].exists()


# --------------------------------------------------------------------------- #
# controller certificate (phase 2)                                            #
# --------------------------------------------------------------------------- #

def test_controller_key_and_certificate_round_trip(rig):
    r = _run(rig, "controller", "ssh_controller_key; echo rc=$?")
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    pub = next(ln[7:] for ln in r.stdout.splitlines() if ln.startswith("PUBKEY "))
    assert pub.startswith("ssh-ed25519 ") and "IP " in r.stdout
    # Same key on a second call.
    again = _run(rig, "controller", "ssh_controller_key").stdout
    assert pub in again

    (rig / "ctl.pub").write_text(pub + "\n")
    subprocess.run([KEYGEN, "-q", "-s", str(rig / "ca"), "-I", "controller-test",
                    "-n", "saviour-controller", "-O", "source-address=10.0.0.1/32",
                    "-V", "+1d", str(rig / "ctl.pub")], check=True)
    r = _run(rig, "controller",
             f"ssh_set_controller_cert '{(rig / 'ctl-cert.pub').as_posix()}'; echo rc=$?")
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert (rig / "src" / "controller_key-cert.pub").exists()
    conf = (rig / "ssh_client" / "50-saviour-controller.conf").read_text()
    assert "Match localuser pi,root" in conf and "controller_key-cert.pub" in conf


def test_controller_cert_for_another_key_is_refused(rig):
    _run(rig, "controller", "ssh_controller_key")
    subprocess.run([KEYGEN, "-q", "-t", "ed25519", "-N", "", "-f", str(rig / "other")],
                   check=True)
    subprocess.run([KEYGEN, "-q", "-s", str(rig / "ca"), "-I", "x", "-n",
                    "saviour-controller", str(rig / "other.pub")], check=True)
    r = _run(rig, "controller",
             f"ssh_set_controller_cert '{(rig / 'other-cert.pub').as_posix()}'; echo rc=$?")
    assert "rc=1" in r.stdout and "not for this controller" in r.stderr
    assert not (rig / "ssh_client" / "50-saviour-controller.conf").exists()
