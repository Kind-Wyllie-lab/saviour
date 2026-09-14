# PTP-synced non-recording companion module (pyControl clock alignment)

- **Status:** built on `feat/external-host-module`
  (`src/modules/variants/external_host/`) — not yet on-device tested (no
  real pyControl Pi run through it yet).
- **Created:** 2026-09-14
- **Owner:** ascottg
- **CLAUDE.md ref:** none — turned out small enough not to need an Open Work
  bullet; see revision history below for why.

## Why

A user runs pyControl on a Raspberry Pi alongside a SAVIOUR fleet and wants
to compare a pyControl-logged event (e.g. a nosepoke) against the equivalent
SAVIOUR video frame. Confirmed with Andrew: this is a **manual, post-hoc
comparison** ("just need the clocks to agree") — not automated closed-loop,
not per-event markers. Putting the pyControl Pi's system clock on the same
PTP domain as the fleet (same mechanism every camera/mic/TTL module already
uses) is the right fix; the only open question was *how* to add it as a
module without side effects on real sessions.

## Revision: the first draft of this plan was solving the wrong problem

**Original design (superseded):** treat the module as something that should
be *excluded* from `target: "all"` — a `module.recording_capable: false`
opt-out — on the theory that a companion device shouldn't be able to
PTP-gate-block or fault-monitor real recordings.

**Andrew's correction:** we *do* want it to block session start if its
clock isn't converged — that's the whole point, and excluding it would have
thrown that away. He also pointed out the module doesn't need to be a pure
no-op: let it "record" in the sense every module already does by default —
the generic health/system-state metadata, not real data.

Checking the code confirmed this is not just correct but nearly free:

- **`Recording._record_health_metadata()`** (`src/modules/recording.py:709`)
  already runs for **every** module type, unconditionally, for the duration
  of any session it's part of — samples `facade.get_health()` every
  `health_metadata_recording_interval` seconds (default 1s) into a
  `<session>_<module>_health_metadata_(<segment>_<ts>).csv`, rotates with
  segments, stages for export exactly like real data. `Health.get_health()`
  (`src/modules/health.py:94`) already includes `ptp4l_offset_ns`,
  `phc2sys_offset_ns`, both with min/max over the sample window, plus freq,
  cpu/disk/mem, throttled state, version. **This is already the durable,
  per-session PTP-quality record the comparison workflow needs** — nothing
  to build.
- **`_check_ptp_sync`** (`recording.py:266`, the session-start gate) and
  **`_check_ptp_mid_recording`** (`recording.py:2374`, warns on transitions
  into/out of degraded PTP *during* a session) both iterate
  `session.modules` with zero module-type special-casing. A module that's a
  normal `session.modules` member gets both for free.
- **`Recording.stop_recording()`** also exports a session journal snapshot
  (`_export_session_journal`) for every module type unconditionally.

So the corrected design is the *opposite* of the original: don't build an
exclusion mechanism at all. Let the module be a completely normal, if
minimal, session participant — same shape as the TTL/RFID modules, just
with no per-type data of its own. **Zero controller-side code changes
needed.** The "gotcha" from the first draft (an unsynced/flaky companion
Pi blocking or fault-flagging real sessions) is real, but it's not a bug to
route around here — it's the desired signal: if this Pi's clock isn't
trustworthy, or it drops out mid-session, the timestamps it was supposed to
make comparable to SAVIOUR's aren't valid for that window, and the session
should say so exactly the way it would for any other module.

## The module itself

Still near-zero new code — `src/modules/variants/template/template_module.py`
is already the right shape. Its three recording hooks are no-ops returning
`True`:

```python
def _start_new_recording(self) -> bool: return True
def _start_next_recording_segment(self) -> bool: return True
def _stop_recording(self) -> bool: return True
```

`_create_initial_recording_segment()` (`recording.py:420`) only treats a
literal `False` as failure, so these no-ops are a clean "I have no per-type
data of my own, but the generic health/PTP/journal machinery runs anyway"
signal — which is exactly what's wanted.

1. Copy `variants/template/` → `variants/external_host/` (generic name —
   reusable for any lab PC/Pi that wants fleet-shared PTP time and
   visibility with no real recording, not pyControl-specific hardware;
   rename to `pycontrol_host` if a more specific name is preferred).
