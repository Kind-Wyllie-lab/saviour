"""GET /ssh/<file>: the SSH CA public key and revocation list modules fetch
for `saviour-config --apply-ssh` (plans/ssh-certificate-auth.md)."""

from src.controller.tests.test_rest_api import _web


def _client(tmp_path):
    web, _pw = _web()
    web._SSH_TRUST_DIR = str(tmp_path)
    return web.app.test_client()


def test_serves_the_ca_key_without_auth(tmp_path):
    (tmp_path / "user_ca.pub").write_bytes(b"ssh-ed25519 AAAA test CA\n")
    resp = _client(tmp_path).get("/ssh/user_ca.pub")
    assert resp.status_code == 200
    assert resp.data == b"ssh-ed25519 AAAA test CA\n"
    assert resp.mimetype == "text/plain"


def test_not_configured_is_404(tmp_path):
    c = _client(tmp_path)
    assert c.get("/ssh/user_ca.pub").status_code == 404
    assert c.get("/ssh/revoked_keys.krl").status_code == 404


def test_serves_the_krl(tmp_path):
    (tmp_path / "revoked_keys.krl").write_bytes(b"SSHKRL\n\x00rest")
    resp = _client(tmp_path).get("/ssh/revoked_keys.krl")
    assert resp.status_code == 200 and resp.data.startswith(b"SSHKRL")


def test_only_the_two_trust_files(tmp_path):
    (tmp_path / "other").write_text("secret")
    c = _client(tmp_path)
    assert c.get("/ssh/other").status_code == 404
    # A decoded slash never matches /ssh/<name>; whatever else answers (the
    # frontend's catch-all, when dist/ is built), it isn't the file.
    assert b"secret" not in c.get("/ssh/..%2Fother").data


def test_serves_the_login_mode(tmp_path):
    (tmp_path / "login_mode").write_bytes(b"certs\n")
    resp = _client(tmp_path).get("/ssh/login_mode")
    assert resp.status_code == 200 and resp.data == b"certs\n"


def test_serves_the_controller_public_key_but_never_the_private(tmp_path):
    (tmp_path / "controller_key.pub").write_bytes(b"ssh-ed25519 AAAA ctl\n")
    (tmp_path / "controller_key").write_bytes(b"PRIVATE")
    c = _client(tmp_path)
    resp = c.get("/ssh/controller_key.pub")
    assert resp.status_code == 200 and resp.data == b"ssh-ed25519 AAAA ctl\n"
    assert c.get("/ssh/controller_key").status_code == 404
