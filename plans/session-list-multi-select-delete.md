# Multi-select delete on the session list

- **Status:** proposed
- **Created:** 2026-09-08
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (new one-liner).

## Why

The session list (`SessionList.jsx`, the narrow rail on the Recording page)
today offers only:

- **single** delete — click a row → `SessionDetailPage` → Delete button →
  `delete_session` socket event
- **all** — "Clear all ended" → `clear_ended_sessions {delete_files: true}`,
  with a force follow-up for the ones blocked on unresolved/failed exports

There is no "delete *these* twelve". Clearing a fleet of throwaway test
sessions (or a run of bad recordings) while keeping a few means 12 trips
through the detail page, or nuking everything ended and losing the keepers.

## Also fixes a latent bug

Rapid back-to-back `DELETE /api/v1/sessions/<name>` (and by extension the
Socket.IO `delete_session`) returns a spurious `404 "Unknown session"` for
every call after the first — a ~1.5 s gap between calls avoids it (found
2026-09-08 clearing 56 test sessions). Root cause not chased, but
`recording.delete_session` mutates `self.sessions` + `_save_sessions()` +
`shutil.rmtree` with the monitor thread running concurrently. A **bulk delete
that does the whole set in one handler under `self._lock`** sidesteps it, and
is the right shape for the UI anyway.

## Backend

- **`recording.delete_sessions(names: list[str], delete_files=True,
  force=False) -> dict`** — iterate once under `self._lock`, per name apply
  the same guards `delete_session` uses (ACTIVE/SCHEDULED refused;
  pending/failed exports refused unless `force`), delete files outside the
  lock or in a single pass at the end. Return
  `{"deleted": [...], "skipped": [{"name", "reason"}]}`. One `_save_sessions()`
  + one `facade.update_sessions()` at the end, not per session.
- **`web.py` `delete_sessions` Socket.IO handler** — `{names, delete_files,
  force}`, auth-gated, emits a `sessions_deleted` result + reuses the existing
  `session_error` + `export_warning` shape for the skipped set so the
  frontend's force-follow-up pattern works unchanged.
- **`DELETE /api/v1/sessions`** (bulk, no name in the path) — body
  `{"names": [...], "files": true, "force": false}` → same
  `recording.delete_sessions`. `200 {"deleted": [...], "skipped": [...]}`;
  `400` if `names` missing/empty. Keeps the per-name `DELETE
  /api/v1/sessions/<name>` as is. Update `docs/REST_API.md` + `openapi.yaml`.
- Tests: `test_recording.py` (bulk with a mix of deletable / active /
  export-blocked / unknown), `test_rest_api.py` (bulk route: happy, partial
  skip → 200 with `skipped`, empty `names` → 400, readonly token → 403).

## Frontend (`SessionList.jsx`)

- A **selection mode**: a "Select" toggle in the list header. While on, each
  **ended** session row gets a checkbox (active / pending / scheduled rows
  never get one — they can't be deleted). A header "select all ended"
  checkbox. Shift-click for range select is a nice-to-have, not required.
- A **"Delete selected (N)"** action, disabled at N=0, with the same
  two-step confirm the "Clear all ended" flow already has
  (`pendingClearAll`-style state → confirm → `delete_sessions {names,
  delete_files: true}`), and the same force follow-up when the result comes
  back with `export_warning` + `skipped_sessions`.
- Selection clears on: leaving selection mode, a successful delete, or the
  session list changing underneath (a new session appended).
- The existing single-delete on `SessionDetailPage` and "Clear all ended"
  stay — this is additive.

## Acceptance

- Select 5 of 20 ended sessions → "Delete selected (5)" → confirm → those 5
  gone, other 15 + any active session untouched; one `sessions_update`
  broadcast, not five.
- Selecting includes an export-blocked session → result comes back with it in
  `skipped`, UI offers "Force delete 1 anyway", force works.
- Active / pending / scheduled rows have no checkbox and can't be swept in
  even via a crafted `delete_sessions` call (backend guard).
- `DELETE /api/v1/sessions` with 30 names in the body completes in one
  request with **no** spurious 404s (the bug above).

## Not doing

- Filtering / search in the session list (separate; the list is a rail, not a
  table — that's `System.jsx`'s job, see the "relocate bulk actions" item).
- Undo / soft-delete — `delete_session` is already immediate and file-deleting.
