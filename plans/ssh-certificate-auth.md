# SSH certificates for fleet login (key-only SSH at scale)

- **Status:** phase 1 built (2026-10-14): CA trust via `saviour-config`,
  controller-served to modules, `tools/ssh_ca.py`. Passwords still on.
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

OpenSSH user certificates. One certificate authority (CA) key pair signs
people's ordinary public keys for a limited time; every Pi trusts the CA's
public key instead of a list of individual keys.

- **The CA is per deployment, never in the repo.** A CA public key shipped as
  a default would let whoever holds that CA's private key into every lab that
  installs SAVIOUR. Each deployment makes its own (`tools/ssh_ca.py init`) and
  gives the public half to its controller.
- **Trust on every Pi** (written by `saviour-config --apply-ssh`):
  - `/etc/ssh/saviour_user_ca.pub`: the CA public key;
  - `/etc/ssh/saviour_revoked_keys.krl`: the revocation list (empty = none);
  - `/etc/ssh/auth_principals/pi`: `saviour-admin`, `saviour-controller`;
  - `/etc/ssh/sshd_config.d/50-saviour-ca.conf`:
    ```
    TrustedUserCAKeys /etc/ssh/saviour_user_ca.pub
    RevokedKeys /etc/ssh/saviour_revoked_keys.krl
    AuthorizedPrincipalsFile /etc/ssh/auth_principals/%u
    ```
  Certificates carry a principal, so one issued for one purpose can't be used
  for another. The KRL is always written (empty if nothing is revoked):
  sshd refuses **every** public key, `authorized_keys` included, if the
  `RevokedKeys` file can't be read.
- **People:** each person keeps their own key pair (passphrase, `ssh-agent`)
  and gets a certificate for principal `saviour-admin`, valid 30 days by
  default (`tools/ssh_ca.py sign --id <person> <key>.pub`).
- **Controllers:** each controller's own key gets a longer certificate for
  principal `saviour-controller`, restricted to its LAN address
  (`--source-address 10.0.0.1/32`), for `push_credentials.sh`,
  `tools/framesync_sweep.py` and any controller-run SSH to modules.
- **Removing access:** certificates expire; for someone leaving, stop issuing.
  Before expiry (lost laptop, compromised controller):
  `tools/ssh_ca.py revoke --serial <n>` then `install` (below).
- **Reaching modules:** modules sit behind their controller:
  `ssh -J pi@<controller> pi@<module>`. One certificate covers both hops; a
  `~/.ssh/config` `ProxyJump` entry per rig hides it.

## Where things live

- **In the repo:** this plan, `tools/ssh_ca.py` (wraps `ssh-keygen`, takes the
  CA path as an argument, holds no secrets), the `saviour-config` /
  `mend.sh` steps.
- **Admin machine only** (next to the CA key, all written by `ssh_ca.py`):
  the CA private key (offline, passphrase, backed up), `<ca>.serial`,
  `<ca>.log.csv` (who holds which serial), `<ca>.krl`.
- **Controller:** `/etc/saviour/ssh/user_ca.pub` and `revoked_keys.krl`, the
  deployment's source of truth, served to modules at `GET /ssh/<file>`
  (unauthenticated, like `/update/package`: both are public by design).
- **Never anywhere but the admin machine:** the CA private key. No Pi ever
  signs anything.

## Distribution

- **Setting it (controller):** `sudo saviour-config` → "SSH access" (paste the
  CA public key, or a path), or headless `--set-ssh-ca FILE|none` /
  `--set-ssh-krl FILE|none`. From the admin machine,
  `python tools/ssh_ca.py install --ca <ca> --host pi@<controller>` copies the
  public key and KRL over and runs those. Setting either applies it on the
  controller and pushes to modules.
- **Push to modules:** `saviour-config --push-ssh` sends the module command
  `apply_ssh_access` to every connected module (through `/facade/send_command`,
  admin password read on the controller), which runs
  `saviour-config --apply-ssh`: fetch `/ssh/user_ca.pub` and
  `/ssh/revoked_keys.krl` from the controller and install them.
- **Catch-up:** `mend.sh` runs `--apply-ssh` on every device, so a module that
  was offline for a push gets the current CA and KRL on its next mend.
