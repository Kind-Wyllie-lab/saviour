# Getting Started

## Assigning a device role

1. Flash the SD card and boot the Raspberry Pi 5 on the PoE network.
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
