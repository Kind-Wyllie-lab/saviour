"""Tests for src/shared/cifs.py (credential handling found in the 2026-09-30 soak)."""

import os
import stat
import sys

import pytest

from src.shared.cifs import cifs_auth_option, redact_secrets


def test_no_username_is_guest(tmp_path):
    assert cifs_auth_option("", "x", directory=str(tmp_path)) == "guest"


def test_writes_a_private_credentials_file(tmp_path):
    opt = cifs_auth_option("saviour_module", "s3cret", "probe", directory=str(tmp_path))
    path = opt.split("=", 1)[1]
    assert opt.startswith("credentials=") and "s3cret" not in opt
    with open(path) as f:
        assert f.read() == "username=saviour_module\npassword=s3cret\n"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_rewrites_on_each_call(tmp_path):
    cifs_auth_option("u", "old", "p", directory=str(tmp_path))
    path = cifs_auth_option("u", "new", "p", directory=str(tmp_path)).split("=", 1)[1]
    with open(path) as f:
        assert "password=new" in f.read()


@pytest.mark.parametrize(("raw", "expected"), [
    ('set_export_config {"share_password": "hunter2", "share_ip": "10.0.0.1"}',
     'set_export_config {"share_password": "***", "share_ip": "10.0.0.1"}'),
    ('x {"NAS_PASSWORD":"a b"}', 'x {"NAS_PASSWORD":"***"}'),
    ('get_health {}', 'get_health {}'),
])
def test_redact_secrets(raw, expected):
    assert redact_secrets(raw) == expected