2. Strip the placeholder `do_this`/`do_that`/`get_something` commands from
   the template copy — nothing needed beyond what `Module`/`Recording`
   already provide.
3. `external_host_config.json`: just `{"module": {"group": "external"}}` —
   a `group` for operator clarity in the module list, nothing else. No new
   config keys.
4. `variant.conf`:
   ```
   NAME="External Host (no recording)"
   DESCRIPTION="PTP-synced fleet member with no data of its own -- records only the generic health/PTP metadata trail (e.g. a pyControl host Pi)"
   ```
5. `sudo saviour-config` on the pyControl Pi → role `module`, type
   `external_host`. PTP client setup is generic to the module role, not
   variant-specific — nothing to wire.

**Optional, not required for v1:** a `@check()` reporting whether pyControl
itself looks alive (its process/port) — skip unless asked; no obvious cheap
signal without knowing this Pi's actual pyControl setup.

**Built** (`feat/external-host-module`) as
`src/modules/variants/external_host/` — `external_host_module.py`,
`external_host_config.json`, `variant.conf`, plus a `README.md` (use cases +
what it actually does, matching the convention other variants like `rfid`
have). One correction from the steps above: Andrew flagged that
`variants/template/` is meant as an onboarding aid for people writing a new
*real* module type, not something a real module should be derived from —
so this was written fresh (same trivial shape, since there's genuinely
little to do, but its own docstring/rationale rather than a template
copy-and-rename) rather than literally copying the template folder. Tests:
`src/modules/tests/test_external_host_module.py` — the abstract-methods
check plus pinning the three recording hooks to return `True` specifically
(not just something truthy), since `_create_initial_recording_segment`
only treats a literal `False` as "could not start."

## What Andrew's user needs to do (outside SAVIOUR)

Nothing SAVIOUR-specific — run pyControl on the newly-provisioned Pi as
normal. The only thing that matters is that pyControl's own session data
anchors event timestamps to that Pi's **system wall clock** at some point
(pyControl's standard `.txt` output already does this — a `started_on`
datetime plus board-relative event offsets); once that anchor is on a
PTP-disciplined clock, every event in the file reconstructs to the same
wall-clock reference SAVIOUR's per-frame CSV timestamps use. Worth a quick
sanity check with a real pyControl session before relying on it.

## Acceptance

- A module of type `external_host` registers, appears in the dashboard with
  a live PTP-offset readout, settles to the fleet's normal figures (<20µs
  mid-convergence, <5µs settled). **Not yet verified** — needs a real Pi
  provisioned via `saviour-config`.
- Creating a session with `target: "all"` (the default everywhere) includes
  it in `session.modules` like any other module, and **blocks session start**
  if its PTP offset isn't converged — the behaviour explicitly wanted.
  **Structurally true by construction** (the module does nothing to opt out
  of the generic path every module goes through — confirmed by the unit
  tests pinning `_start_new_recording()` etc. to `True`) but not yet run
  through a real session.
- The session's exported data includes an
  `<session>_external_host_<mac>_health_metadata_(...).csv` with per-second
  `ptp4l_offset_ns`/`phc2sys_offset_ns` for the whole session — this is the
  artefact the manual comparison actually leans on. **Not yet verified**
  on-device.
- A real pyControl session's reconstructed wall-clock timestamps, checked
  against this CSV and the equivalent SAVIOUR camera frame timestamps, agree
  within the fleet's normal PTP settling window — validated on a real bench
  setup, not just code review, since the entire point is a real cross-device
  comparison. **Not yet done** — the real test of whether this plan actually
  achieves what was asked.

## Not doing

- Any `recording_capable`/`"all"`-exclusion mechanism — see the revision
  above for why this was dropped.
- The REST `POST /api/v1/sessions/<name>/marker` integration — already
  built, right tool for tagging a *specific* behavioral event into a session
  in real time, but not what's needed for a bulk/manual post-hoc comparison.
- A physical TTL bridge (pyControl GPIO → a SAVIOUR TTL module input pin) —
  a tier up in precision and effort; revisit only if PTP-clock-sharing turns
  out insufficient in practice.
- Any pyControl-side code — out of SAVIOUR's repo entirely.
