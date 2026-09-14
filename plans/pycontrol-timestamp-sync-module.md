# PTP-synced non-recording companion module (pyControl clock alignment)

- **Status:** proposed
- **Created:** 2026-09-14
- **Owner:** ascottg
- **CLAUDE.md ref:** new item — see below.

## Why

A user runs pyControl on a Raspberry Pi alongside a SAVIOUR fleet and wants
to compare a pyControl-logged event (e.g. a nosepoke) against the equivalent
SAVIOUR video frame. Confirmed with Andrew: this is a **manual, post-hoc
comparison** — not an automated closed-loop integration, and not per-event
markers. The two systems' logs just need to share the same wall-clock
reference closely enough that a human can look up "which frame corresponds
to this nosepoke timestamp" and trust the answer.

That's exactly what PTP gives every SAVIOUR module already (`docs/`'s
documented settling figures: <20µs mid-convergence, <5µs once `phc2sys` is
fully settled) — so the fix is putting the pyControl Pi's system clock on
the same PTP domain as the fleet, the same way every camera/mic/TTL module
already is. No REST marker calls, no physical TTL wiring — this file exists
because *how* to put a Pi on that domain safely (as a visible, monitored
SAVIOUR module, per the decision below) has one real gotcha worth designing
around up front rather than hitting live.

**Confirmed with Andrew:** the pyControl Pi should be a **real, visible
SAVIOUR module** — dashboard entry, live PTP-offset readout, heartbeat
monitoring — not a bare `ptp4l`/`phc2sys` client invisible to the
controller. That's the harder path (below), chosen deliberately for the
visibility.

## The gotcha: `target: "all"` means literally every registered module

