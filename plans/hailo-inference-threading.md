# Hailo preview inference — move it off the capture thread

- **Status:** shipped 2026-09-08 (PR #374, `d42a5839`), validated on hardware
- **Created:** 2026-09-08
- **Owner:** ascottg

Shipped and written up in `docs/CHANGELOG.md` 2026-09-08 ("`hailo_camera`:
preview inference moved off the capture thread"), with the root cause, the
fix, the hardware validation and the residual follow-ups (a longer
acceptance sweep, more swap/restart cycles, `detect()` latency
instrumentation). Measurements: `docs/hailo-inference-sweep-2026-09-08.md`.

The full design record (alternatives, cost/benefit, amendments) is in git
history: `git log -- plans/hailo-inference-threading.md`.
