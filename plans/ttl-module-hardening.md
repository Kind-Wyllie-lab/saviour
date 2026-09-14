# TTL module hardening: dead config/code, liveness signal, API-triggered pulse

- **Status:** proposed
- **Created:** 2026-09-14
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (new one-liner); also
  referenced from the "Recording-pipeline liveness" item under
  Correctness/data loss (TTL's missing signal).

## Why

Read through `ttl_module.py` end to end looking for gaps beyond the edge-
timestamp-jitter problem already tracked in `plans/ttl-kernel-timestamping.md`.
Found a dead config key, a chunk of superseded dead code, a liveness blind
spot consistent with the one CLAUDE.md already calls out for TTL/RFID, and a
missing primitive for the module's most likely real use case (an external
experiment controller wanting to fire a marker pulse at an arbitrary moment).
None of these require the libgpiod migration in the timestamping plan — all
independently actionable now.

## 1. `debounce_ms` config key is declared but never read

`ttl_config.json:44` advertises a per-input `_debounce_ms` (default 50) in
`_mode_settings_schema._input`, implying the UI can configure input debounce.
`assign_pins()` hardcodes `gpiozero.Button(pin_number, bounce_time=0,
pull_up=pull_up)` at `ttl_module.py:672` and `681` — the config value is
never read into `bounce_time`. Any input wired to a bouncy contact or noisy
line gets zero debounce regardless of what's configured.

**Fix (standalone, no dependency on the kernel-timestamping migration):**
read `pin_config.get("debounce_ms", 0)` in `assign_pins()` and pass
`bounce_time=debounce_ms / 1000.0` to `gpiozero.Button`.

**Note:** `plans/ttl-kernel-timestamping.md` Phase 1 also plans to plumb
`debounce_ms` through, but as a kernel `debounce_period` on the libgpiod
reader (different semantics — "stable for N" vs gpiozero's "ignore edges
within N of the last edge", per that plan's Risks section). If this fix lands
first on the gpiozero path, update that plan's Phase 1 config bullet to note
the interim `bounce_time` behaviour it's replacing rather than introducing
debounce from scratch.

## 2. Dead code sweep

- **`start_pseudo_random_pulses` / `stop_pseudo_random_pulses` /
  `_pulse_generation_worker` / `get_pulse_generation_status`**
  (`ttl_module.py:473-625`) plus the `pulse_generation_active` /
  `pulse_generation_thread` / `pulse_generation_pin` / `pulse_generation_config`
  state (`:100-108`) — no callers anywhere in the repo (not in
  `ttl_commands`, not in `web.py`, not in tests). Superseded by the
  config-driven `_start_pseudorandom_generator` / `_pseudorandom_worker` pair
  that reads per-pin settings from `pin_configs` instead of a single global
  config dict.
- **`_create_ttl_file()`** (`ttl_module.py:447-451`) — docstring says "Legacy
  helper used by `_start_recording`," which was already removed (see the
  comment at `:140`). No remaining callers; `_start_new_recording()` uses
  `_get_ttl_filename()` + `_open_ttl_file()` instead.

**Fix:** delete both blocks. Small diff, no behaviour change — verify with
`ruff check src/modules/variants/ttl/` and `pytest
src/modules/tests/test_ttl_module.py` (or wherever TTL tests live) after.

## 3. No liveness signal — generator threads can die silently

TTL never overrides `_check_recording_alive()`, so it falls through to
`module.py`'s default `(True, None)`. This is the TTL half of the gap
CLAUDE.md's "Recording-pipeline liveness" item already names ("TTL/RFID have
no signal"). Concretely:

