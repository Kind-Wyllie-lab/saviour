# TTL edge timestamping: move acquisition off the Python callback path

- **Status:** proposed
- **Created:** 2026-09-07
- **Owner:** ascottg
- **CLAUDE.md ref:** new item under "Open work → Reliability / UX" (see bottom)

## Problem

`ttl_module.py` records an input-pin edge by calling `time.time_ns()` **inside a
gpiozero Python callback**:

- `_handle_input_pin_low` / `_handle_input_pin_high` (`ttl_module.py:420-433`) →
  `_write_ttl_event(time.time_ns(), ...)`.
- Callbacks are attached at recording start in `_start_recording_on_output_pin`
  (`ttl_module.py:348-351`) as `pin.when_pressed` / `pin.when_released` on a
  `gpiozero.Button(pin, bounce_time=0, pull_up=...)` (`ttl_module.py:672`).

The path from the electrical edge to that `time.time_ns()` call is:

```
kernel GPIO IRQ
  → lgpio notification thread (gpiozero's default pin factory on Bookworm/Pi 5)
  → gpiozero internal event queue + dispatch thread
  → Python callback scheduled (GIL acquire, contends with ZMQ / health / MJPEG-preview cv2 work)
  → time.time_ns()
```

Every stage between the IRQ and `time.time_ns()` adds **variable** delay. On an
idle Pi it's small; under load (preview streaming, an export in flight, GC) it is
tens to hundreds of µs and unbounded in the tail. The recorded value is on
`CLOCK_REALTIME`, which *is* phc2sys/PTP-disciplined — so the clock is right; the
**acquisition latency** is the error. For aligning a stimulus pulse to a camera
frame (`SensorTimestamp`, hardware-stamped, sub-µs) this Python-side jitter is
the dominant term.

Secondary: `bounce_time=0` means every edge fires a callback; a bouncy contact or
a fast pulse train can back up the gpiozero queue.

## Goal

- Edge timestamp within **single-digit µs** of the electrical edge, tied to the
  same PTP-disciplined `CLOCK_REALTIME` everything else uses.
- **Bounded, measured** jitter — a number we can quote, not "usually fine".
- **No change** to the exported CSV
  (`Timestamp_nanoseconds,pin_number,pin_mode,pin_state,pin_description`) or the
  `TTLEvent` dataclass — downstream tooling and `_write_ttl_event` stay as-is.

## Key enabler: the GPIO character device (libgpiod v2)

The Linux GPIO uAPI v2 delivers line edge events as
`struct gpio_v2_line_event { __u64 timestamp_ns; __u32 id; __u32 offset; ... }`
where `timestamp_ns` is **set by the kernel in the IRQ handler**. With the
`GPIO_V2_LINE_FLAG_EVENT_CLOCK_REALTIME` flag that stamp is `CLOCK_REALTIME` —
exactly the clock phc2sys disciplines and the camera path converts
`SensorTimestamp` into.

So the timestamp is acquired hardware-close, in-kernel, with **zero userspace
scheduling in the measurement**. Userspace only has to read the event out
eventually; how late it does so no longer affects the recorded time.

Read path: blocking `poll()`/`read()` on the line-request fd. No callbacks, no
event queue, no per-edge Python function-call dispatch.

## Phased approach

### Phase 0 — measure the status quo (do this first)

Without a baseline we can't tell whether Phase 1 is enough or Phase 2 is needed.

