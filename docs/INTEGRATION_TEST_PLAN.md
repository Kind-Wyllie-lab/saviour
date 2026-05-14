# SAVIOUR — Integration Test Plan

**Purpose:** A structured checklist for validating a newly set up SAVIOUR system before use in experiments. Work through the sections in order — each layer depends on the one before it.

**Reference setup:** controller + PoE switch + 2 × camera modules + 1 × microphone module + 1 × TTL module + desktop workstation.  
Adjust module counts to match your deployment.

**How to use:** Print or open alongside the system. Mark each item ✓ (pass), ✗ (fail), or N/A. Record any failure notes inline. A system is ready for use when all applicable items are ✓.

---

## Section 1 — Physical / Network Layer

> Complete before opening the GUI. These failures are cabling and power issues, not software.

| # | Check | Method | Pass |
|---|-------|--------|------|
| 1.1 | All modules show solid power LEDs; none are reboot-looping | Visual inspection | |
| 1.2 | All occupied PoE switch ports show link activity | Switch LED panel | |
| 1.3 | Controller reachable from desktop by mDNS name | `ping saviour.local` → reply < 5 ms | |
| 1.4 | Controller reachable by IP (rules out mDNS-only failure) | `ping <controller-ip>` | |
| 1.5 | Each module reachable from desktop | `ping <module-id>.local` for each | |
| 1.6 | PTP converging on controller | `sudo journalctl -u ptp4l -n 50` — offset < ±1 µs within 60 s of boot | |
| 1.7 | PTP converging on each module | Same command on each module — offset < ±1 µs | |

**Failure notes:**

---

## Section 2 — Web Interface

| # | Check | Method | Pass |
|---|-------|--------|------|
| 2.1 | Dashboard loads from desktop | `http://saviour.local:5000` — page renders within 3 s | |
| 2.2 | All modules appear in module list | Dashboard — correct count, correct names and IPs | |
| 2.3 | No modules stuck on "Offline" at boot | Dashboard — wait 60 s after all modules powered | |
| 2.4 | Heartbeats updating for all modules | Dashboard — watch "last seen" for 90 s; each module ticks over every ~30 s | |
| 2.5 | Config card populates for each module | Open each module's config card — all fields show values, none blank | |
| 2.6 | GUI responsive to config edits | Change a value in any field — form updates without page reload | |

**Failure notes:**

---

## Section 3 — Per-Module Hardware

### 3A — Camera modules (repeat for each camera)

| # | Check | Method | Pass |
|---|-------|--------|------|
| 3A.1 | Livestream visible and live (not frozen) | Config card — wave hand in front of camera, image moves | |
| 3A.2 | Correct resolution and FPS displayed | Config card resolution/FPS fields match hardware config | |
| 3A.3 | Timestamp overlay present | Livestream — timestamp text visible in corner | |
| 3A.4 | Timestamp matches desktop clock | Compare overlay time to system clock — within 1 s | |
| 3A.5 | Flip/monochrome settings apply live | Toggle monochrome in config card — livestream updates immediately | |
| 3A.6 | Sensor modes loaded | Config card sensor mode dropdown populated (not "Sensor modes not yet loaded") | |

**Camera notes:**

---

### 3B — Microphone module

| # | Check | Method | Pass |
|---|-------|--------|------|
| 3B.1 | Monitoring spectrogram visible | Config card — spectrogram/waveform renders | |
| 3B.2 | AudioMoth sample rate correct | Module log — device listed as `<rate>kHz AudioMoth USB Microphone` | |
| 3B.3 | Usable bandwidth matches sample rate | Spectrogram — signal present up to ~5 kHz (48 kHz), ~20 kHz (96 kHz), ~70 kHz (192 kHz); no higher | |
| 3B.4 | Microphone is live | Clap near mic — broadband spike visible in spectrogram | |
| 3B.5 | Freq range controls reflect Nyquist | Set freq_hi above Nyquist — warning appears; Save blocked until resolved | |

