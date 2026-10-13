# SSH certificates for fleet login (key-only SSH at scale)

- **Status:** proposed
- **Created:** 2026-10-13
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Security": clones share the OS login
  credential; `push_credentials.sh` disables host-key checks.

## Why

Every Pi accepts SSH password logins (`PasswordAuthentication yes`, checked on
the desk controller and `apa_arduino_2904` 2026-10-13), and cloning copies one
login password and one `~/.ssh/authorized_keys` to every device
(`clone_prep.sh` leaves both "intentionally unchanged"). So one leaked
password, or one leaked private key in the master image's `authorized_keys`,
opens every Pi in every rig, and there's no practical way to take a person's
access away: that means editing `authorized_keys` on hundreds of devices.

Not brute force: there were no failed SSH logins in 7 days on either Pi, and
the controller's wlan0 firewall already drops SSH. fail2ban was considered and
rejected: it guards against guessing that isn't happening, and could lock the
controller or the admin's PC out of modules during scripted fleet work.

## Design

OpenSSH user certificates. One certificate authority (CA) key signs people's
ordinary public keys for a limited time; every Pi trusts the CA instead of
individual keys.

- **Trust anchor on every Pi:** `/etc/ssh/user_ca.pub` plus a drop-in
  `/etc/ssh/sshd_config.d/50-saviour-ca.conf`:
  ```
  TrustedUserCAKeys /etc/ssh/user_ca.pub
  RevokedKeys /etc/ssh/revoked_keys.krl
  AuthorizedPrincipalsFile /etc/ssh/auth_principals/%u
  ```
  `/etc/ssh/auth_principals/pi` lists the principals allowed to log in as
  `pi` (e.g. `saviour-admin`, `saviour-controller`). Certificates carry a
  principal, so a certificate for one purpose can't be used for another.
- **People:** each person keeps their own key pair (with a passphrase, in
  `ssh-agent`) and gets a certificate signed for principal `saviour-admin`,
  valid for a week:
  ```
  ssh-keygen -s user_ca -I <person>-<yyyymmdd> -n saviour-admin -V +1w \
      -z <serial> id_ed25519.pub
  ```
  `-I` is a key id for logs, `-z` a serial number for revocation.
- **Controllers:** each controller's own key gets a longer certificate
  (e.g. 1 year) for principal `saviour-controller`, restricted with
  `-O source-address=10.0.0.1/32` so it only works from the controller's LAN
  address. Covers `push_credentials.sh`, `tools/framesync_sweep.py` and any
  controller-run SSH to modules.
- **Removing access:** certificates expire; for someone leaving, stop issuing.
  Before expiry (lost laptop, compromised controller), add the certificate's
  serial or key to the revocation list (`ssh-keygen -k -u -f revoked_keys.krl
  -z <serial>`) and ship it to the fleet (below).
- **Reaching modules:** modules sit on each rig's private network behind its
  controller, so: `ssh -J pi@<controller> pi@10.0.0.190`. One certificate
  covers both hops. A `~/.ssh/config` `ProxyJump` entry per rig makes it
  invisible.
- **Host keys (optional, phase 4):** the same mechanism the other way round
  (a host CA signing each Pi's host key at clone time) would end the
  "host key changed" prompts for re-imaged Pis on reused IPs and remove the
  reason for `StrictHostKeyChecking=no` in `push_credentials.sh`.

## What goes in the repo and what doesn't

The repo is public. The design above is standard and safe to publish; its
security rests on the CA private key, not on the design being unknown.

- **In the repo:** this plan; the CA *public* key
  (`deploy/ssh/user_ca.pub`); the sshd drop-in; `auth_principals/pi`; the
  signing helper script (it takes the CA key path as an argument and contains
  no secrets); the revocation list (it holds serials and key fingerprints,
  not names).
- **Never in the repo:** the CA private key; any private key; the shared login
  password; the issued-certificate log (who holds which serial); a rig
  inventory (names, locations, real addresses).
- **Where those live:** CA private key offline (an admin-only machine or an
  encrypted vault, with an offline backup), never on a Pi or a daily laptop.
  Certificate log and rig inventory in a password manager or a private lab
  repo.

## Distribution to the fleet

- **New images:** `setup.sh` / `saviour-config` install the trust anchor,
  drop-in and principals file from `deploy/ssh/`, so every clone trusts the CA
  with no per-device step. `clone_prep.sh` empties `~/.ssh/authorized_keys`
  (and says so in its summary) once the CA is in place.
- **Existing Pis and later changes (revocation list, CA rotation):**
  `mend.sh` installs the same files, so `run_mend` reaches every module
  through the controller, and a normal update + mend carries a new
  revocation list. `mend.sh` reloads `ssh` only if the files changed, and
  runs `sshd -t` first, leaving the old config in place if the new one fails
  to parse.
- **Caveat (accepted under the threat model):** the update package modules
  fetch is unsigned (`plans/backlog.md`, "Deferred for v1.0"), so a LAN host
  that impersonates the controller could push its own CA. Same exposure as
  pushing any code today; closing it is the package-signing work, not this
  plan.

## Rollout (phased; nobody locked out at any step)

1. **Build, passwords still on.** Generate the CA, add `deploy/ssh/`, the
   signing script, and the `mend.sh` / `saviour-config` install steps.
   `PasswordAuthentication` unchanged. Desk fleet first.
   Accept: on the desk controller and one module, a certificate login works
   (`ssh -v` shows `Server accepts key: ... CERT`), an expired certificate is
   refused, a revoked serial is refused, a certificate without the principal
   is refused, the controller certificate is refused from any address but
   10.0.0.1.
2. **Switch the scripts.** Issue each controller's certificate. Change
   `push_credentials.sh` and `tools/framesync_sweep.py` to rely on it
   (drop `StrictHostKeyChecking=no` once host keys are handled, phase 4).
   Accept: both scripts run on the desk fleet with no password prompt.
3. **Passwords off.** Add `PasswordAuthentication no` (and
   `KbdInteractiveAuthentication no`) to the drop-in, desk fleet first, then
   rig by rig at a planned time. Before each rig: confirm certificate login to
   the controller and every module, and that console or VNC access works as a
   fallback. Note: the `pi` password still matters for `sudo` and the desktop
   session; rotating it per device is separate (backlog: "Fleet image cloning
   never rotates the OS user's password").
   Accept: password login refused fleet-wide; certificate login and
   controller scripts unaffected; `authorized_keys` empty on new clones.
4. **(Optional) host certificates.** Sign host keys at clone/first boot
   (`multiclone.sh` / `clone_prep.sh` already regenerate them), add
   `@cert-authority` to admins' `known_hosts`.

**Rollback:** remove the drop-in (or set `PasswordAuthentication yes` in it)
via mend or console; passwords work again. Phases 1-2 change nothing for
password users.

## Open questions

- Certificate lifetime for people: a week (re-sign weekly) vs a day (more
  re-signing, smaller window). Start with a week.
- Who holds the CA key, and a second holder for continuity.
- One CA for all rigs, or one per site? One is simpler; per-site limits a CA
  compromise to one site.
- Root: `PermitRootLogin without-password` today; set `no` in the same
  drop-in unless something needs root SSH (nothing found so far).
