# Getting Started

## Before you start

This guide assumes:

- You have the hardware together: a PoE+ switch, one controller, and one or more modules, all on the same network. See [Hardware](hardware.md) for what to buy and how to size your switch.
- Every device already has Raspberry Pi OS flashed to its SD card (or NVMe, for the controller) and boots normally - a plain [Raspberry Pi Imager](https://www.raspberrypi.com/software/) install, nothing SAVIOUR-specific needed at that stage.
- SAVIOUR itself is installed on each device. If it isn't yet, see below.

### Installing SAVIOUR

On a fresh Raspberry Pi OS install, run:

```
curl -fsSL https://raw.githubusercontent.com/Kind-Wyllie-lab/saviour/main/install.sh | bash
```

This clones the repo to `/usr/local/src/saviour` and runs `setup.sh`, which installs everything SAVIOUR needs (PTP, ffmpeg, Picamera2, Samba, etc.). It doesn't assign a role by itself - that's the next step, below.

Doing this one device at a time works fine but is slow for a big rig - once you've got one device fully configured, it's usually faster to clone its SD card/NVMe image onto the rest instead (`scripts/multiclone.sh` in the repo).

## Assigning a device role

1. Boot the device on the PoE network (SAVIOUR should already be installed - see above).
2. Run `sudo saviour-config` on the device.
3. Choose Controller (one per system) or Module, then pick the module type (camera, microphone, TTL, RFID, ...).
4. The device reboots into its assigned role and appears automatically once discovered.

## Connecting modules to the controller

1. Power on the controller first - it acts as the PTP grandmaster and service discovery hub.
2. Power on modules; they register over mDNS and appear on the Dashboard within a few seconds.
3. Check the System page to confirm every module shows a recent heartbeat and a locked PTP offset before recording.

## Running a recording session

1. Configure each module on the Settings page (resolution, sample rate, etc.) before starting.
2. On the Recording page, click "Check Ready" to confirm PTP sync is within threshold on every module.
3. Start the session, and stop it (or let a scheduled window end it) once you're done.
4. Recordings export automatically to the controller's share once each module finishes.

## Exporting and retrieving data

1. Recordings land on the controller's Samba share, organised by session name and date.
2. Connect to the share from a lab workstation to copy files off, or point analysis tools at it directly.
3. Use `tools/analyse_framesync.py` and `tools/make_aligned_video.py` to check multi-camera timing and build aligned review videos.

## Troubleshooting a module

1. A module marked offline usually means its heartbeat timed out - check power, PoE link and network cabling first.
2. The System page shows per-module CPU, disk, temperature and PTP offset - a drifting PTP offset points to a clock sync problem, not a recording bug.
3. Reboot or shut down an individual module from its actions menu on the System page if it needs a clean restart.
