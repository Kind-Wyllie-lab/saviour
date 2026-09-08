# Firewall: scope every service to the interface it belongs on

- **Status:** proposed
- **Created:** 2026-09-08
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Security (open)" — the "All network services
  bind `0.0.0.0`" bullet. Evolves the `fix/wlan0-firewall` work
  (commit `471eebfb`, `saviour-config` `configure_firewall()` /
  `_fw_build_family()` / the `SAVIOUR-WLAN-IN` chain).

## Why

`fix/wlan0-firewall` added a default-deny INPUT filter, but **only hooked on
the untrusted interfaces** (`wlan0`, `WAN_INTERFACE`). `lo`, `eth0` and
`tailscale0` are deliberately left wide open so Tailscale SSH / an `ssh -D`
SOCKS proxy to the UI keep working.

Consequence: **everything the controller listens on is reachable from any
tailnet node** — including the *unauthenticated* ZMQ command bus on `:5555`
(arbitrary `shutdown` / `reboot` / `start_recording` / `update_saviour`, no
password), the `:5556` status bus, `GET /update/package` (unauthenticated),
and the `:80→:5000` redirect. A compromised tailnet device has more power
over the fleet than a web-UI user does.

Nothing on the tailnet legitimately needs the ZMQ bus, Samba, the update
endpoint, or the module MJPEG ports — modules talk to the controller over
`eth0`. The web UI is the one thing you genuinely want remotely, and that
should be an explicit opt-in, gated behind
`plans/remote-access-auth-hardening.md` for the auth to be adequate.

## Target policy — service × interface

`lo` and `eth0` (the PoE LAN — physical access is already the trust boundary):
**everything**, unchanged. Optionally source-restricted to
`FIREWALL_LAN_SUBNET` if set (default unset — don't break a lab that
renumbers). `tailscale0`: only what's listed. `wlan0` / WAN: unchanged
(default-deny, `FIREWALL_WLAN_SSH` break-glass).

| service | port(s) | role | eth0 | tailscale0 | wlan0/WAN |
|---|---|---|---|---|---|
| SSH | 22/tcp | both | **always** | **always** | opt-in `FIREWALL_WLAN_SSH` |
| Web UI / REST / SSE / `/update/package` | 5000/tcp | controller | allow | **opt-in `FIREWALL_TAILSCALE_WEB`** | deny |
| `:80→:5000` redirect | 80/tcp | controller | allow | deny | deny |
| ZMQ ROUTER (commands) | 5555/tcp | controller | allow | **deny** | deny |
| ZMQ PUB (status) | 5556/tcp | controller | allow | **deny** | deny |
| Samba | 139,445/tcp; 137,138/udp | controller | allow (already `interfaces = lo eth0`) | deny | deny |
| DHCP server | 67/udp | controller | allow | deny | deny |
| MJPEG streams + `/roi*` | 8080–8083/tcp | module | allow (→ controller IP only if `FIREWALL_LAN_SUBNET` set) | **deny** | deny |
| PTP event/general | 319,320/udp (mcast) + IGMP | both | allow | n/a | deny |
| mDNS / avahi | 5353/udp | both | allow | allow | deny |
| Tailscale direct path | 41641/udp | both | n/a | accepted (already) | already |

Modules: the DEALER **connects out** to the controller's `:5555`, so nothing
ZMQ *listens* on a module — the module-side rules are only about the MJPEG /
`/roi` ports and SSH.

## Phase 1 — scope `tailscale0`, extend to modules, `eth0` subnet option

Small delta on top of `fix/wlan0-firewall`; ships the actual fix (closes the
ZMQ-and-friends-over-Tailscale hole) without a full host-firewall rewrite.

1. **Generalise the chain.** `SAVIOUR-WLAN-IN` → per-purpose chains:
   `SAVIOUR-TS-IN` (hooked `-i tailscale0`) and keep `SAVIOUR-WLAN-IN`.
   `SAVIOUR-TS-IN`: accept ESTABLISHED/RELATED, ICMP/ICMPv6, `udp --dport
   41641`, `tcp --dport 22`, `tcp --dport 5000` **iff `FIREWALL_TAILSCALE_WEB`
   = yes**, then **DROP**. `eth0` and `lo` still never hooked.
