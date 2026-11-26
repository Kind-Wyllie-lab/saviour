# Sound library: upload with analysis, generated stimuli, calibrated levels

- **Status:** phases 1-2 built 2026-11-26 (written up in CHANGELOG), not
  yet run in a browser; phases 3-4 not started.
- **Created:** 2026-11-26
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (one-liner)

## Why

The sound module plays only the files committed to
`src/modules/variants/sound/sounds/` (`8k.wav`, `dog_bark.wav`). Adding a
stimulus means a commit and a deploy, nothing says what a file contains
(rate, level, clipping, bandwidth), and the standard rodent stimuli (tones,
noise bursts for startle/PPI, gaps, click trains, sweeps) have to be made
elsewhere. The output test buttons (SoundConfigCard, 2026-11-26) made this
visible.

## Decisions

- **One library, on the controller.** Protocols name sounds and a rig can
  have several sound modules, so the controller holds the library
  (`/var/lib/saviour/sound_library/`: `<name>.wav` + `<name>.json` sidecar)
  and sound modules fetch from it, the way `fetch_hef` does (chunked
  Socket.IO upload to the controller, module downloads by name). Uploaded
  files live outside the checkout, so a deploy's hard reset never touches
  them. The two shipped files stay as built-ins.
- **Generated sounds are rendered once to a file, never synthesised at
  playback.** The generator writes a WAV plus a sidecar with its parameters,
  generator version and the file's SHA-256. A session records the hash of
  every sound its protocol used (in `_protocol.json`), so a later generator
  change can't silently change what a protocol plays. Playback is unchanged
  (`load_sound` preloads samples).
- **Every library file gets the same analysis**, whether uploaded or
  generated, stored in its sidecar and shown in the UI.
- **Levels are dBFS until an output is calibrated.** Then dB SPL at the
  animal (phase 4). A per-output cap on level and continuous duration
  applies from phase 1 in dBFS terms (welfare: startle pulses are
  100-120 dB; a long tone at that level damages hearing).

## The 48 kHz constraint (settle in phase 1)

`SoundModule._RATE` is fixed at 48 kHz, so nothing above ~20 kHz plays, and
`load_sound` resamples with `np.interp` (no anti-alias filter): a 192 kHz
USV recording today plays as aliased audible junk instead of failing.

- Phase 1: replace `np.interp` with a band-limited resampler (polyphase
  windowed-sinc in NumPy, or `python-soxr` if a wheel exists for the Pis;
  scipy is not in the module env). Analysis flags energy above the output's
  Nyquist and says it will be removed.
- Ultrasonic playback (22 kHz alarm calls at full bandwidth, 50 kHz USV
  playback) needs `sound.sample_rate` configurable (check the DAC8x really
  runs at 192 kHz on `plughw`), the stream opened at that rate, and speakers
  that reproduce it. Generators refuse frequencies above the output's
  usable band rather than alias. Separate decision; not needed for audible
  stimuli.

## Analysis (`src/shared/sound_analysis.py`)

NumPy only, so the controller can run it (its env has no `soundfile`): a
small RIFF/WAV reader for PCM 16/24/32 and float32; anything else is refused
with "convert to WAV". Output, stored in the sidecar:

- **Format:** sample rate, channels, sample format/bit depth, duration,
  file size.
- **Level:** peak and RMS in dBFS per channel; crest factor; clipped sample
  count (at or near full scale); DC offset.
- **Edges:** leading/trailing silence; whether it starts or ends abruptly
  (non-zero first/last samples → audible click; suggest a ramp).
- **Spectrum:** dominant frequency, -10 dB bandwidth, fraction of energy
  above the output's usable band (removed at playback) and below 1 kHz.
- **Stereo:** mono, identical channels, or different L/R (an output plays
  the pair as is).
- **Render data:** a min/max waveform envelope (~800 bins per channel) and
  a coarse spectrogram (~200 × 64, dB) for the frontend to draw. Data, not
  images, so it follows the theme and the shared spectrogram palettes
  (`plans/spectrogram-palettes-and-call-bands.md`).
- **Warnings** in plain words, e.g. "Clips: 312 samples at full scale",
  "38 % of the energy is above 20 kHz and won't play at 48 kHz", "Starts
  abruptly: will click".

## Phases

