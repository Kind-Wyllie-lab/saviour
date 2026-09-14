# pyControl live-event bridge + integration alternatives

- **Status:** proposed — design only, nothing built.
- **Created:** 2026-09-14
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (new one-liner).
- **Builds on:** `plans/pycontrol-timestamp-sync-module.md` (the
  `external_host` module, already built on `feat/external-host-module`) —
  this plan is the next increment on the same Pi, not a separate device.

## Why

Follow-up to the pyControl clock-sync work: given SAVIOUR now has a REST
API pyControl can call into, is there value in the other direction —
SAVIOUR surfacing pyControl's own live state, or a deeper GUI/execution
fusion? Researched pyControl's actual architecture and docs
(pycontrol.readthedocs.io, pyControl/code on GitHub) rather than guessing.
Two things came out of that research that reshape the design from what was
discussed in conversation:

1. **pyControl already has a documented two-way host API** (`v.api_class`,
   reference implementation `tasks/example/api.py`) — events/prints/
   variables can originate from a custom Python script (`subtype: "api"`
   in the data file), not just the task itself. A live bridge is building
   on a sanctioned integration point, not reverse-engineering a serial
   protocol.
2. **pyControl already has its own recommended synchronisation methods**,
   and neither of them is "trust a live-received timestamp on the host
   side" — see below. This matters: a naive live bridge would be tempting
   to treat as the accurate comparison record, and it should not be.

## pyControl's own sync methods (read this before designing anything)

From `pycontrol.readthedocs.io/en/latest/user-guide/synchronisation/` and
`.../pycontrol-data/`:

- **Primary/recommended: `Rsync`**, a hardware sync-pulse method. Outputs a
  train of pulses with *randomised* inter-pulse intervals on a digital
  output pin; each system records the pulses in its own native time
  reference; the random intervals give a unique fingerprint so the pulse
  sequences can be matched even if some are missed on either side. This is
  the precision option — same class of mechanism as SAVIOUR's own camera
  framesync, just applied cross-device. pyControl logs `Rsync` pulses into
  the *same* data file as behavioural events, as `type=event,
  subtype=sync` rows — same time axis, no separate reconciliation step
  needed on pyControl's side.
- **Secondary: the computer-clock method.** pyControl's `.tsv` file (format
  ≥2.0: columns `time, type, subtype, content`, `time` = seconds since
  session start on the *pyboard's* clock) carries `info` rows with
  `subtype: start_time` / `end_time` in ISO 8601, taken from the **host
  computer's** clock at session start/stop. The docs explicitly recommend
  **linearly interpolating** event times between those two computer-clock
  anchors — not just using the start time — because the pyboard crystal and
  the host clock can drift apart over a session.

Neither of these is "read `v.api_class` events live and stamp them with
whatever time the host received them at." A live bridge has real value
(below), but it is a **visibility** feature, not a **precision** feature —
its timestamps carry USB-serial + Python + (if bridged further) ZMQ/HTTP
latency on top of whatever the pyboard's own clock already contributes,
the same class of problem `plans/ttl-kernel-timestamping.md` already
documents for SAVIOUR's own TTL input path. Don't let "live" get confused
with "accurate" in anything built from this plan.

## Two independent increments, not one

What got called "Tier 1" in conversation splits cleanly into two pieces
with very different cost/value/risk profiles. Worth doing separately, in
this order.

### 1a. Export pyControl's own `.tsv` alongside the SAVIOUR session (do this first)

The cheap, high-value one. pyControl writes its own `.tsv` file to a
configured data directory as a session runs (filename encodes subject +
date + time). If the `external_host` module (or a Pi running one) just
**watches that directory and stages finished files for export** the same
way every other module stages its own segment files, the file with the
best-available reconciliation data (the interpolatable start/end
computer-clock anchors, described above) rides the *same export pipeline*
as the video/TTL/audio data and lands in the *same session folder* on the
share automatically. No pyControl Python package dependency, no API
surface to track, no coupling of session lifecycles.

- New optional config on `external_host_config.json`:
  `external_host.pycontrol_data_dir` (default unset/disabled).
- A poll-loop thread (same shape as `Recording._monitor_recording_length`'s
  existing pattern — check periodically, no filesystem-events dependency)
  watching that directory: a file is "finished" once its size/mtime has
  been stable for two consecutive polls (pyControl has closed the handle),
  matching the "stable size = done writing" heuristic already implicit in
  how camera/TTL segment files get staged only after their own close call.
  Stage each newly-finished `.tsv` via `facade.stage_file_for_export(path)`.
- **Open design question:** what if pyControl runs a task while no SAVIOUR
  session is active? Staging has nowhere to go — same shape as the
  `habitat_camera` "islanded clip" problem CLAUDE.md already tracks
  (`_orphaned/`-style handling, or just log + skip and let the operator
  retrieve it manually). Don't solve this cleverly for v1 — log-and-skip
  is fine, matching the honesty of the existing islanded-clip gap rather
  than inventing new orphan-recovery machinery for a secondary path.
- **Not doing here:** coupling pyControl's task start/stop to SAVIOUR's
  session start/stop. The original ask ("she could run pyControl on this
  Pi") implies independent operator control of pyControl; Tier 0's REST
  API already gives a path to add that coupling later from pyControl's own
  task-definition code (plain Python, can call `POST /api/v1/sessions`)
  if ever wanted — no SAVIOUR-side change needed for that either.

### 1b. Live event bridge via `v.api_class` (do this only if dashboard visibility is actually wanted)

The deeper, pyControl-specific one. A **new module type**,
`pycontrol_host` (not an `external_host` config flag — this one imports the
`pyControl` Python package and understands its API surface, genuinely
different code and a real dependency, unlike 1a):

- Runs the task via `run_task.py`'s headless path (confirmed to exist,
  documented CLI alternative to the GUI) or drives a board directly via
  pyControl's host-side Python objects — needs a closer read of
  `tasks/example/api.py` and the `run_task.py` source before committing to
  which; this plan doesn't yet know enough to pick.
