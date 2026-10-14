#!/usr/bin/env python3
"""SAVIOUR SSH certificate authority helper (plans/ssh-certificate-auth.md).

Runs on the admin machine that holds the CA private key -- never on a Pi.
Windows (PowerShell, built-in OpenSSH), macOS and Linux; it only wraps
`ssh-keygen`, so the CA key never passes through anything else.

    python tools/ssh_ca.py init   --ca D:/saviour-ca/saviour_ca
    python tools/ssh_ca.py sign   --ca D:/saviour-ca/saviour_ca --id andrew ~/.ssh/id_ed25519.pub
    python tools/ssh_ca.py revoke --ca D:/saviour-ca/saviour_ca --serial 3
    python tools/ssh_ca.py install --ca D:/saviour-ca/saviour_ca --host pi@10.0.0.1
    python tools/ssh_ca.py certify-controller --ca D:/saviour-ca/saviour_ca --host pi@10.0.0.1
    python tools/ssh_ca.py show   ~/.ssh/id_ed25519-cert.pub

Files kept next to the CA key (`<ca>`), none of which belong in git:
    <ca>, <ca>.pub   the CA key pair (`init`)
    <ca>.serial      the last serial issued
    <ca>.log.csv     every certificate issued: serial, id, principals, expiry, key
    <ca>.krl         the revocation list (`revoke`), shipped by `install`

Principals: `saviour-admin` (people) and `saviour-controller` (a controller's
own key, for its scripts; sign with --source-address <controller ip>/32).
Every Pi lets either log in as `pi`.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PRINCIPALS = ("saviour-admin", "saviour-controller")


def _keygen() -> str:
    exe = shutil.which("ssh-keygen")
    if not exe:
        sys.exit("ssh-keygen not found. On Windows: Settings > Optional features > OpenSSH Client.")
    return exe


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run([_keygen(), *args], check=True, **kw)


def _next_serial(ca: Path) -> int:
    f = ca.with_name(ca.name + ".serial")
    n = int(f.read_text().strip() or 0) + 1 if f.exists() else 1
    f.write_text(f"{n}\n")
    return n


def _log(ca: Path, row: dict) -> None:
    f = ca.with_name(ca.name + ".log.csv")
    new = not f.exists()
    with f.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def _cert_path(pub: Path) -> Path:
    stem = pub.name[:-4] if pub.name.endswith(".pub") else pub.name
    return pub.with_name(stem + "-cert.pub")


def cmd_init(a) -> None:
    ca = Path(a.ca)
    if ca.exists():
        sys.exit(f"{ca} already exists; refusing to overwrite a CA.")
    ca.parent.mkdir(parents=True, exist_ok=True)
    args = ["-t", "ed25519", "-f", str(ca), "-C", a.comment]
    if a.no_passphrase:          # tests / throwaway CAs only
        args += ["-N", ""]
    _run(args)
    print(f"\nCA created. Private key: {ca}  (keep offline, back it up)")
    print(f"Public key to give saviour-config: {ca}.pub")


def _sign(ca: Path, pub: Path, ident: str, principal: str, valid: str,
          source_address: str | None) -> tuple[Path, str]:
    """Certify `pub` with the next serial and log it. Returns (cert, -L text)."""
    serial = _next_serial(ca)
    key_id = f"{ident}-{datetime.date.today():%Y%m%d}-s{serial}"
    args = ["-s", str(ca), "-I", key_id, "-n", principal, "-V", valid, "-z", str(serial)]
    if source_address:
        args += ["-O", f"source-address={source_address}"]
    _run([*args, str(pub)])
    cert = _cert_path(pub)
    info = _run(["-L", "-f", str(cert)], capture_output=True, text=True).stdout
    valid_text = next((ln.split(":", 1)[1].strip() for ln in info.splitlines()
                       if ln.strip().startswith("Valid:")), "")
    fp = _run(["-l", "-f", str(pub)], capture_output=True, text=True).stdout.split()[1]
    _log(ca, {"issued": datetime.datetime.now().isoformat(timespec="seconds"),
              "serial": serial, "key_id": key_id, "principal": principal,
              "valid": valid_text, "source_address": source_address or "",
              "key_fingerprint": fp})
    return cert, info


def cmd_sign(a) -> None:
    ca, pub = Path(a.ca), Path(a.pubkey).expanduser()
    if not pub.name.endswith(".pub"):
        sys.exit(f"{pub} is not a .pub file: sign the PUBLIC key, never the private one.")
    cert, info = _sign(ca, pub, a.id, a.principal, a.valid, a.source_address)
    print(info)
    print(f"Certificate: {cert}")
    print("Keep it next to the key; ssh uses it automatically.")


def cmd_certify_controller(a) -> None:
    """Fetch a controller's own SSH public key, certify it for principal
    saviour-controller from that controller's address only, and install the
    certificate there. The controller's scripts then SSH to modules with it."""
    for exe in ("scp", "ssh"):
        if not shutil.which(exe):
            sys.exit(f"{exe} not found.")
    out = subprocess.run(["ssh", a.host, "sudo saviour-config --controller-key"],
                         capture_output=True, text=True, check=True).stdout
    fields = dict(ln.split(" ", 1) for ln in out.splitlines() if " " in ln)
    if not fields.get("PUBKEY", "").startswith("ssh-"):
        sys.exit(f"Unexpected reply from {a.host}:\n{out}")
    with tempfile.TemporaryDirectory() as d:
        pub = Path(d) / "controller_key.pub"
        pub.write_text(fields["PUBKEY"].strip() + "\n")
        cert, info = _sign(Path(a.ca), pub, f"controller-{fields.get('HOST', 'unknown')}",
                           "saviour-controller", a.valid, f"{fields['IP'].strip()}/32")
        remote = "/tmp/saviour_controller_key-cert.pub"
        subprocess.run(["scp", str(cert), f"{a.host}:{remote}"], check=True)
        subprocess.run(["ssh", a.host, f"sudo saviour-config --set-controller-cert {remote}"
                        f"; rc=$?; rm -f {remote}; exit $rc"], check=True)
    print(info)


