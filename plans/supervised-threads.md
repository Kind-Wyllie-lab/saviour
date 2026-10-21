# Supervised long-lived threads

- **Status:** shipped 2026-09-30 (`feat/supervised-threads`, roadmap A6)
- **Created:** 2026-09-07
- **Owner:** Andrew SG

Shipped and written up in `docs/CHANGELOG.md` ("Supervised long-lived
threads"), including the convention for new long-lived loops. The helper is
`src/shared/supervised.py::supervise()`.

Still open (in `plans/backlog.md`, "Last unsupervised long-lived threads"):
`controller/modules.py`'s ready-timeout and dropout threads. The APA
`send_state_loop`, the other half of the plan's step 6, was supervised in
`fix/apa-arduino-shutdown`.

The full plan is in git history: `git log -- plans/supervised-threads.md`.