- Registers a `v.api_class` handler that receives task events/prints/
  variables as they happen and re-emits them through SAVIOUR's existing
  status pipeline — same shape as this session's `ttl_edge` work:
  `communication.send_status({"type": "pycontrol_event", "pyc_type": ...,
  "subtype": ..., "content": ..., "board_time_s": ..., "host_recv_wall_ns":
  time.time_ns()})`, a `web.py` case fanning it to `socketio.emit` +
  `_publish_api_event`, a new `pycontrol_event` type on
  `/api/v1/events`. Field naming should mirror the `.tsv` schema
  (`type`/`subtype`/`content`) so anyone who already knows pyControl's data
  format doesn't have to learn a second vocabulary.
- **`host_recv_wall_ns` is explicitly a visibility timestamp, not a
  precision one** — document this in the field itself (docstring/comment,
  and in `docs/REST_API.md` if it reaches that far), given the sync-methods
  section above. The `board_time_s` value from the event itself, combined
  with the file's own start/end anchors (1a), is what any real comparison
  should use.
- Real, standing cost: SAVIOUR takes on a dependency on pyControl's Python
  package and API stability going forward — same maintenance-burden shape
  as any vendored third-party integration, worth naming explicitly rather
  than discovering it later.

## Alternatives considered

| Option | What it is | Verdict |
|---|---|---|
| **Tier 0 (built)** | pyControl task code calls SAVIOUR's REST API (`docs/REST_API.md`'s sketch) for start/stop/markers. | Already works. Baseline every other option adds to, not replaces. |
| **1a: file-export bridge** | Watch pyControl's data dir, stage finished `.tsv` for export. | **Recommended first.** Cheap, no new dependency, delivers the actual precision-bearing artefact (pyControl's own interpolatable start/end anchors) into the session export automatically. |
| **1b: live API bridge** | `v.api_class` → SAVIOUR status pipeline → dashboard/SSE. | Worth it **only if** real-time visibility (not precision) is actually wanted — e.g. an operator watching both systems' activity on one screen, or wiring a downstream reaction to a live pyControl event. Ongoing API-surface maintenance cost. Don't build speculatively. |
| **Rsync hardware bridge** | pyControl's own recommended sync-pulse method, wired into a SAVIOUR TTL module input pin (or the newly-built `pulse_pin` output). | The actual precision option, if 1a's interpolated-file accuracy ever turns out insufficient. Real project of its own — Rsync's randomised-interval pulse-train matching needs to be decoded, not just edge-logged like a normal TTL trigger; the TTL module's plain edge-CSV wouldn't do this by itself. Not scoped here; revisit only if 1a's precision is measured and found wanting. |
| **Tier 2: full GUI fusion** | SAVIOUR's web UI becomes the primary control surface for pyControl (task selection, board management, variable editing, compilation). | **Not recommended.** Reimplements a meaningful slice of pyControl's own actively-maintained GUI inside this repo, with a permanent commitment to track its protocol/API forever. CLAUDE.md already keeps even v1.0 scope disciplined ("deliberately excludes the big structural refactors") — this is bigger than any of those. Tier 0 + 1a (+ 1b if wanted) covers the real, stated need without owning pyControl's control surface. |
| **Reverse: SAVIOUR widget embedded in pyControl's PyQt GUI** | A pyControl-side plugin/custom tab showing SAVIOUR session/PTP state. | Possible in principle, lives entirely outside this repo (pyControl-side code), lower leverage than 1b since SAVIOUR's web dashboard is already the shared-viewing surface multiple people use, not pyControl's single-machine desktop app. Not pursued unless someone wants to write it pyControl-side. |

## Acceptance (if built)

- **1a:** run a real pyControl task on the `external_host`/`pycontrol_host`
  Pi during an active SAVIOUR session; confirm the `.tsv` lands in the
  session's export folder within one poll cycle of the task finishing, with
  the same start/end computer-clock anchors pyControl itself wrote,
  unmodified.
- **1b:** a live pyControl event (a `print`, a state entry) appears on
  `/api/v1/events` as `pycontrol_event` within normal SSE latency; verify
  `host_recv_wall_ns` is documented/visibly labelled as non-authoritative
  wherever it's surfaced (code comment at minimum; UI label if it ever
  reaches the frontend).
- Both: no coupling assumed between pyControl's task lifecycle and
  SAVIOUR's session lifecycle — pyControl running with no active SAVIOUR
  session, and a SAVIOUR session running with no pyControl task, are both
  unremarkable, not error states.

## Not doing (either increment)

- Anything that starts/stops pyControl's task from SAVIOUR, or vice versa,
  beyond what Tier 0's REST API already permits from pyControl's own task
  code.
- Rsync hardware bridge implementation — named as the real precision
  option above, not scoped.
- Any pyControl-side code (a GUI plugin, a bundled task file) — 1a/1b are
  entirely SAVIOUR-repo-side; the "reverse" GUI-embedding alternative is
  explicitly out of scope for this repo regardless of who might want it.
- Full GUI fusion (Tier 2) — see table above.
