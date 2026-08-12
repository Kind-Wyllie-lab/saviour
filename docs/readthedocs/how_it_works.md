# How SAVIOUR Works

## Overview

A SAVIOUR system is one **controller** talking to any number of **modules** over a PoE LAN. Every device is a Raspberry Pi 5 running the same codebase, with `/etc/saviour/config` telling it which of the two roles to boot into.

The controller is the brains of the operation: it's the PTP grandmaster (so every device's clock is disciplined to the same reference), it runs mDNS service discovery to find modules as they come online, and it hosts the web GUI you actually interact with (Flask + Socket.IO, port 5000 by default). Modules are dumber by design - each one just owns a single sensor or piece of equipment (camera, microphone, TTL I/O, etc.) and does what the controller tells it.

Commands flow controller -> module over a ZeroMQ ROUTER/DEALER socket pair (port 5555): the controller binds a ROUTER, each module connects a DEALER using its own module ID as identity, and sends a "hello" frame to register. Status and heartbeats flow the other way on a separate PUB/SUB pair (port 5556) - modules publish, the controller subscribes to everything. This is plain ZeroMQ, not MQTT or anything with brokers or persistence; if a module isn't connected when a command goes out, it doesn't get it, which is why heartbeats and reconnect logic matter as much as they do.

The recordings themselves follow a similar controller/module split. While recording, a module writes video/audio straight to its own local storage (its SD card, not the network) - the network is only carrying commands and status, never the recording itself, so a flaky wifi card or a busy switch can't drop frames. Once a segment (or the whole session) finishes, the module exports it across to the controller's NVMe drive over the Samba share described below. From there you can get at it two ways: download it straight from the GUI's Recording page (individual files, or the whole session as a zip), or connect to the controller's Samba share from your own PC and copy files off directly - the GUI download is disabled above a certain size and points you at the share instead, since a browser download isn't a sensible way to move tens of gigabytes.

None of this needs the internet. The whole point of SAVIOUR is that it works on a closed lab LAN with no cloud dependency - the sections below cover how that's actually wired together.

## Network

Every device connects to the same PoE+ switch, controller included. The controller's `eth0` gets a static IP, `10.0.0.1/16` by default (chosen in `saviour-config`, written into `/etc/saviour/config`). Modules don't get static IPs - the controller runs `dnsmasq` as a small DHCP server for the network, handing out addresses from the top half of its own /16 range to anything that plugs in.

The controller can also act as your gateway to the outside world if you want one (`GATEWAY_MODE=controller` in the config, NAT'd out through wifi or a second interface), or you can point it at an existing router, or run it fully offline. None of the recording/discovery/export machinery cares either way - it's LAN-only by design.

## Avahi (mDNS hostname resolution)

Avahi is the system mDNS daemon (the same one most Linux desktops use for network discovery), and on SAVIOUR its job is narrow: making `saviour.local` resolve to the controller's IP from any device on the same LAN, so you can type that into a browser instead of hunting for an IP address. `saviour-config` sets `host-name=saviour` in `/etc/avahi/avahi-daemon.conf` and restricts it to `eth0` only.

That's genuinely all avahi does here - it is *not* how modules and the controller find each other. That's a separate mechanism, covered next, and it's worth keeping the two straight because they're easy to conflate.

## Zeroconf (service discovery)

Module <-> controller discovery is handled entirely by `python-zeroconf`, a Python library implementing mDNS/DNS-SD directly (it doesn't touch avahi at all, even though they're both speaking the same underlying protocol). Both sides register their own service type and browse for the other's:

- The controller registers itself as `_controller._tcp.local.` and browses for `_module._tcp.local.`.
- Each module registers itself as `_module._tcp.local.` and browses for `_controller._tcp.local.`.

The advertised service carries properties in its TXT record - a module's registration includes its `type`, `id`, `name`, `group` and current software `version`, so the controller can build a full picture of what just appeared on the network (IP and port come from the mDNS packet itself) without a separate handshake. This is also why a module rebooting with new firmware shows its updated version on the Dashboard automatically once it re-announces - no polling required.

## Samba Shares

Exporting recordings off a module and onto the controller is done over Samba (CIFS), mounted by the module at `/mnt/export`. The controller runs `smbd` serving a `controller_share`, with three separate accounts: `saviour_module` (a machine account every module authenticates as, password regenerated automatically), and `saviour_user`/`saviour_admin` for a researcher connecting from their own laptop to grab files.

Getting a file onto the share safely, without ever losing a partially-copied recording, is the fiddly part. Each file is renamed locally to `PENDING_<filename>`, copied across to the share (also as `PENDING_<filename>`), `fsync`'d, then atomically renamed to its final name on both sides - only then is the local copy moved into an `exported/` folder. If anything fails partway through, the `PENDING_` rename is rolled back locally, so the file is always either safely on the controller or still sitting untouched on the module, never in a state where it looks exported but isn't.

## systemd

Everything runs under systemd. `saviour.service` is what actually launches the controller or module Python process - `saviour-config` writes its `WorkingDirectory`/`ExecStart` to point at the right entrypoint for whatever role and type you've configured, and it's set to restart automatically if the process dies.

PTP sync (the thing that makes every camera's timestamps comparable to the millisecond) runs as its own pair of services, `ptp4l.service` and `phc2sys.service`, independent of `saviour.service` - `ptp4l` disciplines a hardware clock, `phc2sys` disciplines the system clock from it, and they keep running even if you restart the SAVIOUR process itself.

One small logging detail worth knowing if you're ever reading logs directly rather than through the web UI: when running under systemd, log lines don't carry their own timestamp, because journald already timestamps every entry it receives (`journalctl` shows both). Run the same code manually outside systemd and you'll see timestamps reappear in the log lines themselves - it's not a bug, it's just avoiding printing the same information twice.

## Picamera2

Every camera-type module (`camera`, `apa_camera`, `loom_camera`) is built on Picamera2, the current Raspberry Pi camera stack, wrapped in a shared `CameraBase` class that owns the whole lifecycle: opening the sensor, configuring resolution/framerate/sensor mode, the MJPEG live-preview stream you see on the Dashboard, and segmented recording to disk.

Recording isn't one long file - it's split into segments (so a multi-hour session isn't one enormous, hard-to-recover video), and every segment gets its own timestamp CSV sidecar recording the real capture time of each frame. That per-frame timestamp is what makes multi-camera alignment possible later - frame *index* alone isn't reliable enough once you're comparing cameras that may drift by a frame or two over a long session.

`CameraBase` itself is relatively recent - the three camera module types each used to be their own ~1200-line, independently-maintained implementation of the same Picamera2 plumbing, which meant a bug fixed in one didn't get fixed in the other two. They're now thin subclasses (as little as 47 lines) of one shared base, which is also why cross-cutting camera features - FrameSync, rotation, sensor-mode switching - only need to be written once.
