# Per-session metadata gap record

- **Status:** shipped 2026-09-30 (`feat/session-gap-record`, `93122b6e`; escalation `b02f0840`)
- **Created:** 2026-09-08
- **Owner:** ascottg

Shipped and written up in `docs/CHANGELOG.md` ("Per-session gap record").
The schema and where gaps open and close are documented beside the code in
`src/controller/recording.py` (`_open_gap`, `_close_gaps`, `_write_gaps`).

Still open (in `plans/backlog.md`, "Gap record: two causes and the consumers
not built"): the `plan_window` and `ptp_regressed` causes, and anything that
reads `session_gaps.json` (compose / ethogram provenance, the daily digest,
a `tools/` reader).

The full plan is in git history: `git show 0e9cda62:plans/metadata-gap-record.md`.
