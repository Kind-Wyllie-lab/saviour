# A/V sync test: buzzer + LED

Measures how well AudioMoth audio lines up with camera video, against a
ground truth the system creates itself: the microphone module fires a piezo
buzzer and an LED from its own GPIO and stamps each edge on the
PTP-disciplined clock. Two tests use the same rig:

| Test | Needs | Answers |
|---|---|---|
| **Self-test** (`sync_selftest`) | buzzer near the AudioMoth(s); no session | How late (or early) does the aligner place a sound relative to when it happened? Per AudioMoth, in ms, with its spread. |
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
| `tone_hz` | `4000` | tone for `"pwm"` |
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
pulse's offset and SNR, and the clock-fit quality. **Positive = the audio
places the sound after it happened.** Refused while recording (use the
session check instead).

Run it several times, across a reboot and on another day: if `mean_ms` is
stable for an AudioMoth (spread well under 10 ms between runs), it is a
per-device constant that can be subtracted in `parse_mic_sidecar`. If it
moves, calibration alone won't fix it (see the plan's Phase C).

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
- `STARTED` (the audio anchor) is stamped in Python just after the
  AudioMoth stream opens, so a thread switch there (up to ~5 ms) shifts that
  recording's anchor. The run-to-run spread of the self-test includes it.
- Buzzers take a millisecond or two to start sounding after the edge; that
  is part of what is measured (an LED is effectively instant).
- The video side assumes frame `i` of the `.ts` is row `i` of the timestamp
  CSV. The tool warns when the counts differ (the sync-client caveat in
  CLAUDE.md).