2. **`FIREWALL_TAILSCALE_WEB`** in `/etc/saviour/config`. Migration: on the
   first `configure_firewall()` run where a `tailscale0` (or `tailscale*`)
   interface exists and the key is unset → default it to `yes` and log a
   loud notice (**preserves** today's "GUI over Tailscale works" behaviour);
   `setup.sh` prompts on a fresh install (default no). `saviour-config`'s
   network menu gets a toggle.
3. **Run on the module role too.** `configure_firewall()` / `disable_firewall()`
   currently only fire on the controller path. Add them to the module
   provision path + `mend.sh`. Module `SAVIOUR-TS-IN` = SSH + Tailscale
   path only (no `:5000`); a `SAVIOUR-LAN-IN` hooked `-i eth0` that, **only
   when `FIREWALL_LAN_SUBNET` is set**, drops 8080–8083 / `/roi` from
   sources outside that subnet (so a rogue device on the PoE LAN can't scrape
   a module's live feed even before `plans/mjpeg-stream-auth.md` lands).
4. **`FIREWALL_LAN_SUBNET`** (e.g. `10.0.0.0/24`, default unset) — when set,
   the SAVIOUR service ports are accepted on `eth0` only from that subnet.
   Off by default.
5. Mirror everything in `ip6tables` (Tailscale is dual-stack: `100.64/10` +
   `fd7a:115c:a1e0::/48`). The existing `_fw_build_family ip6tables` path
   extends.
6. `--apply-firewall` re-applies the fuller ruleset; `mend.sh` step 9 already
   calls it; keep it flush-and-rebuild idempotent.

## Phase 2 (optional) — full default-deny INPUT

Only if there's appetite. `SAVIOUR-IN` hooked once at `INPUT 1` with no `-i`;
inside: `-i lo -j ACCEPT`, ct ESTABLISHED/RELATED, per-interface accept
blocks, final `-j DROP` — a real host firewall on both roles. Higher
assurance, higher lockout risk, and it **must** explicitly allow the things
that currently work by default: ptp4l/phc2sys UDP 319/320 multicast + IGMP on
`eth0` (clocks fail silently otherwise), avahi 5353, the DHCP **server** 67
on the controller, NDP. Needs the confirm-or-revert safety below.

## Barriers / risks

- **Lockout.** SSH on `eth0` *and* `tailscale0` is **always** accepted (never
  gated). Add `saviour-config --flush-firewall`, and a confirm-or-revert:
  `configure_firewall()` applies, drops a `/run/saviour-fw-pending` marker,
  and a 90 s timer reverts to the previous ruleset unless
  `saviour-config --confirm-firewall` (or the next successful controller
  start) clears the marker. Model on `iptables-apply` / `ufw`'s approach.
- **Extending firewall to the module role is new** — `saviour-config` only
  does it for the controller today. Provision path + `mend.sh` + the
  `--apply-firewall` entrypoint all need the role branch.
- **`FIREWALL_TAILSCALE_WEB` default on upgrade** — must default `yes` when a
  tailnet interface already exists, or every "I use the GUI over Tailscale"
  deployment breaks on the next `mend.sh`. Fresh installs default `no` +
  prompt.
- **Userspace Tailscale** (no `tun`, `--tun=userspace-networking`) has **no
  interface** to scope — detect (`ip link show tailscale0` fails) and log
  that scoping falls back entirely to Tailscale ACLs.
- **Bookworm is `iptables-nft`.** Stay on the `iptables` / `ip6tables`
  command wrappers the existing code uses; don't introduce raw `nft`.
- **PTP / mDNS on `eth0`** — Phase 1 doesn't touch `eth0` so it's fine;
  Phase 2 must whitelist them or the fleet's clocks and discovery break.
- **IPv6 unusable on a kernel** — the existing `_fw_build_family` already
  skips a broken family with a log line; keep that.
- **Testing is on-device only.** Acceptance below is `ss` + `nmap` from three
  vantage points; can't CI it.

## Acceptance

- From a **tailnet node**: `:5000` reachable iff `FIREWALL_TAILSCALE_WEB=yes`;
  `:5555`, `:5556`, `:80`, `:445`, `:8080–8083` all **refused**; `:22`
  reachable.
- From an **`eth0` host** on the PoE LAN: everything reachable (or only from
  `FIREWALL_LAN_SUBNET` if set).
- From a **`wlan0` client**: unchanged — only `:22` iff `FIREWALL_WLAN_SSH`.
- `saviour-config --apply-firewall` then a reboot then `mend.sh` — rules
  identical each time (idempotent), `netfilter-persistent` survives reboot.
- Kill SSH mid-apply / apply a bad ruleset → the 90 s revert restores access.
- PTP offset + module discovery unaffected (Phase 1); with Phase 2, verify
  `ptp4l` still locks and a fresh module still registers.
- A module: `:8080` refused from a tailnet node, allowed from the controller.

## Not doing

- ZMQ CURVE auth — separate concern, deliberately deferred (see the threat
  model in CLAUDE.md). This plan is the cheap control that makes deferring it
  defensible even with the fleet on Tailscale.
- Per-service `bind()` narrowing in the apps — firewall `-i eth0` matching is
  robust to a DHCP-changing `eth0` IP; bind-to-IP isn't. A later cleanup can
  narrow binds where the address is stable, but it's not load-bearing here.
- TLS on any service.
