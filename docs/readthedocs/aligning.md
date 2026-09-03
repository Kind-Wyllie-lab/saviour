# Post-Processing & Aligning

[Synchronisation](synchronisation.md) gets every stream onto one shared clock *while recording*. But the recordings still come off the system as separate files — one video per camera, one audio file per microphone, each with its own timestamp sidecar — and a few real-world effects mean you cannot just line them up by file position and press play. This page explains, in plain terms, what those effects are and how SAVIOUR's post-processing tools correct for them.

## Why alignment is still needed after PTP

- **Audio devices don't run at exactly their nominal rate.** An AudioMoth set to 192 kHz might actually be sampling at 191 990 Hz — a tiny error, but over an hour it adds up to nearly 0.2 seconds of drift against wall-clock. Play that audio back next to the video at face value and by the end of the recording the sound is visibly behind the picture.
- **Video files carry "nominal" timestamps, not real ones.** The recorded `.ts` file stamps every frame at a perfectly even 40 ms (for 25 fps), but the camera's *real* frame times — the ones in the CSV sidecar — jitter around that and slowly drift. The true capture time of each frame only exists in the sidecar, not in the video file itself.
- **Recordings start and stop at different moments.** A camera session and a microphone session are separate recordings; they overlap in time but rarely start together. The aligned output has to be trimmed to the window they share.

None of these are clock errors — PTP is doing its job. They are consequences of how the audio and video hardware write their files, and they are fixed after the fact.

## How SAVIOUR aligns audio to video

The microphone writes a small text sidecar next to each audio file, with a wall-clock timestamp for the start of every ~0.7 s block of samples. Because those timestamps are on the same PTP clock as the camera's per-frame timestamps, the audio and video are already on one timeline — the tool just has to measure and correct the two effects above:

1. **Recover the true sample rate.** Fitting a straight line through the block timestamps gives the real seconds-per-block, and therefore the AudioMoth's true sample rate — typically a few tens of parts-per-million off nominal. A handful of blocks where the recorder briefly stalled (normal on a busy Pi) are detected and discarded so they don't skew the fit.
2. **Find where sample zero sits on the clock.** The same fit, extended back to the first sample, gives the wall-clock instant the recording truly began — accurate to well under a millisecond in practice.
3. **Resample and pin.** The audio is resampled from its true rate onto a clean rate (removing the drift), then shifted so sample zero lands exactly at the start of the video window, with silence padded at the front or back so the track spans the whole window.

The output is a full-bandwidth aligned audio file (nothing above the human hearing range is thrown away — ultrasonic vocalisations are preserved), plus a report of the fit: the measured rate, the error in parts-per-million, and how tightly the block timestamps fit the line (the honest measure of how good the alignment is).

## How SAVIOUR aligns video frames

For anything that combines multiple cameras, or a camera with audio, frames are placed by their **real timestamp from the CSV sidecar**, not by assuming a constant framerate. Each output frame time is matched to the camera frame whose capture timestamp is closest. This keeps cameras that drift by a frame or two over a long session — and audio that has been corrected to true wall-clock — all locked together, instead of slowly sliding apart.

## What you can produce

- **An aligned audio file** per microphone, on the video's timeline, ready to drop into a vocalisation-analysis tool.
- **A whole-session spectrogram** per microphone, for scanning at a glance where sound events occur.
- **An "ethogram" video** — the camera footage on top, a spectrogram scrolling underneath with a "now" marker — so you can scrub through a session looking for events and see, frame by frame, that a sound and the behaviour that caused it line up.

## Aligning with ephys

The ephys clock is foreign to SAVIOUR, so it is aligned separately, using the shared TTL pulse train rather than timestamps. That process — fitting the offset and drift between ephys time and SAVIOUR time from the matched pulse edges — is covered in [Using SAVIOUR with Open Ephys](open_ephys.md) and the [`saviour-ephys-analysis`](https://github.com/Kind-Wyllie-lab/saviour-ephys-analysis) repository.
