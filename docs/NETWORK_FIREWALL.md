# Controller wlan0 / WAN firewall

## Why

The controller has two defences against a `wlan0` that sits on campus wifi
(eduroam / UoE-Device), where AP/client isolation can't be relied on:

1. **SAVIOUR's services listen on the eth0 address only** (roadmap B2).
2. **A default-deny firewall on `wlan0`** (and the WAN interface) catches
   everything else on the box that listens on every interface, sshd first
   of all.

`wlan0` is then only used for *outbound* traffic: package downloads and
Tailscale's own transport. Replies to those are allowed back in.

| Service | Port(s) | Listens on | Auth |
|---|---|---|---|
| Flask/SocketIO web UI + `/api/v1` REST | 5000/tcp | eth0 address | shared password (plaintext HTTP) |
| `:80` → `:5000` redirect (`configure_mdns`) | 80/tcp | `-i eth0` | — |
| ZeroMQ ROUTER command bus | 5555/tcp | eth0 address | **none** |
| ZeroMQ PUB status bus | 5556/tcp | eth0 address | **none** |
| Samba `smbd` / `nmbd` | 445, 139 / 137, 138 | `lo eth0` | share password |

`ptp4l`, `dnsmasq` and `avahi` are already scoped to `eth0` in their own
configs.

### Services bound to eth0

`interface.listen_on` in the controller config (default `"lan"`) sets the
address the web UI and the ZMQ bus listen on:

| Value | Listens on |
|---|---|
| `"lan"` (default) | the controller's validated eth0 address (normally `10.0.0.1`) |
| `"all"` | every interface - the pre-B2 behaviour; only rely on this with the firewall below in place |
| an IP address | that address |

Consequences:

- The web UI is **not** on `localhost:5000` any more. Use
  `http://10.0.0.1:5000` (or the eth0 address) from the controller itself.
- A client reaching the controller's `wlan0` or Tailscale address on `:5000`
  gets nothing. For Tailscale, use `tailscale serve` (below).

### Web UI over Tailscale

Tailscale (`tailscale0`) is treated as trusted and is never filtered - on the
assumption the tailnet only contains your own machines. Because the web UI
listens on the eth0 address only, publish it to the tailnet with
`tailscale serve`, which proxies to it and adds HTTPS:

```bash
sudo tailscale serve --bg http://10.0.0.1:5000
tailscale serve status          # shows the https://<name>.<tailnet>.ts.net URL
```

Then browse to `https://<controller-name>.<tailnet>.ts.net`. Turn it off with
`sudo tailscale serve --https=443 off`. (HTTPS certificates must be enabled
for the tailnet in the Tailscale admin console.)

### Is the firewall actually on?

The controller checks at startup (`src/controller/firewall_status.py`) that
every untrusted interface that exists has the `SAVIOUR-WLAN-IN` hook and the
chain ends in `DROP`, for IPv4 and IPv6. If not, it logs an ERROR, sends an
alert, and the System page shows **"Firewall not active on …"** under the
controller row. Fix with `sudo saviour-config --apply-firewall`.

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
  the onward connection itself. Point the proxied browser at the
  controller's **eth0** IP (`http://10.0.0.1:5000`); `localhost` no longer
  works because the UI only listens on the eth0 address.
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