1. **Upload + analysis + library.** *Built 2026-11-26.* Differences from
   the plan below: the limits (`sound.max_peak_dbfs`, `sound.max_length_s`,
   0 = off) are per module, not per output (per output comes with
   calibration); they're enforced by the module (play_sound, its protocol
   check at start and `validate_protocol`), not yet by the controller's
   session-create check or the JS designer. Controller: library store, chunked
   upload (`upload_sound_*`, size cap), analysis on upload complete, shown
   before **Add to library** (cancel discards). Sound card: a **Library**
   tab listing files (rate, length, peak/RMS dBFS, warnings) with the
   waveform + spectrogram view on select, delete (refused while a saved
   protocol uses the file), and the output Test buttons using any library
   file. Modules sync the library on connect and on change (name + hash;
   fetch what's missing). Band-limited resampler. Protocol designer's file
   picker lists the library. Per-output level/duration cap (dBFS).
2. **Generators.** *Built 2026-11-26* (`src/shared/sound_generator.py`,
   `NewSoundModal.jsx`); noise colours white/pink/brown/blue/violet with
   band and notch were added to the plan; background noise is the noise
   type at a long length; "fresh noise per trial" is still a later protocol
   option. Revised the same day (generator v2): the library holds single
   sounds and the protocol designer repeats them, so a single click replaced
   the click train and there are no presets; level is the plateau RMS (click:
   peak); the window previews live. Open: a protocol that plays a file
   longer than the file (e.g. 30 s of background from a 1 s noise) needs
   looping on the module, which play steps can't do yet. A **New sound** modal: tone, noise burst (white /
   band-limited; frozen with a recorded seed, or fresh per trial as a
   protocol option later), continuous background noise, gap in noise, click
   train, AM tone, FM sweep. All with duration, level (dBFS), cosine onset/
   offset ramps (default 5 ms), preview of the analysis before saving.
   Python generator shared by controller and tests; the modal posts
   parameters, the controller renders.
3. **Session provenance.** `_protocol.json` records each sound's name,
   SHA-256 and sidecar; the module refuses a cue whose file hash differs
   from the protocol's (library changed mid-campaign). NWB export carries
   the stimulus table with hashes.
4. **Calibration (dB SPL).** Per output, play tones/noise and measure at
   the animal's position with a calibrated AudioMoth (EM258 calibration
   from `plans/per-audiomoth-settings.md`; 192 kHz covers the audible and
   ultrasonic bands). Store a frequency-response table per output; the
   generator then takes dB SPL and the library shows the expected SPL per
   output; caps move to dB SPL. Recalibrate when a speaker moves.

## Accept tests

- Upload a 192 kHz file with energy at 50 kHz on a 48 kHz output: analysis
  warns, and playback captured on an AudioMoth shows no aliased energy
  below 20 kHz.
- Generated 5 kHz, -20 dBFS, 1 s tone with 5 ms ramps: analysis reports
  5 kHz, peak -20 dBFS, no edge warning; the same parameters after a
  generator change produce a new hash and the old protocol refuses until
  re-pointed.
- Clipped and abrupt test files raise the right warnings.
- A sound above the per-output cap can't be played or saved into a
  protocol.
- Two sound modules end up with identical library hashes after an upload.


## Intended design: recipes vs recordings (agreed 2026-11-26)

The library has two kinds of entry, shown as separate sections on the
Library tab (`kind` on every entry: `recipe` when it has `generator`
parameters, else `recording`):

- **Recordings**: uploaded WAVs (USVs, predator sounds, anything recorded).
  Can't be re-rendered. A protocol step plays them as they are, cut to the
  step's length; a step longer than the file loops it (noise-like
  recordings with a short crossfade) or is refused/warned in the designer
  for tones and calls. Looping is not built yet.
- **Built sounds (recipes)**: tone, noise, click, AM tone, sweep from the
  sound builder. Not built yet: the sound module renders each from its
  recipe **at the length the protocol step asks for** when the session
  loads (`src/shared/sound_generator.py` is NumPy-only and already on the
  module), so every step gets its own onset/offset ramps and plateau level.
  The stored file becomes the reference copy (Library analysis, output
  Test buttons). Per step: frozen noise (the recipe's seed) or fresh noise
  (seed derived from the session seed + trial number). The session record
  (phase 3) stores per step the recipe, generator version, seed used and a
  hash of the rendered audio, so it can be re-rendered and checked.
  The protocol designer previews a step at its length with `preview_sound`.

Gap-in-noise was dropped as a recipe: a protocol builds a gap from two
noise steps. The browser preview repeats a click 4× (250 ms apart) and pads
sounds under 0.4 s with silence, since browsers skip very short audio; the
analysis and the stored sound are unchanged.