def cmd_revoke(a) -> None:
    ca = Path(a.ca)
    krl = ca.with_name(ca.name + ".krl")
    with tempfile.TemporaryDirectory() as d:
        if a.serial is not None:
            spec = Path(d) / "spec"
            spec.write_text(f"serial: {a.serial}\n")
            target = str(spec)
        else:
            target = str(Path(a.key).expanduser())
        args = ["-k", "-f", str(krl), "-s", f"{ca}.pub"]
        if krl.exists():
            args.insert(1, "-u")      # add to the existing list
        _run([*args, target])
    what = f"serial {a.serial}" if a.serial is not None else a.key
    print(f"Revoked {what} in {krl}. Ship it: python tools/ssh_ca.py install --ca {ca} --host pi@<controller>")


def cmd_install(a) -> None:
    """Copy the CA public key (and KRL, if any) to a controller and apply them
    there with saviour-config, which pushes them on to its modules."""
    ca = Path(a.ca)
    pub, krl = Path(f"{ca}.pub"), ca.with_name(ca.name + ".krl")
    if not pub.exists():
        sys.exit(f"{pub} not found.")
    files = [pub] + ([krl] if krl.exists() else [])
    for exe in ("scp", "ssh"):
        if not shutil.which(exe):
            sys.exit(f"{exe} not found.")
    subprocess.run(["scp", *map(str, files), f"{a.host}:/tmp/"], check=True)
    cmds = [f"sudo saviour-config --set-ssh-ca /tmp/{pub.name}"]
    if krl.exists():
        cmds.append(f"sudo saviour-config --set-ssh-krl /tmp/{krl.name}")
    cmds.append("rm -f " + " ".join(f"/tmp/{f.name}" for f in files))
    subprocess.run(["ssh", a.host, " && ".join(cmds)], check=True)


def cmd_show(a) -> None:
    _run(["-L", "-f", str(Path(a.cert).expanduser())])


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create the CA key pair")
    s.add_argument("--ca", required=True, help="path for the CA private key")
    s.add_argument("--comment", default="SAVIOUR SSH CA")
    s.add_argument("--no-passphrase", action="store_true",
                   help="no passphrase on the CA key (tests and throwaway CAs only)")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("sign", help="certify a person's or a controller's public key")
    s.add_argument("--ca", required=True)
    s.add_argument("--id", required=True, help="who this is for, e.g. andrew or controller-desk")
    s.add_argument("--principal", default="saviour-admin", choices=PRINCIPALS)
    s.add_argument("--valid", default="+30d", help="ssh-keygen -V, e.g. +30d, +52w (default +30d)")
    s.add_argument("--source-address", help="only usable from these addresses, e.g. 10.0.0.1/32")
    s.add_argument("pubkey", help="the .pub file to certify")
    s.set_defaults(fn=cmd_sign)

    s = sub.add_parser("revoke", help="add a certificate (by serial) or a key to the KRL")
    s.add_argument("--ca", required=True)
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--serial", type=int, help="certificate serial (see <ca>.log.csv)")
    g.add_argument("--key", help="a public key or certificate file to revoke outright")
    s.set_defaults(fn=cmd_revoke)

    s = sub.add_parser("install", help="send the CA public key + KRL to a controller")
    s.add_argument("--ca", required=True)
    s.add_argument("--host", required=True, help="e.g. pi@10.0.0.1")
    s.set_defaults(fn=cmd_install)

    s = sub.add_parser("certify-controller",
                       help="certify a controller's own key (for its SSH to modules) and install it")
    s.add_argument("--ca", required=True)
    s.add_argument("--host", required=True, help="e.g. pi@10.0.0.1")
    s.add_argument("--valid", default="+52w", help="ssh-keygen -V (default +52w)")
    s.set_defaults(fn=cmd_certify_controller)

    s = sub.add_parser("show", help="print a certificate's contents")
    s.add_argument("cert")
    s.set_defaults(fn=cmd_show)

    a = p.parse_args(argv)
    try:
        a.fn(a)
    except subprocess.CalledProcessError as e:
        sys.exit(f"failed: {' '.join(map(str, e.cmd))} (exit {e.returncode})")


if __name__ == "__main__":
    os.umask(0o077)   # new CA files readable by the owner only
    main()
