# System → Network tab

- **Status:** proposed
- **Created:** 2026-11-10
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX". Sibling of the System →
  Sync tab (shipped 2026-11-10, `src/controller/sync_overview.py`).

## Why

Nothing in the UI shows the rig's network: what the controller's eth0 looks
like, what else is plugged in, or whether a module's link is degraded. The
`HARDWARE_NOTES` PTP-topology problems (extra switch hops, a module on a
100 Mb link, bulk traffic on the PTP path) are only found today by SSHing in.

**Every device the controller has handed a DHCP lease to belongs on this
page, not only registered modules.** The controller is the rig's DHCP server,
so its leases also cover unprovisioned or re-imaged Pis
(`saviour-unprov-*`), the user's PC or laptop, the NAS/share, and anything
else plugged into the switch. Those are exactly the hosts nobody can see
right now, and a stray device on the PTP switch is a real fault.

## What it shows

1. **Controller interfaces:** eth0 address/subnet, the DHCP range and lease
   time (find where `saviour-config`/`setup.sh` configure dnsmasq or the NM
   shared connection, and read it from there, not hard-coded), gateway/DNS if
   any, wlan0 and Tailscale state, and the existing `firewall_status()`.
2. **Hosts table:** the **union** of the dnsmasq leases (`/var/lib/misc/dnsmasq.leases`:
   expiry, MAC, IP, hostname, client-id), the mDNS-discovered modules
   (`Modules`), and the configured share host. Each row is classed:
   - *module*: matched to a registered module by IP (or MAC once modules
     report it);
   - *unprovisioned SAVIOUR Pi*: hostname `saviour-unprov-*`, or a Raspberry
     Pi OUI with no registered module;
   - *share / NAS*: the export target;
   - *other*: everything else, shown with hostname + vendor (from the MAC OUI)
     so "Andrew's laptop" vs "unknown device" is obvious;
   - *module not in leases*: a registered module with a static IP or an
     expired lease, flagged.
   Expired-but-recent leases are shown greyed out ("last seen"), not dropped.
3. **Per-host health:**
   - round-trip time + loss from a supervised controller thread that pings
     every lease/module IP every ~10 s (`ping -c1 -W1`, or `fping` across all
     hosts in one call if it's available; keep the history short, ~1 h);
   - for modules only, heartbeat arrival gap/jitter. It's free and a good
     proxy for ZMQ link health;
   - for modules only, new heartbeat fields read from `/sys/class/net/eth0/`:
     `speed`, `duplex`, `carrier_changes` and rx/tx `errors`/`dropped`
     deltas. Flag < 1000 Mb/s, half duplex, and climbing errors;
   - share reachability (SMB port) and current export throughput.

## Phasing

1. Backend `network_overview.py` (pure functions like `sync_overview.py`):
   lease parsing, host merge/classification, interface summary. Socket event
   `get_network_overview`, guest-readable. Tests with fixture lease files.
2. Ping thread (supervised, eth0 only). Run it always rather than only while
   the tab is open: it's ~20 pings per 10 s, and the history is then there
   when someone opens the page.
3. Module heartbeat link counters (`ModuleHealthSnapshot` fields; old modules
   send none, so the UI shows "–").
4. Frontend `NetworkPanel.jsx` as a third System tab, plus the mock fixture
   (include an unprovisioned Pi, a laptop and an expired lease).

## Open questions

- MAC→vendor lookup: ship a trimmed OUI list (Raspberry Pi, common NIC
  vendors) rather than the full IEEE file.
- Should an unknown non-module host on the rig switch raise a fault alert, or
  just show on the page? Start with the page only.
- Optional `GET /api/v1/network` for external tooling, mirroring the socket payload.
