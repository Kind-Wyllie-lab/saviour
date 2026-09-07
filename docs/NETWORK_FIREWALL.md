# Controller wlan0 / WAN firewall

## Why

Every controller service binds `0.0.0.0`:

| Service | Port(s) | Auth |
|---|---|---|
| Flask/SocketIO web UI + `/api/v1` REST | 5000/tcp | shared password (plaintext HTTP) |
| `:80` → `:5000` redirect (`configure_mdns`) | 80/tcp | — |
| ZeroMQ ROUTER command bus | 5555/tcp | **none** |
| ZeroMQ PUB status bus | 5556/tcp | **none** |
| Samba `smbd` | 445/tcp, 139/tcp | share password |
| Samba `nmbd` | 137,138/udp | — |

On the PoE LAN (`eth0`) that is by design — the LAN is the trust boundary
(see the threat model in `CLAUDE.md`). But a controller that also has
`wlan0` on campus wifi (eduroam / UoE‑Device) would expose all of the above
to every other client on that network if AP/client isolation is imperfect.

`ptp4l`, `dnsmasq` and `avahi` are already scoped to `eth0` in their own
configs and are not affected by any of this.

## What `configure_firewall()` does

`saviour-config` installs a default‑deny `INPUT` filter **scoped to the
untrusted interface(s) only** — it never touches `eth0`, `lo` or
`tailscale0`.

- **Untrusted interfaces** = `wlan0` always (the rule is inert if `wlan0`
  is absent) plus `WAN_INTERFACE` when this controller shares internet from
  something other than `eth0` (`GATEWAY_MODE=controller`).
- A chain `SAVIOUR-WLAN-IN` is hooked as the first `INPUT` rule for each
  untrusted interface, for both IPv4 and IPv6 (`ip6tables` is skipped, with
  a log line, on a kernel where it is unusable).
- The chain **allows**: established/related return traffic, ICMP / ICMPv6
  (ICMPv6 is required for IPv6 to work at all), the DHCP client ports, and
  Tailscale's direct‑path UDP `41641` (so it is not forced onto a DERP
  relay). Everything else inbound is **dropped**.
- Persisted with `netfilter-persistent save` (same mechanism as the
  existing NAT rules) → `/etc/iptables/rules.v{4,6}`.

`smbd` / `nmbd` are additionally pinned in `smb.conf`:

```
bind interfaces only = yes
interfaces = lo eth0
```

so Samba does not even open a socket on `wlan0`, independent of the filter.
Add `tailscale0` to that line if you want to reach the share over Tailscale.

## Remote access is unaffected

- **Tailscale SSH** — arrives on `tailscale0`, which is never filtered.
- **`ssh -D 1080` SOCKS to the web UI** — `sshd` on the controller makes
  the onward connection to `:5000` from `localhost`/`eth0`, not `wlan0`.
  Point the proxied browser at `http://localhost:5000` or the controller's
  **eth0** IP, not a name that resolves to its `wlan0` address.
- **Modules on the PoE LAN** — unchanged; `eth0` is not filtered.

### Break‑glass

To keep direct SSH reachable on the untrusted interface as a fallback to
Tailscale, add to `/etc/saviour/config`:

```
FIREWALL_WLAN_SSH=yes
```

then re‑apply (`sudo saviour-config --apply-firewall`). This key survives
`saviour-config` rewriting the file.

## Applying / re‑applying

| Situation | Command |
|---|---|
| Fresh provisioning / role change | automatic (`run_configuration`) |
| Re‑run on an existing controller | `sudo saviour-config` → pick the same role/type |
| Fleet, non‑interactive | `sudo bash mend.sh` (step 9) |
| Just the firewall + `smb.conf` binding | `sudo saviour-config --apply-firewall` |
| Remove it | `sudo saviour-config` → switch to module role, or `uninstall.sh` |

## Verifying on a device

```bash
# What is actually listening
sudo ss -tulpnH | awk '{print $1,$5,$7}' | sort -u

# The chain and its hooks
sudo iptables  -S INPUT | grep SAVIOUR-WLAN-IN
sudo iptables  -S SAVIOUR-WLAN-IN
sudo ip6tables -S SAVIOUR-WLAN-IN

# From another host on the same wifi (should now fail / time out):
curl -m 3 http://<controller-wlan0-ip>:5000/   ; echo $?
nc -vz -w3 <controller-wlan0-ip> 445 5555

# rpcbind: nothing here needs it — disable if present
sudo ss -tulpnH | grep -E ':111|rpcbind' && sudo systemctl disable --now rpcbind.socket rpcbind.service
```
