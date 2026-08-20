# Habitat Camera

The `habitat_camera` module is built for continuous, unattended recording in a habitat box: one camera runs per box, and instead of an operator starting and stopping each recording, the camera watches for activity itself and only keeps footage of the periods when something was actually happening.

This page covers how the motion-gated recording behaves and how to configure and tune it. For general module setup (hardware, role assignment, connecting to a controller) see [Getting Started](getting_started.md) first.

## How it decides to record

Arming a `habitat_camera` module works exactly like any other camera - press "Start Recording" (or let a scheduled session start it) as usual. What's different is what happens once it's armed:

1. The camera starts capturing continuously, but doesn't write anything to disk yet - it keeps a short rolling buffer of the last few seconds in memory (the "pre-roll buffer", `pre_roll_secs`).
2. Every frame, it scores how much of the image has changed (0 = nothing moving, higher = more of the frame changed).
3. If that score stays above `activity_threshold` for `activity_min_duration_s`, a clip starts - the pre-roll buffer gets flushed to the new file first, so the clip includes a few seconds *before* the trigger, not just after it.
4. The clip keeps recording as long as activity continues. Once the score drops back below threshold, a countdown starts (`inactivity_min_duration_s`); if activity resumes before that countdown finishes, it resets to the full duration and recording continues uninterrupted. If the countdown reaches zero, the clip closes.
5. The camera goes back to watching (step 2) for the next trigger, and this repeats for as long as the module stays armed - a single armed session can produce many separate clips.

Each clip is a normal `.ts` video with its own timestamp CSV, exported the same way as any other recording once it closes - there's no need to wait for the whole armed session to end.

## Configuring it

On the module's Settings page, the "Motion" tab has:

| Setting | What it does |
|---|---|
| Motion algorithm | `Frame differencing` compares each frame to the previous one - fast and simple, but sensitive to noise. `Background subtraction (MOG2)` builds a model of the static background and flags anything that doesn't match it - more robust to gradual lighting changes, at a bit more CPU cost. |
| Activity threshold | Fraction of the frame that needs to change before it counts as activity (0-1). Lower values trigger more easily but risk false positives from lighting flicker, condensation, or shadows. |
| Min activity duration | How long the score needs to stay above threshold before a clip actually starts. Filters out single-frame noise spikes. |
| Min inactivity duration | How long the score needs to stay below threshold before a clip closes. This is the "grace period" described in step 4 above. |
| Pre-roll buffer | How many seconds of footage before the trigger get included in the clip. |
| Processing width | The frame is downscaled to this width before scoring, to keep the per-frame cost low regardless of recording resolution. Lower is faster but coarser. |

There's also a "Reset trigger to idle" button on the same tab - useful while tuning, since otherwise a triggered state only clears the way a real clip would close (waiting out the full inactivity duration).

## Reading the live preview

The MJPEG preview shows a small badge in the corner at all times, even while the module isn't armed - useful for tuning threshold and duration values against real activity before ever starting a recording, since the badge reflects exactly the same trigger logic a real recording would use:

- **IDLE** (grey) - score is below threshold.
- **ABOVE THRESHOLD** (amber) - score is above threshold, accumulating toward the trigger.
- **RECORDING (motion)** (red) - triggered, and a clip is actually being written (module is armed).
- **TRIGGERED (not armed)** (red) - triggered by the same logic, but the module isn't armed right now, so nothing is being recorded. Useful for confirming your settings would trigger correctly before actually starting a session.
- Once triggered, if activity has stopped, the badge shows a countdown ("closing in MM:SS") toward when the clip will close, so it's clear whether it's about to close or another activity burst reset the timer.

## Tuning threshold and duration values

Real habitat activity doesn't move nearly as much of the frame as you might expect - a single animal grooming or resting will often change a much smaller fraction of the image than moving around does, so a threshold that seems reasonable in theory can turn out to need real data to get right.

Every armed session writes a `..._motion_diagnostic.csv` file alongside the usual recordings, logging the raw score and state for every single frame of the whole armed session - including the stretches where nothing ever triggered. This is exported the same way as any other session file. If a session never produced a clip and you expected it to, check this file first: it will show whether the score genuinely stayed flat (camera saw nothing) or spiked without lasting long enough to trigger (values need tuning), which isn't otherwise visible after the fact.

A reasonable workflow:

1. Arm the module (or just watch the live badge unarmed) and observe real activity in the box.
2. Use the diagnostic CSV or the live score number to see what scores real activity actually produces.
3. Set `activity_threshold` comfortably below the scores you see during genuine activity, and above what you see when the box is empty/still.
4. Start with a short `activity_min_duration_s` (around 1 second) to avoid missing brief activity, and adjust `inactivity_min_duration_s` based on how much you want a single active bout to be split into separate clips versus merged into one.

## Known limitations

- A single continuous activity streak isn't split by a maximum clip length - if activity never stops for longer than the inactivity duration, one clip keeps growing for as long as that lasts.
- Motion detection alone can't distinguish an animal moving from other sources of change in the frame (bedding shifting, condensation, lighting changes). If false triggers are a persistent problem, `Background subtraction (MOG2)` is generally more robust to gradual lighting drift than frame differencing, but neither algorithm understands what it's looking at the way a trained detector would.