**Microphone notes:**

---

### 3C — TTL module

| # | Check | Method | Pass |
|---|-------|--------|------|
| 3C.1 | Module appears online | Dashboard — TTL module shows as online | |
| 3C.2 | Output pin responds to test pulse | Send test pulse from GUI — measure output pin with multimeter or scope | |
| 3C.3 | Input events logged (if applicable) | Apply test signal to input pin — event appears in module log | |

**TTL notes:**

---

## Section 4 — Configuration Persistence

> Verifies that saved settings survive a module restart. Failure here means `active_config.json` is not being written or is being ignored on startup.

| # | Check | Method | Pass |
|---|-------|--------|------|
| 4.1 | Change a value on each module | Camera: adjust brightness. Mic: adjust gain. TTL: any user-settable field | |
| 4.2 | Save confirmed for each module | "Saved" badge appears after clicking Save | |
| 4.3 | Reboot one module | Reboot button in GUI or `sudo reboot` on module | |
| 4.4 | Module reappears after reboot | Module back online in dashboard within 60 s | |
| 4.5 | Changed values still present after reboot | Open config card — values match what was saved, not reverted to default | |
| 4.6 | Active config exported on NAS | After any recording, NAS session folder contains `config.json` (not empty, not base config) | |

**Config persistence notes:**

---

## Section 5 — Recording

### 5A — Baseline recording (~30 seconds)

| # | Check | Method | Pass |
|---|-------|--------|------|
| 5A.1 | Session name set | Enter a test session name in the GUI | |
| 5A.2 | All modules enter Recording state simultaneously | Start recording — all modules show "Recording" within 1 s of each other | |
| 5A.3 | All modules stop simultaneously | Stop recording — all modules stop within 1 s of each other | |
| 5A.4 | NAS folder created with correct structure | Browse NAS share — `<session>/<date>/<module-name>/` exists for all modules | |
| 5A.5 | Video files present for each camera | NAS — `.mp4` or `.h264` file present, size > 1 MB | |
| 5A.6 | FLAC file present for microphone | NAS — `.flac` file present, size consistent with duration and sample rate (~70–100 MB per 30 s at 192 kHz) | |
| 5A.7 | TTL log present | NAS — TTL output file present | |
| 5A.8 | Config file present per module | NAS — `config.json` in each module folder | |
| 5A.9 | Video files play without corruption | Open in VLC or similar — no decoding errors, smooth playback | |
| 5A.10 | FLAC audible and correct sample rate | Open in Audacity — correct sample rate shown, audio content present | |

**Baseline recording notes:**

---

### 5B — Timestamp alignment

> Requires a visible synchronisation event (hand clap in front of both cameras, or a brief LED connected to the TTL output).

| # | Check | Method | Pass |
|---|-------|--------|------|
| 5B.1 | Visible event appears on same frame in both cameras | Step through video frame-by-frame; event frame numbers match ± 1 frame | |
| 5B.2 | TTL pulse aligns with video (if applicable) | TTL timestamp vs frame timestamp at event — within one frame period | |
| 5B.3 | Audio and video approximately aligned (if applicable) | Clap visible in video and as transient in FLAC — within 50 ms | |

> If camera frames are offset by more than 1–2 frames, PTP is not locking correctly on one of the modules. Check `ptp4l` logs on each device.

**Alignment notes:**

---

### 5C — Extended recording (~10 minutes)

| # | Check | Method | Pass |
|---|-------|--------|------|
| 5C.1 | All modules record for full duration | Stop recording — file sizes scale linearly with duration (compare to 30 s baseline) | |
| 5C.2 | No recording thread crashes | Module logs — no `IndexError`, `RuntimeError`, or thread exception during recording | |
| 5C.3 | Monitoring stream (mic) remains live throughout | Microphone config card — spectrogram still updating at end of recording | |
| 5C.4 | No `PENDING_*` files left on module | SSH to each module — `ls /var/lib/saviour/recordings/to_export/` — should be empty after export | |