- If `_experiment_clock_worker` / `_pseudorandom_worker` / `_interval_pulse_worker`
  hits an exception, it logs, sets the pin inactive in its `finally`, and the
  thread exits. The `Thread` object stays in `self.generator_threads` looking
  "started" — nothing polls `thread.is_alive()`. A dead experiment clock
  mid-session (the timing reference other modules' markers may key off) stops
  producing pulses with zero alert to the operator.
- `_sample_pins()` (the monitor-stream poller feeding the MJPEG waveform
  view) dying freezes the live preview with no health signal either — lower
  stakes since it doesn't touch the recorded CSV, but worth covering in the
  same pass since it's the same pattern.

**Fix:** add

```python
def _check_recording_alive(self) -> tuple[bool, str | None]:
    if not self.is_recording:
        return True, None
    dead = [pn for pn, t in self.generator_threads.items() if not t.is_alive()]
    if dead:
        return False, f"TTL generator thread(s) dead: pins {dead}"
    return True, None
```

This plugs directly into the existing `recording_health_warning` → controller
`handle_recording_health_status()` → alert path (`Recording._monitor_recording_health()`
polls every 10 s per module) — no new wiring needed on the controller side.
Model it on `camera_base.py:1114`'s and `microphone_module.py:460`'s
existing overrides for the same hook.

**Not covering here:** an unresponsive `_sample_pins` thread doesn't need to
fail recording health (it's a preview-only concern) — a simple restart-on-
death or just leaving it as a known gap is fine; call this out explicitly if
skipped rather than silently doing nothing.

## 4. Missing primitive: one-shot API-triggered pulse for closed-loop use

`test_pin` (`ttl_module.py:195-332`) is a bench-test primitive — fixed
duration, replays a configured *mode* (experiment_clock/pseudorandom/
interval_pulse/generic square wave), cancellable. There is no primitive for
"emit one timestamped pulse on this output pin right now," which is what an
external experiment controller (pyControl etc., the audience `docs/REST_API.md`
already targets) actually wants: mark the instant it detected a behavior, or
deliver a single TTL-triggered stimulus.

Today that has to go through the generic `/facade/send_command` escape hatch
calling `test_pin` with a short duration — which logs as a mode-faithful
*test* run in the module's log, not a marker tied to the session, and doesn't
distinguish itself from generator-driven pulses in the events CSV.

**Proposed:**

- New TTL command `pulse_pin(pin: int, duration_ms: float = 20.0) -> dict` —
  drive the pin active for `duration_ms`, then inactive, on a dedicated short-
  lived thread (same shape as `test_pin`'s `_run_test`, minus the mode
  replay). Write both edges via the existing `_write_ttl_event`, but with a
  `pin_description` suffix (or a new CSV column — see below) marking the
  source as `api` so it's distinguishable from `input`/generator rows in
  post-hoc analysis.
- Register in `self.ttl_commands` alongside `test_pin`.
- **`POST /api/v1/modules/<id>/pulse {"pin": int, "duration_ms": float}`** in
  `rest_api.py`, bearer-authed like the rest of `/api/v1`, 404 if the module
  isn't a TTL module or the pin isn't a configured output, 409 if
  `pin_number not in self.pin_configs`. Returns the two edge timestamps
  (`onset_ns`, `offset_ns`) in the response body so the caller can log its
  own correlation without polling the CSV.
- Document in `docs/REST_API.md` next to the existing session-marker
  endpoints.

**Open question:** should the CSV distinguish source (`api` vs `input` vs
generator-mode-name) via a new column, or is packing it into
`pin_description` (already free text) enough? A new column changes the CSV
schema every downstream tool reads — lean towards reusing
`pin_description` unless a real analysis need says otherwise.

## 5. Smaller: no live pulse-count/rate surfaced

The monitor stream (`_render_monitor_frame`) shows a scrolling waveform per
pin but no numeric summary (edges since recording start, time since last
edge). Useful for an operator to notice "the pseudorandom generator has gone
quiet" without staring at 20s of waveform history, and cheap to add — a
running counter dict incremented in `_write_ttl_event`, rendered as a small
label per row. Nice-to-have, not blocking; do only if 1-4 land cleanly.

## Acceptance

- `debounce_ms` fix: bench test with a bouncy input source, confirm the
  configured value changes callback count; `bounce_time=0` behaviour
  unchanged when `debounce_ms` is absent/0 (default preserved).
- Dead code removal: `ruff check` clean, existing TTL tests green, no grep
  hits for the removed symbols outside the diff.
- Liveness: kill a generator thread's underlying pin object mid-recording
  (or monkeypatch to raise), confirm `_check_recording_alive()` flips to
  `False` and the controller-side `recording_health_warning` fires within one
  10 s poll cycle.
- Pulse API: `POST .../pulse` on a configured output pin during an active
  recording produces exactly one onset/offset pair in the events CSV at the
  requested duration (±scheduling jitter, not the sub-µs target of the
  kernel-timestamping plan); 409 on a non-output or unconfigured pin; 404 on
  a non-TTL module id.

## Not doing

- Any of the edge-timestamp-acquisition-jitter work — that's
  `plans/ttl-kernel-timestamping.md`, unaffected by anything here.
- Changing `test_pin`'s behaviour — it stays as the bench-test tool; `pulse_pin`
  is additive, not a replacement.