`Modules.get_modules_by_target()` (`modules.py:575`) resolves `"all"` to
`self.get_modules()` — every module, unconditionally. `"all"` is also the
default `target` everywhere a session gets created: the frontend
(`RecordingLayout.jsx`'s `usePersistedState(..., "all")`), the REST API's
own `docs/REST_API.md` pyControl integration sketch, and almost certainly
how this lab already runs real sessions day to day.

`recording.py::create_session()` sources its module list from exactly that
call (`:512`) and feeds it straight into `_check_ptp_sync()` (`:517`). So a
dummy module that's a real, visible SAVIOUR module would, by default, be
swept into *every real recording session*:

- **The PTP start-gate would wait on it too.** `_check_ptp_sync` only skips
  a module if its health status is already `"offline"` (`recording.py:301`)
  — an *online-but-not-yet-converged* pyControl Pi (plausible: it's running
  someone else's realtime Python workload, competing for CPU/scheduling with
  `phc2sys`) would block every real session's start, not just fail its own
  meaningless no-op recording.
- **It becomes a session participant for fault-monitoring purposes** —
  `session.modules` includes it, so if it reboots mid-session (again,
  plausible — it's not SAVIOUR's to keep alive) it reads as a session fault
  on an unrelated real recording, not just a general fleet-offline alert.

This isn't hypothetical risk-aversion — `"all"` is the default target
*today*, unconditionally, and this module's whole point is to sit there
being sometimes-flaky (it's not a SAVIOUR-dedicated device) while still
being visible. Needs a real fix, not "remember to pick a different target."

## Fix: a self-declared `recording_capable` opt-out

Mirrors the existing `module.group` pattern exactly (`modules.py:654`,
`_update_module_name`, reads `config.get("module", {}).get("group", "")`
off the module's own confirmed config) — a module declares this about
itself, no controller-admin bookkeeping, no per-deployment config drift.

- **`src/controller/models.py`** — `Module` dataclass gets
  `recording_capable: bool = True`. Default preserves every existing module
  type's behaviour untouched.
- **`src/controller/modules.py::_update_module_name`** (rename or leave —
  it already syncs more than the name) — add a sibling read:
  `self._modules[module_id].recording_capable = config.get("module", {}).get("recording_capable", True)`.
- **`src/controller/modules.py::get_modules_by_target`** — the `"all"`
  branch filters:
  ```python
  if target.lower() == "all":
      return {mid: asdict(m) for mid, m in self._modules.items() if m.recording_capable}
  ```
  Explicit targeting (by `module_id` or by `group`) is **unaffected** — you
  can still target this module directly or via its group if you ever
  deliberately want to. Only the `"all"` convenience target excludes it.
- **Frontend `targetModules.js`** (`target === "all"` branch, `:17`) needs
  the same filter for consistency — otherwise the New Session form's
  module-preview/readiness-check list would show this module while the
  session actually created on submit wouldn't include it. Mirror the same
  `recording_capable !== false` check (module objects from the frontend's
  `useModules` hook already carry every `Module` dataclass field verbatim).
- This is the **single correct choke point** — `create_session`,
  `_check_ptp_sync`, and the resulting `session.modules` list all source
  from `get_modules_by_target`, so fixing it there means the new module
  never enters a real session's world at all: not PTP-gated, not
  fault-monitored, nothing. It still fully participates in **fleet-wide**
  visibility (`get_modules()`, the dashboard, health/PTP display, the
  generic heartbeat-offline alert) — which is the whole point.

## The module itself: near-zero new code

`src/modules/variants/template/template_module.py` already **is** this —
its three recording hooks are no-ops returning `True`
(`_start_new_recording`/`_start_next_recording_segment`/`_stop_recording`).
Everything else (PTP slave setup, Zeroconf registration, ZMQ heartbeat,
config layering, health reporting) comes from the `Module` base class
unconditionally — none of it is gated on module type. So this is genuinely
just:

1. Copy `variants/template/` → `variants/external_host/` (name chosen to be
   reusable beyond pyControl specifically — any lab PC/Pi that just needs
   fleet-shared PTP time and visibility, not recording — rename if a more
   specific name is preferred, e.g. `pycontrol_host`).
2. Strip the placeholder `do_this`/`do_that`/`get_something` commands —
   nothing needed beyond what `Module` already provides.
3. `external_host_config.json`:
   ```json
   { "module": { "group": "external", "recording_capable": false } }
   ```
4. `variant.conf`:
   ```
   NAME="External Host (no recording)"
   DESCRIPTION="PTP-synced, fleet-visible companion device -- no recording capability (e.g. a pyControl host Pi)"
   ```
5. `sudo saviour-config` on the pyControl Pi → role `module`, type
   `external_host` (auto-discovered from `variant.conf`, per the existing
   menu-generation CLAUDE.md already documents). PTP client setup happens
   automatically as part of the generic module role — nothing
   variant-specific to wire.

**Optional, not required for v1:** a `@check()` reporting whether pyControl
itself looks alive (e.g. its process/port), so the dashboard shows more
than "the Pi is up." Skip unless asked — no obvious cheap signal (pyControl
isn't a SAVIOUR-managed process) without knowing pyControl's actual setup
on this Pi.

## What Andrew's user needs to do (outside SAVIOUR)

Nothing SAVIOUR-specific — just run pyControl on the newly-provisioned Pi
as normal. The only thing that matters is that pyControl's own session data
anchors event timestamps to that Pi's **system wall clock** (`time.time()`/
`datetime.now()`) at some point (pyControl's standard `.txt` session output
already does this — a `started_on` datetime plus board-relative event
offsets) — once that anchor is taken on a PTP-disciplined clock, every
event in the file reconstructs to the same wall-clock reference SAVIOUR's
per-frame CSV timestamps use, and can be compared directly. Worth a quick
sanity check with a real pyControl session before relying on it, but this
isn't a SAVIOUR-side risk.

## Acceptance

- A module of type `external_host` registers, appears in the dashboard,
  shows a live PTP offset like any other module, and settles to the same
  sub-20µs (mid-convergence) / sub-5µs (settled) figures already documented
  for the fleet.
- Creating a session with `target: "all"` while this module is online does
  **not** include it in `session.modules`, does **not** wait on its PTP
  offset, and does **not** fault the session if it goes offline mid-run.
- Explicitly targeting it by `module_id` or by its `group` ("external")
  still works (it responds to commands, reports health) — the exclusion is
  `"all"`-only.
- A real pyControl session's timestamps, once reconstructed to wall-clock,
  land within the fleet's normal PTP settling window of the equivalent
  SAVIOUR frame timestamp — validated on a real bench setup, not just unit
  tests, since the whole point is a real cross-device comparison.
- Regression: existing module types (all default `recording_capable=True`)
  are unaffected — full existing test suite green, no change to any
  existing session's module list for a fleet with no `external_host`
  module present.

## Not doing

- The REST `POST /api/v1/sessions/<name>/marker` integration — already
  built, still the right tool if a *specific behavioral event* needs to be
  tagged into a session in real time, but not what was asked for here
  (bulk/manual post-hoc comparison, not per-event markers).
- A physical TTL bridge (pyControl GPIO → a SAVIOUR TTL module input pin,
  timestamped via `pulse_pin`/input-edge capture) — the sub-frame-accurate,
  wiring-required option, one tier up in precision and effort from what's
  needed here. Worth revisiting only if PTP-clock-sharing precision turns
  out to be insufficient in practice.
- Any pyControl-side code — out of SAVIOUR's repo entirely.
