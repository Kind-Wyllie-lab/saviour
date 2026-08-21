# Habitat Deployment Improvements — Scoping

**Status: proposal, not implemented.** Nothing described here exists yet. Written 2026-08-21 out of a
design conversation covering the habitat variant specifically (16 `habitat_camera` modules + 4
`microphone` modules, each with 4 attached AudioMoths = 20 modules / 16 audio channels total,
running unattended for weeks at a time) — captured here so it isn't lost before being picked back up.
Each section below is independently buildable; there's no dependency order between them except where
noted.

## 1. Habitat livestream grid: configurable dimensions + cell aspect ratio

**Current state.** `HabitatLivestreamGrid.jsx` hardcodes `COLUMNS = ["A","B","C","D"]` and
`ROWS = [4,3,2,1]` — a fixed 4×4 grid. The cell-coordinate scheme ("name your module A1, B3, …") is a
pure frontend convention: nothing in the module config or backend schema ties a cell code to real
enclosure geometry, it's just a lookup by `module.name`. Two separate things are *also* hardcoded to a
square today, independently of the 4×4 count:
- `HabitatDashboard.css`'s `.livestream-square` forces the whole composed grid to `aspect-ratio: 1/1`.
- `HabitatLivestreamCard.css` forces *each individual cell* to `aspect-ratio: 1/1` with
  `object-fit: cover` on the `<img>` — so a real 4:3 or 16:9 camera feed is already being cropped to
  fit a square tile today, regardless of the outer grid's shape.

**Agreed design** (from conversation, not yet built):
- Column/row *counts* become a per-deployment Settings-page setting, default 4×4 — this is a runtime
  display preference (the room's shape doesn't change at build time), so it belongs in the controller's
  config (existing `base_config.json` → `active_config.json` → `.env` layering), not a `variant.conf`
  manifest. The letters-for-columns/numbers-for-rows *convention* itself stays fixed — only the counts
  change.
