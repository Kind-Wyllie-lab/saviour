# SSH certificates for fleet login (key-only SSH at scale)

- **Status:** phases 1-3 built (2026-10-14). CA trust, login modes
  (`any`/`keys`/`certs`) and the controller certificate via `saviour-config`,
  controller-served to modules; `tools/ssh_ca.py`. Desk fleet trusts the SIDB
  IT CA, login mode left at `any`. Not done: controller certificate on
  hardware (needs the CA holder), VNC, other rigs, SSH as a step of device
  setup (phase 5, planned).
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
2. **Controller certificate.** *Built 2026-10-14.* Key at
   `/etc/saviour/ssh/controller_key`; `tools/ssh_ca.py certify-controller
   --ca <ca> --host pi@<controller>` fetches it (`--controller-key`), signs it
   (`saviour-controller`, `source-address=<controller ip>/32`, 52 weeks) and
   installs it (`--set-controller-cert`, which refuses a certificate for any
   other key) with an `ssh_config.d` drop-in for `pi` and `root`.
   `tools/framesync_sweep.py` runs on the admin PC, so it uses the person's
   certificate. Accept (outstanding, needs the CA holder):
   `push_credentials.sh` reaches a module in `certs` mode.
3. **Passwords off, old keys out.** *Built and accepted on the desk fleet
   2026-10-14.* The rig's **login mode**, set on the controller
   (`--set-ssh-login-mode any|keys|certs`, SSH access menu), served at
   `/ssh/login_mode`, applied as `sshd_config.d/01-saviour-auth.conf`:
   `keys` = `PasswordAuthentication no` + `KbdInteractiveAuthentication no`;
   `certs` adds `AuthorizedKeysFile none` (authorized_keys ignored, not
   deleted, so `any` restores it). Checked with `sshd -T` after the reload.
   Guard: refused unless the CA is set and the controller has seen a
   certificate login in 30 days (`--force` overrides). Clearing the CA clears
   the mode. Notes:
   - **Drop-in order.** sshd keeps the first value it reads, and Pis imaged
     with Raspberry Pi Imager carry cloud-init's
     `sshd_config.d/50-cloud-init.conf` = `PasswordAuthentication yes`
     (camera_0fcc, hailo_camera_3606 on the desk fleet, 2026-10-14). It sorts
     before `50-saviour-ca.conf`, so "passwords off" there would be silently
     ignored: put the auth settings in a drop-in that sorts first (e.g.
     `01-saviour-auth.conf`) and/or remove cloud-init's, and check with
     `sshd -T | grep passwordauthentication`, not by reading the file.
   - **`authorized_keys`.** Trusting a CA revokes nothing; `certs` mode is
     what stops the master-image key in every clone's `authorized_keys`.
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
5. **SSH in device setup.** *Planned.* Today SSH is configured only from the
   SSH access menu, the headless flags, a controller push or `mend`; a
   freshly configured device doesn't get it as part of setup. Make it part of
   `saviour-config`'s configure flow (`run_configuration`), for both roles:
   - **Controller, interactive** (`configure_device`): after role/type, an
     optional "SSH certificate authority" step: paste the CA public key, give
     a path, or skip. Shown only when `/etc/saviour/ssh/user_ca.pub` is
     missing; a re-run keeps the existing CA without asking. Login mode is not
     offered here: it stays `any` until someone has logged in with a
     certificate (the existing guard), so setup can't lock anyone out.
   - **Controller, headless** (`--apply`, boot-time provisioning): a CA
     public key dropped at `/etc/saviour/ssh/user_ca.pub` over the USB mount
     (alongside a hand-edited `/etc/saviour/config`) is picked up; nothing to
     add to `/etc/saviour/config`, so `write_config` needn't preserve a new
     key.
   - **Every run, both roles:** `run_configuration` calls `apply_ssh_access`
     near the end, non-fatal (`|| true` inside its `set -e` bracket, the
     outcome in the summary and the log). On a module the controller may not
     be reachable yet; an unreachable controller leaves trust unchanged.
   - **Module, first contact:** the module service runs
     `saviour-config --apply-ssh` in the background when it first connects to
     a controller, or connects to a different one (the address it records in
     `/var/lib/saviour/controller_ip` changes). A new module then picks up the
     rig's CA and login mode on its first connection, with no push or mend.
   - **Cloned controllers:** `run_clone_fix` ("Refresh identity") deletes
     `/etc/saviour/ssh/controller_key*` and the `ssh_config.d` drop-in. The
     certificate belongs to the old key and is pinned to the old controller's
     address; the clone gets a new key and needs `certify-controller` again.
     The CA, revocation list and login mode are deployment-wide and stay.
   - **Visibility:** `view_config` ("View current configuration") and the
     end-of-setup summary include `ssh_status_text`.

   Tests: the harness in `src/tests/test_ssh_access.py` and
   `test_saviour_config.py` (`run_configuration` calls the SSH step and
   survives its failure; clone fix removes the controller key but keeps the
   CA); a module test for the first-contact trigger (fires on a new or changed
   controller address, not on a reconnect to the same one).
   Accept, on the desk fleet: re-imaging one module (or clearing its
   `/etc/ssh` CA files and `controller_ip`) and running `saviour-config`
   leaves it trusting the CA with the rig's login mode after its first
   connection, with no push or mend; a controller set up from scratch offers
   the CA step and works with it skipped.

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
