# A/V sync test: buzzer + LED

Measures how well AudioMoth audio lines up with camera video, against a
ground truth the system creates itself: the microphone module fires a piezo
buzzer and an LED from its own GPIO and stamps each edge on the
PTP-disciplined clock. Two tests use the same rig:

| Test | Needs | Answers |
|---|---|---|
| **Self-test** (`sync_selftest`) | buzzer near the AudioMoth(s); no session | How late (or early) does the aligner place a sound relative to when it happened? Per AudioMoth, in ms, with its spread. Also **calibrates** each AudioMoth. |
| **Session check** (`sync_pulses` + `tools/av_sync_check.py`) | buzzer + LED in view of every camera; a normal session | Where do the same instant's sound and flash land in every stream, and how far apart are they in an aligned composite? |

Background and the measurements so far: `plans/audio-video-sync-residual-validation.md`.

## Wiring (Raspberry Pi 5, BCM numbering)

GPIO pins drive 3.3 V at a few mA, so:

- **LED**: GPIO → 330 Ω → LED → GND. Fine directly from the pin.
- **Active buzzer** (has its own oscillator, buzzes on DC; most 3-5 V "piezo
  buzzer" modules): usually draws 20-30 mA, more than a GPIO pin should
  supply. Use an NPN transistor (2N2222 / BC547): GPIO → 1 kΩ → base;
  emitter → GND; buzzer between 5 V and the collector. `drive: "dc"`.
- **Passive buzzer** (no oscillator, clicks on DC): drive it with a tone,
  `drive: "pwm"`, `tone_hz` near its resonance (often 2-4 kHz). Small
  12 mm "5 V passive buzzers" (e.g. The Pi Hut 5V Buzzer, YMD-12095-G,
  30 mA) are **magnetic**, a coil: use the transistor above at 5 V **and a
  flyback diode** (1N4148 / 1N4001) across the buzzer, stripe to 5 V.
  Only a bare piezo disc (no coil, a few mA) can go straight on the pin.
- **LED resistor**: use one (330 Ω for red/green, about 4 mA). Without it the
  GPIO driver is the only current limit and the pin is pushed past its
  rating. Dimmer is better here anyway: a very bright LED blooms and blurs
  the onset. Avoid blue/white on 3.3 V (about 3 V forward drop: dim and
  inconsistent).

Suggested pins: buzzer GPIO17 (header pin 11), LED GPIO27 (pin 13), GND
pin 9. One pin can drive both (set `led_pin` = `buzzer_pin`, or wire the LED
across the buzzer).

### Ultrasonic buzz (no audible beeps)

A bare piezo (PS1240) driven at **25 kHz** is silent to people and louder on
an AudioMoth than its 4 kHz resonance (desk rig, buzzer a few cm away, band
level over the noise: 22 dB at 4 kHz, ~50 dB at 16-25 kHz, 26 dB at 30 kHz,
38 dB at 50 kHz). gpiozero's software PWM stops at 10 kHz, so give the
buzzer pin a hardware-timed PIO PWM channel (Pi 5, any GPIO 0-27), then set
the tone:

```bash
# on the microphone Pi, once
echo "dtoverlay=pwm-pio,gpio=17" | sudo tee -a /boot/firmware/config.txt
sudo reboot
# then
curl -X PATCH "http://10.0.0.1:5000/api/v1/modules/microphone_4703/config?wait=15" \
  -H "Authorization: Bearer $PW" -H "Content-Type: application/json" \
  -d '{"sync_pulse": {"drive": "pwm", "tone_hz": 25000}}'
```

`drive: "pwm"` uses the PIO channel whenever the pin has one (the result's
`pwm_backend` says `"pio"`), at any `tone_hz`; a tone above 10 kHz without
it is refused with the overlay line to add. The gating still makes a faint
tick at each pulse's start and end. **Rats and mice hear 25 kHz** (it's in
their most sensitive range): run the self-test with no animals near, and
think twice before `sync_pulses` during a session with animals in. The
piezo starts faster at 25 kHz than at 4 kHz, so **recalibrate** (run the
self-test twice) after changing the tone.

Placement:

- Buzzer close to the AudioMoth, and measure the distance: sound takes
  ~2.9 ms per metre. Put it in `mic_distance_m` and it is subtracted.