- One shared cell-aspect-ratio *preset* per deployment (Square 1:1 / 4:3 / 16:9 — presets, not
  free-form, since real camera hardware only outputs a handful of ratios and free-form solves a
  problem that doesn't exist here), on the assumption every `habitat_camera` in one deployment uses
  the same physical camera/orientation. Confirmed with the user this is a reasonable assumption.
- The **outer** grid's overall aspect ratio should be *derived*, not a separate setting: `cols × rows ×
  cellAspectRatio` naturally produces a wider-than-tall (or taller-than-wide) overall shape with no
  risk of the outer AR and the cols/rows/cellAR disagreeing with each other. Concretely this likely
  means `.livestream-square`'s `aspect-ratio: 1/1` becomes a computed inline style rather than a fixed
  CSS class value.
- Left open: whether `object-fit: cover` (crop to fill) stays as-is once cells match the camera's real
  ratio (cropping should mostly stop mattering once the cell AR matches the source AR) versus switching
  to `contain` — worth revisiting once cells aren't forced into a mismatched ratio, not a separate
  decision to make now.

**Implementation touches**: `HabitatLivestreamGrid.jsx` (COLUMNS/ROWS from config, not hardcoded arrays),
`HabitatDashboard.css` (`.livestream-square` → computed style), `HabitatLivestreamCard.css` (cell
aspect-ratio driven by the same config value), plus wherever the new Settings-page control lives
(likely a new section in the habitat variant's Settings page, backed by a new controller config key).

## 2. Bulk module actions on `System.jsx`

**Verified 2026-08-21, per user request to confirm before documenting**: `System.jsx` has no bulk-action
UI at all today (no checkboxes, no "select all", no bulk buttons — confirmed by grep, zero matches).
This was already a known gap (see CLAUDE.md's medium-priority TODO), re-surfaced here because it matters
disproportionately at habitat's 20-module scale — rebooting or updating each module one at a time via
`System.jsx`'s per-row `ModuleActionsMenu` is real friction an operator will hit constantly.

The good news: the logic to port already exists and works, it's just stranded. `ModuleList.jsx` (still
present, just no longer imported anywhere since the Recording page redesign moved everything to
`ReadinessSummary`/`SessionDetailPage`) has a working, tested "Update All"/"Reboot All":
- `handleUpdateAll()`: sets every module to a local "updating" status, then
  `socket.emit("send_command", { module_id: "all", type: "update_saviour", params: {} })` — a broadcast,
  not N individual commands. Per-module results stream back via the existing `module_update_result`
  socket event and update each row's status independently.
- `handleRebootAll()`: a confirm-modal ("Any active recordings will be interrupted") then
  `socket.emit("send_command", { module_id: "all", type: "reboot", params: {} })`.

**Plan**: relocate/port this block (state, handlers, the two buttons, the reboot-confirm modal) onto
`System.jsx`'s richer per-module table rather than rebuilding it — `System.jsx` already has the fuller
column set (Device/Connection/Status/IP/Version/CPU/Temp/Memory/Disk/Time Sync/Last seen/Actions) that
`ModuleList.jsx` doesn't, so this is genuinely a relocation, not new design. Once ported, decide whether
`ModuleList.jsx` itself should be deleted (it's still used on the basic/apa/acoustic_startle/loom
Dashboard pages per the existing CLAUDE.md note, so check those call sites before removing).

## 3. Fleet health rollup at a glance, on the Dashboard

**Current state**: `ReadinessSummary` (built 2026-08-21, same day as this doc) already does exactly this
kind of collapsed "N/N ready ✓" grouping — but only inside the "+ New Session" drawer, scoped to
whichever target is selected there. The live Dashboard/Monitor page an operator actually watches
day-to-day has no equivalent — the only way to know "is everything actually healthy right now" is to
open `System.jsx`'s full table and scan every row.

**Idea**: a small persistent badge near the livestream grid (e.g. "15/16 cameras · 4/4 mics"), likely
reusing `ReadinessSummary`'s grouping logic against `target: "all"` rather than building new
aggregation from scratch. Collapsed-when-healthy, expanding or recoloring when something isn't — same
"don't make the operator hunt for the one thing wrong" instinct `ReadinessSummary` already established.

## 4. Trend view — disk usage / export backlog over time

**Current state**: everything the frontend shows about module/session health is a live snapshot (current
disk %, current export backlog count) — nothing tracks history. For a deployment that runs for weeks,
a slow disk fill or a growing export backlog could go unnoticed until it's already critical, since
there's no way to see "this has been climbing for three days" versus "this just happened."

**Idea**: even a lightweight, purely client-side sparkline built from periodic polls (no new backend
storage/DB needed to start — `get_module_health` is already polled regularly; just retain the last N
samples client-side and render a small trend line) would catch slow degradation well before a hard
threshold trips. A server-side retained history (actual time-series storage) is a bigger, separate
decision if the client-side version turns out not to be enough — not assumed here.

## 5. Reliability/performance — prioritized for habitat's scale, not new findings

These are pre-existing, already-documented gaps elsewhere in CLAUDE.md — listed here together because
they're the ones most likely to actually matter at 20-module / weeks-long-unattended scale, so it's
worth treating "make habitat reliable at this scale" as the forcing function that finally prioritizes
them, rather than re-describing them in full (see the referenced CLAUDE.md sections for complete detail):

- **Highest priority: no liveness signal beyond `is_recording`** (CLAUDE.md, High priority section) — a
  camera's frame callback can throw on every frame forever (bare except-log-continue) while
  `is_recording` stays `True`, and nobody is watching 16 tiles constantly for weeks. This is the exact
  "silent data loss on an unattended run" scenario habitat is most exposed to of any variant.
- **The libzmq heartbeat-reconnect crash** (CLAUDE.md, Architectural concerns — found live on a habitat
  camera module) — `_force_reconnect()` and the independent `_schedule_reconnection()`/
  `_attempt_reconnection()` path don't share `_reconnect_lock`. More modules running unattended means
  more chances any one hits a flaky ack; each hit is a hard `abort()` (systemd restarts within ~10s, but
  it's still a gap in an otherwise-unattended stream).
- **Unsupervised daemon threads generally** (CLAUDE.md, Architectural concerns — 59 ad-hoc spawns, ~280
  broad `except Exception` handlers) — same root cause as the liveness-signal gap, broader scope. A
  small supervised-thread-with-restart helper for the long-lived loops pays off most on exactly this
  kind of long unattended deployment.
- **Export/Samba bandwidth contention at 20-module scale** (new observation from this conversation, not
  previously documented anywhere) — 16 cameras + 4 mic modules could all export concurrently over Samba
  to one controller share right after a segment rotation. Worth checking whether `export.py`'s existing
  traffic-shaping actually throttles this, or whether 20 modules hammering the same SMB mount
  simultaneously around each hour boundary (if segments are hour-aligned) causes export stalls that
  wouldn't show up at loom/apa's much smaller module counts.
- **PTP settle time after a restart** (connects two existing CLAUDE.md items: the Hardware gotchas
  section's "wait 5-10 min after reboot for phc2sys to converge" note, and the libzmq crash above) — if
  the reconnect crash causes periodic restarts, a camera could run for several minutes post-restart with
  a not-yet-converged frequency estimate, quietly degrading framesync accuracy with no error surfaced
  anywhere.

## Next steps

Nothing here has been started. Pick up by choosing one section (§2 — bulk actions — is probably the
smallest, most self-contained, and highest-immediate-value: no design decisions left, just relocation)
and scoping it properly before writing code, per this repo's usual practice.