**Extended recording notes:**

---

## Section 6 — Export and NAS

| # | Check | Method | Pass |
|---|-------|--------|------|
| 6.1 | Samba mount succeeds on all modules | Module logs — `Successfully mounted controller share` for each module | |
| 6.2 | No persistent `PENDING_*` files | `ls /var/lib/saviour/recordings/to_export/` on each module — empty after export completes | |
| 6.3 | File sizes on NAS are plausible | 30 s 1080p30 H.264 at 10 Mbps ≈ 37 MB; 30 s FLAC at 192 kHz ≈ 70–100 MB | |
| 6.4 | Export retry visible on transient failure | Stop/start `smbd` on controller mid-recording; check module logs for retry attempts | |

**Export notes:**

---

## Section 7 — Failure and Recovery

> These are the scenarios most likely to cause silent data loss in a real experiment. Test at least 7.1 and 7.2 before first use.

### 7.1 — Module dropout mid-recording

1. Start a recording with all modules
2. Unplug one camera's ethernet cable mid-recording
3. Wait 90 s — GUI should mark the module as Offline
4. Plug cable back in — module should reappear and reconnect
5. Stop the recording

| # | Check | Pass |
|---|-------|------|
| 7.1a | Disconnected module marked Offline after ~90 s | |
| 7.1b | Remaining modules continue recording unaffected | |
| 7.1c | Disconnected module reappears after reconnect | |
| 7.1d | Other modules export cleanly after stop | |
| 7.1e | Dropped module has partial recording file (not zero bytes) | |

---

### 7.2 — NAS unavailable at recording start

1. Stop Samba on the controller: `sudo systemctl stop smbd`
2. Start and stop a short (~30 s) recording
3. Restart Samba: `sudo systemctl start smbd`
4. Wait for export retry

| # | Check | Pass |
|---|-------|------|
| 7.2a | Module logs show mount failure and retry attempts | |
| 7.2b | After Samba restarts, export completes (files appear on NAS) | |
| 7.2c | No files stranded in `to_export/` after successful retry | |

---

### 7.3 — Controller restart

1. Start a recording
2. Restart the controller (`sudo reboot`)
3. Observe module behaviour

| # | Check | Pass |
|---|-------|------|
| 7.3a | Modules stop recording when ZMQ connection drops | |
| 7.3b | Modules reappear in dashboard after controller comes back | |
| 7.3c | Files recorded up to disconnect are present and playable | |

> **Note:** Modules do not currently resume a recording after a controller restart. This is expected behaviour — plan experiments accordingly.

---

## Section 8 — Sign-off

| Item | Value |
|------|-------|
| Date tested | |
| Tester | |
| Controller version | |
| Module firmware versions | |
| Hardware configuration | |
| Any open failures | |
| System approved for use | ☐ Yes ☐ No — pending: |

---

## Quick reference: common failure modes

| Symptom | Likely cause | Where to look |
|---------|-------------|---------------|
| Module stuck Offline at boot | ZMQ port blocked, wrong `MODULE_CMD_PORT` env var | Module log, `active_config.json` on module |
| `saviour.local` not resolving | avahi not running, or mDNS blocked by Windows firewall on PoE NIC | `sudo systemctl status avahi-daemon` on controller |
| PTP offset never converges | Wrong NIC in `ptp4l.conf`, UDP 319/320 blocked by switch | `sudo journalctl -u ptp4l` on controller and module |
| Config reverts after reboot | `active_config.json` not writable, or wrong path | Check `save_active()` log line on module startup |
| FLAC missing from NAS | Recording thread crashed (AudioMoth USB ID stale) | Module log — look for `IndexError: no soundcard with id` |
| `PENDING_*` file left on module | Samba copy failed and rollback ran | Module export log, NAS connectivity |
| Videos timestamp-misaligned | PTP not locked on one module | `ptp4l` offset on the drifting module |
| Export never completes | Samba credentials wrong or `smbd` not running | `sudo systemctl status smbd` on controller |