- **Finding the controller:** the module service records the controller's
  address in `/var/lib/saviour/controller_ip` whenever it connects (a shell
  script can't do the mDNS lookup); `--apply-ssh` falls back to 10.0.0.1.
- **Safety:** files are staged and compared first (no change = no reload);
  `sshd -t` runs before the reload and the previous files are restored if it
  fails. An unreachable controller leaves a module's current trust as it is;
  a controller with no CA (404) makes modules drop theirs.
- **Caveat (accepted under the threat model):** modules trust whatever CA
  their controller serves, so a LAN host impersonating the controller could
  push its own. Same exposure as the unsigned update package (backlog,
  "Deferred for v1.0").

## Rollout (phased; nobody locked out at any step)

1. **Trust the CA, passwords still on.** *Built and accepted 2026-10-14*
   (desk fleet, throwaway CA, since removed; see docs/CHANGELOG.md). Accept: on the
   desk controller and one module, a certificate login works (`ssh -v` shows
   the certificate accepted), an expired certificate is refused, a revoked
   serial is refused, a certificate without the principal is refused, the
   controller certificate is refused from any address but 10.0.0.1.
2. **Switch the scripts.** Issue each controller's certificate (a
   `saviour-config` action to make the controller's key and show its public
   half for signing). Change `push_credentials.sh` and
   `tools/framesync_sweep.py` to rely on it. Accept: both run on the desk
   fleet with no password prompt.
3. **Passwords off, old keys out.** Add `PasswordAuthentication no` and
   `KbdInteractiveAuthentication no` to the drop-in, pushed from the
   controller like the CA, per rig at a planned time. Guard: refuse unless a
   CA is installed and the device has seen a certificate login. Also:
   - **Drop-in order.** sshd keeps the first value it reads, and Pis imaged
     with Raspberry Pi Imager carry cloud-init's
     `sshd_config.d/50-cloud-init.conf` = `PasswordAuthentication yes`
     (camera_0fcc, hailo_camera_3606 on the desk fleet, 2026-10-14). It sorts
     before `50-saviour-ca.conf`, so "passwords off" there would be silently
     ignored: put the auth settings in a drop-in that sorts first (e.g.
     `01-saviour-auth.conf`) and/or remove cloud-init's, and check with
     `sshd -T | grep passwordauthentication`, not by reading the file.
   - **Empty `authorized_keys`.** Trusting a CA revokes nothing: the
     master-image key in every clone's `authorized_keys` keeps working until
     it's removed. `clone_prep.sh` empties it on new clones.
   - **VNC.** RealVNC on the Pis logs in with the `pi` password, the same on
     every clone; SSH passwords off doesn't touch it. Disable VNC fleet-wide
     or rotate the `pi` password per device (backlog: "Fleet image cloning
     never rotates the OS user's password"). The `pi` password is also
     `sudo`'s.
   Before each rig: certificate login to the controller and every module,
   console access as a fallback. Accept: password login refused fleet-wide;
   certificate login and controller scripts unaffected.
4. **(Optional) host certificates.** Sign host keys at clone/first boot,
   `@cert-authority` in admins' `known_hosts`: no more "host key changed"
   prompts for re-imaged Pis on reused IPs, and `push_credentials.sh` can
   drop `StrictHostKeyChecking=no`.

**Rollback:** `saviour-config --set-ssh-ca none` on the controller (pushes the
removal), or delete the drop-in by console; passwords and `authorized_keys`
are untouched in phases 1-2.

**Losing the CA key:** with passwords off you can't issue certificates. Reach
the controller by console, make a new CA, set it there; modules take it on the
push or their next mend. Keep an encrypted backup of the CA key, or a second
holder.

## Open questions

- Certificate lifetime for people: 30 days to start; a week is tighter but
  means weekly signing with the offline CA key.
- Who holds the CA key, and a second holder for continuity.
- One CA for all rigs, or one per site? One is simpler; per-site limits a CA
  compromise to one site.
- Root: `PermitRootLogin without-password` today; set `no` in the drop-in in
  phase 3 unless something needs root SSH (nothing found so far).