- Bench TTL module. Drive one input pin from a signal generator (or a second
  Pi's output) at known rates: 1 Hz, 10 Hz, 100 Hz, plus some randomised
  intervals.
- Independently capture the same edges (scope, or a second Pi with the Phase 1
  reader already, or the driving Pi's own output timestamps).
- Compute recorded-`timestamp_ns` delta vs nominal; report p50 / p95 / max and
  worst single excursion, in two conditions:
  1. idle module;
  2. `stress-ng --cpu 4 --io 2` + MJPEG preview open + a fake export loop.
- **Record the numbers in this file.** They become the acceptance target.

### Phase 1 — in-process libgpiod v2 reader thread  *(primary recommendation)*

Replace gpiozero **for input pins only**. Output-pin waveform generation
(`experiment_clock`, `pseudorandom`, `interval_pulse`) stays on gpiozero — it's
driven from Python `threading` timers already, isn't improved by this, and
gpiozero's `LED` convenience is worth keeping. Different line offsets on the same
`gpiochip0` can be held by gpiozero/lgpio and gpiod concurrently.

**New file `src/modules/variants/ttl/gpio_reader.py`:**

- `gpiod` ≥ 2.1 Python bindings. One `request_lines()` covering all configured
  input offsets on `gpiochip0` with:
  - `edge_detection = Edge.BOTH`
  - `bias = PULL_UP` / `PULL_DOWN` from `ttl.active_logic`
  - `debounce_period` from a new per-pin `debounce_ms` (config schema already
    hints `_debounce_ms` default 50 — reconcile with today's hardcoded
    `bounce_time=0`; default to 1 ms, not 0)
  - `event_clock = Clock.REALTIME`
- One dedicated `threading.Thread(name="ttl-gpio-reader", daemon=True)` running a
  blocking `wait_edge_events()` / `read_edge_events()` loop. Per `EdgeEvent`:
  map `line_offset → pin_number`, `event_type → TTLValue`, and pass
  `event.timestamp_ns` **verbatim** to the existing
  `_write_ttl_event(timestamp_ns, pin_number, state)`.
- Maintain an in-memory `{pin_number: bool}` current-state dict (seeded with one
  `get_values()` at start) so the MJPEG preview and any `@check()` state
  reporter keep working without a `gpiozero.Button` object to poll
  (`ttl_module.py:~1109`).
- Clean shutdown: a `stop` event + closing the request unblocks the read.

**Wiring in `ttl_module.py`:**

- `_assign_pins_from_config` (`~661`): for `mode == "input"`, don't create
  `gpiozero.Button`; collect `{pin_number: pin_config}` for the reader.
- `_start_recording_all_input_pins` / `stop_recording_all_input_pins`
  (`342-346`, `402-411`): start / stop the reader thread instead of assigning
  `when_pressed` / `when_released`. Keep method names and the `facade`
  staging contract (`stage_file_for_export` on stop) unchanged.
- Keep `_handle_input_pin_low/high` as thin adapters (or delete once the reader
  calls `_write_ttl_event` directly).

**Config:**

- `ttl.timestamp_source: "kernel" | "userspace"` (default `"kernel"`). Anything
  where gpiod v2 isn't available falls back to today's gpiozero path unchanged.
- Plumb `ttl.pins.<n>.debounce_ms` through for real.

**Optional:** set the reader thread `SCHED_FIFO` via `os.sched_setscheduler`
(service runs as root, so `CAP_SYS_NICE` is there). This only reduces how
quickly we *drain* events (matters for the live preview and for not overflowing
the kernel's per-request event FIFO at high rates) — it does **not** change
timestamp accuracy, which is already kernel-set. Add only if Phase 0 shows drain
latency is a problem.

**Tests (`test_ttl_module.py`):**

- Fake the reader: feed synthetic `EdgeEvent`s, assert CSV rows carry
  `timestamp_ns` unaltered and pin/state mapping is correct.
- `@pytest.mark.hardware` loopback test (output pin → input pin, assert recorded
  interval matches the driven interval within tolerance).

### Phase 2 — external C helper  *(only if Phase 0/1 measurement demands it)*

**Why it may be unnecessary:** with `EVENT_CLOCK_REALTIME` the recorded
timestamp is kernel-set, so a slow/stalled Python process changes *when we learn*
about an edge, not its recorded time. Phase 2 only buys:

- resilience to the Python process stalling long enough to overflow the kernel
  edge-event kfifo (16 events/line by default) → **dropped** edges;
- headroom at very high sustained edge rates.

If Phase 0 shows realistic experiment rates (≤ ~100 Hz) with plenty of kfifo
margin and Phase 1 holds its jitter target under load, **stop at Phase 1.**

**If built:**

- `src/modules/variants/ttl/ttl_edge_logger.c` (~200 lines), or just
  `gpiomon` from libgpiod-tools v2
  (`gpiomon --event-clock=realtime --format=...`) if packaging allows.
- `SCHED_FIFO` + `mlockall`, single `read()` loop, appends fixed-width binary
  records `{u64 timestamp_ns; u32 line; u8 rising}` to a per-segment file (or a
  FIFO the Python side tails).
- `ttl_module.py` owns it as a `subprocess`: spawn on recording start (line list
  + output path in argv), `SIGTERM` on stop, convert binary → CSV at segment
  close (or have the helper emit CSV directly and Python just owns lifecycle +
  export staging). Reuse the child-process liveness pattern from
  `camera_base.py`'s per-segment ffmpeg child (`_after_frame_hook` poll +
  `@check()`).
- Built by `variant.conf` `POST_INSTALL` against a pinned `libgpiod-dev`.

**Not doing** under any phase: output waveform generation in the helper; a
kernel module; PREEMPT_RT.

## Risks / cross-checks

- **libgpiod v2 availability on Bookworm — the load-bearing risk.** `apt`
  `python3-libgpiod` is 1.6.x; the v2 API (`request_lines`,
  `EdgeEvent.timestamp_ns`, event clock) needs 2.x. Options, in order of
  preference: (a) PyPI `gpiod` wheel — manylinux aarch64 exists for 2.1+;
  (b) build libgpiod 2.x from source in `variant.conf`; (c) raw
  `GPIO_V2_GET_LINE_IOCTL` via `ctypes` on `/dev/gpiochip0` (~120 lines, no
  dep, ugly). **Confirm (a) works on a real Pi 5 before committing to Phase 1.**
- **Clock choice:** use `Clock.REALTIME`, not the default `CLOCK_MONOTONIC` —
  monotonic would reintroduce the same mono→realtime offset-caching/skew
  question the camera path already has to manage
  (`camera_base.py:_get_wall_mono_offset_ns`).
- **phc2sys steps:** a `CLOCK_REALTIME` step (rare post-convergence, possible on
  a large correction) would jump an edge timestamp. The recording-start gate
  already requires `phc2sys_offset < 50 µs`; optionally note in the session log
  if `phc2sys` logs a step mid-recording. Low priority.
- **Debounce semantics change:** kernel `debounce_period` = "line must be stable
  for N" (true debounce); gpiozero `bounce_time` = "ignore edges within N of the
  last". Near a bouncy contact these differ. Document; move off the current `0`.
- **Two chip consumers:** gpiozero/lgpio holds output lines on the same
  `gpiochip0`. Concurrent gpiod requests on *different* offsets are allowed —
  but bench-verify there's no lgpio-vs-gpiod interaction surprise on the Pi 5
  (RP1 GPIO) before fleet rollout.
- **Related, out of scope:** `apa_arduino/shock.py` (`:285` etc.) stamps
  serial-line receipt with `time.time_ns()` — same class of problem, but USB
  serial has no kernel-timestamp path worth having. Note only.

## Acceptance criteria

- Phase 0 baseline numbers written into this file.
- **Phase 1:** bench loopback — recorded edge-delta jitter p95 ≤ [Phase 0
  target, aim < 10 µs], no worse than baseline under `stress-ng --cpu 4` +
  active MJPEG preview; zero dropped edges at 100 Hz; CSV byte-identical to
  before; `pytest src/modules/tests` green; `ruff check` clean on new code
  (`E,F,B,UP,W,I,N`).
- **Phase 2 (if triggered):** zero lost edges at 100 Hz across a 10 s `SIGSTOP`
  of the Python process.

## Effort estimate

| Phase | Estimate |
|-------|----------|
| 0 — measure | 0.5 day |
| 1 — libgpiod v2 reader + tests + one bench session | 2–3 days (+1 if libgpiod 2.x must be built in `variant.conf`) |
| 2 — C helper | 2–3 days, only if measurement demands it |
