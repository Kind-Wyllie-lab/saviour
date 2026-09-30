# Security Model & Limitations

SAVIOUR is designed to run on a **closed, dedicated lab network**: the
controller, its modules and the PoE switch(es) they share, with nothing else
on that LAN except the machines you use to operate it. This page states
plainly what that design does and does not protect against, so you can
decide where it is safe to deploy.

## The trust boundary is the LAN

Anyone who can reach the SAVIOUR network is treated as authorised, in the
same way that anyone with SSH or a keyboard on one of the Pis already is.

The web interface's **guest / admin** split exists to stop an operator
*accidentally* breaking a running experiment (a wrong button, a mid-session
config change). It is **not** a defence against a deliberate attacker who is
already on the network.

**Do not** connect SAVIOUR's `eth0` network to a building, campus or
internet-facing network, or bridge other untrusted devices onto it.

## What SAVIOUR does NOT protect against

Assume anything on the SAVIOUR LAN can do all of the following:

| Exposure | Detail |
|---|---|
| **Control any module without a password** | The ZeroMQ command bus (TCP 5555/5556) has no authentication. Any LAN host can start/stop recordings, change config, trigger updates or shut modules down, and can impersonate a module's ID to intercept its commands. |
| **Read the admin password** | The web UI and REST API (`:5000`) use plain HTTP, so the shared admin password crosses the network in clear text on every login. The browser also caches it indefinitely. There is no login lockout and no per-user accounts. |
| **Watch the live camera feeds** | Each camera module's MJPEG preview (port 8080 and up) is unauthenticated and served directly from the module. It can show animal procedures and identifiable people. |
| **Read recorded data in transit** | Exports to the controller's Samba share are not encrypted (no SMB3 sealing). |
| **Push unsigned software** | Updates (`update_saviour`, `/update/package`) are only checked to be a valid zip, not signed. Anyone on the LAN can serve a modified package. |
| **Log in to a cloned device** | Cloning an SD image copies its OS login password and SSH `authorized_keys` unchanged to every device made from it. |

The software also runs as `root` without systemd sandboxing.

## What SAVIOUR does protect against

- **A controller that is also on Wi-Fi / a WAN link.** `saviour-config`
  installs a default-deny firewall on `wlan0` (and on the WAN interface when
  sharing internet), and Samba only listens on `lo` and `eth0`. See
  `docs/NETWORK_FIREWALL.md` in the repository.
- **Operator mistakes.** Config changes are refused for modules that are
  recording, destructive actions need the admin password, and sessions with
  exports that never reached the share can't be deleted without an explicit
  override.
- **Credentials in source control.** Known hard-coded Samba passwords have
  been removed. Anything that was ever committed to git history should be
  treated as compromised and rotated.

## Remote access (Tailscale etc.)

Tailscale's `tailscale0` interface is **not** filtered by the firewall. Every
exposure in the table above is therefore reachable from **every node on your
tailnet**, not just the lab LAN. If you use Tailscale, restrict which tailnet
devices can reach the controller with Tailscale ACLs, and prefer an
`ssh -D` SOCKS proxy to the web UI over exposing `:5000` directly.

## Checklist before deploying

1. The SAVIOUR `eth0` network is physically separate, or a VLAN nothing else
   can reach.
2. Everyone who can reach that network is someone you would trust with the
   admin password and with the recorded data.
3. If the controller also joins Wi-Fi, the firewall is installed
   (`sudo saviour-config --apply-firewall`) and you use a unique admin
   password there.
4. After cloning SD cards, change the `pi` user's password on each device
   and replace `~/.ssh/authorized_keys` if the master image carried keys.
5. Any tailnet that includes the controller is locked down with ACLs.

Hardening beyond this (encrypted and authenticated module traffic, HTTPS,
named accounts, signed updates) is planned after v1.0. The design notes are
in the repository's `plans/` directory (`remote-access-auth-hardening.md`,
`mjpeg-stream-auth.md`, `firewall-interface-scoping.md`).