- LED in view of every camera, ideally near the **top** of the frame (the
  sensor reads rows top to bottom, so a low LED adds up to one readout time),
  and not so bright it blooms. Manual exposure makes the numbers steadier.
- If the AudioMoth has a hardware high-pass set (`audiomoth.filter_type`),
  a 2-4 kHz buzz may be filtered out. Use `filter_type: "none"` for the test.

## Configure

```bash
curl -X PATCH "http://10.0.0.1:5000/api/v1/modules/microphone_4703/config?wait=15" \
  -H "Authorization: Bearer $PW" -H "Content-Type: application/json" \
  -d '{"sync_pulse": {"buzzer_pin": 17, "led_pin": 27, "drive": "dc", "mic_distance_m": 0.10}}'
```

| Key (`sync_pulse.*`) | Default | |
|---|---|---|
| `buzzer_pin` | `null` | BCM pin; `null` = no buzzer |
| `led_pin` | `null` | BCM pin; `null` = no LED |
| `drive` | `"dc"` | `"dc"` active buzzer, `"pwm"` passive piezo |
| `tone_hz` | `4000` | tone for `"pwm"`; above 10 kHz needs the PIO overlay (below) |
| `pulse_ms` | `50` | default pulse length |
| `mic_distance_m` | `0.0` | buzzer-to-mic distance, removes flight time |

## Self-test (no session)

```bash
curl -X POST http://10.0.0.1:5000/api/v1/modules/microphone_4703/sync_selftest \
  -H "Authorization: Bearer $PW" -H "Content-Type: application/json" \
  -d '{"pulses": 20, "interval_s": 1.2}'
# ~30 s later:
curl -H "Authorization: Bearer $PW" http://10.0.0.1:5000/api/v1/modules/microphone_4703/sync_selftest
```

The module records every AudioMoth to a scratch folder through the same
recorder code as a real segment (never exported), fires the buzzes at
jittered intervals, finds each onset and converts it to wall time with
exactly the anchor the aligner uses (`audio_align.parse_mic_sidecar`).
The result (also an SSE `sync_selftest_result` event and a controller log
line) has, per AudioMoth: `mean_ms`, `median_ms`, `std_ms`, min/max, each
pulse's offset and SNR, the clock-fit quality, and the calibration (below).
**Positive = the audio places the sound after it happened.** Refused while
recording (use the session check instead).

**From the web UI:** Settings → the microphone module → **Sync** tab. It
shows each AudioMoth's calibration ("Calibrated 3 days ago", amber when over
30 days old or measured at a different sample rate or block size, "Not
calibrated" otherwise; the tab gets a ⚠ when a connected AudioMoth needs
one), a **Run self-test** button (signed in, module not recording) with each
AudioMoth's result, and the buzzer/LED settings. `sync_pulses` is not
offered there and is refused from the web UI: it fires into a running
session, so it stays a deliberate REST call.

### How audio is anchored, and what the self-test calibrates

Every recording's sidecar has a `LAT` line per block: PipeWire's reported
stream latency (`microphone.latency_probe`, on by default). The aligner
(`audio_align.parse_mic_sidecar`) places sample 0 from those, which is steady
to ~0.1 ms between recordings, then subtracts a fixed **per-AudioMoth
offset**. Recordings without `LAT` lines (before 2026-11-06) fall back to
`STARTED`, about 20 ms late, ±5 ms per recording.

The offset is what the self-test measures. A run that is clean for an
AudioMoth (≥ 75 % of pulses found, median SNR ≥ 25 dB, offsets within a
1 ms MAD) stores it in the module's `audiomoth_sync_offsets_ms` (serial →
ms, with when and under which sample rate, block size and tone), and each
later recording copies it into its sidecar (`LATENCY_OFFSET_MS`). The result shows `anchor`, the `latency_offset_ms`
in force, the `measured_offset_ms`, and `calibration_skipped` with the
reason when a run doesn't qualify. An uncalibrated AudioMoth gets a default
(−4.8 ms), good to about ±2 ms. Pass `"calibrate": false` to measure only.

**When to run it:** once per AudioMoth, buzzer a few cm away, before it's
deployed (on the bench is fine, the offset belongs to the AudioMoth); again
after changing its firmware, sample rate, block size or filter, a kernel or
PipeWire update, or the Pi/USB hub it hangs off; and a quick check at the
start of an experiment campaign. Measured 2026-11-06 on the desk rig: the
offsets hold to 0.1 ms across a reboot and move 0.3-0.5 ms under heavy
CPU/disk load. After a calibration, a second run should read `median_ms`
near 0.

### External microphones

An AudioMoth with an external mic records through it (SAVIOUR switches it
over; the firmware also does when one is plugged in). The self-test moves it
to its **internal** mic for the run, since the buzzer sits next to the
AudioMoth, and back afterwards; results and stored offsets carry `input`.
To measure on the external mic instead, pass `"mic_input": "current"` (each
unit stays on its active mic) and, if the mic isn't next to the buzzer,
`"external_distance_m"` to remove its flight time (~2.9 ms per metre).
External-mic results are never stored as the calibration.

**The internal-mic offset holds on the external mic.** Measured 2026-11-11
on the desk rig: an EM258 on AudioMoth `2474750264200FAD`, the same distance
from the buzzer as the internal mic, so no flight-time correction was
needed. Four measure-only runs of 10 pulses, alternating inputs, with the
second AudioMoth (internal throughout) as a control:

| Run | `2474…0FAD` input | median ms | control median ms |
|---|---|---|---|
| 1 | internal | +0.24 | −0.08 |
| 2 | EM258 (external) | −0.18 | −0.16 |
| 3 | internal | −0.20 | +0.11 |
| 4 | EM258 (external) | −0.28 | −0.34 |

10/10 pulses found every run, sd 0.2-0.8 ms, SNR ~32-34 dB. The external
runs read ~0.2 ms earlier, the same size as the control's run-to-run drift,
so the two analog front ends agree within what the test resolves and one
calibration (taken on the internal mic) serves both. Checked with one mic
type (EM258) on one unit.

## Session check (cameras too)

1. Start a session with the cameras and the microphone module.
2. Fire pulses while it records:
   ```bash
   curl -X POST http://10.0.0.1:5000/api/v1/sessions/<session>/sync_pulses \
     -H "Authorization: Bearer $PW" -H "Content-Type: application/json" \
     -d '{"count": 30, "interval_s": 2}'
   ```
   Each pulse is logged to `<session>_..._sync_pulses_(<seg>_<utc>).csv`,
   which exports with the session.
3. Stop the session, let it export, then on the controller:
   ```bash
   cd /usr/local/src/saviour
   env/bin/python tools/av_sync_check.py /home/pi/controller_share/<session> \
       --out /tmp/av_sync.json --csv /tmp/av_sync_pulses.csv
   ```

It prints three blocks:

- **Audio onset − edge**, per AudioMoth: same as the self-test, measured
  inside a real session.
- **First lit frame − edge**, per camera: the timestamp of the first frame
  showing the LED. Because a pulse lands at a random point in a frame, this
  spreads over about one frame period. The **lit-fraction fit** says where
  the frame timestamp sits relative to the light: `slope` should come out
  near the exposure time (a check that detection works), and `intercept`
  is about −exposure if timestamps mark the start of exposure, about 0 if
  they mark the end.
- **Audio − video**, per mic/camera pair: how far apart the sound and the
  flash of the same instant are when the streams are aligned by their own
  timestamps, which is what compose does. Its mean is the correctable part;
  its spread (mostly the frame period) is the floor for that frame rate.

## Limits

- Edge times are stamped by Python around the GPIO write (the bracket width
  is reported as `edge_spread_us_max`, typically tens of µs).
- `STARTED` (the fallback anchor) varies ±5 ms per recording: the audio
  stack's latency is fixed for a stream's life and differs on each open.
  The latency anchor measures it; without `LAT` lines nothing can.
- Buzzers take a millisecond or two to start sounding after the edge; that
  is part of what is measured (an LED is effectively instant), so it ends
  up in each AudioMoth's calibrated offset. It is the same for every
  AudioMoth calibrated with the same buzzer.
- The video side assumes frame `i` of the `.ts` is row `i` of the timestamp
  CSV. The tool warns when the counts differ (the sync-client caveat in
  CLAUDE.md).
