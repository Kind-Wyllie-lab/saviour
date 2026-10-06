# Pre-v1.0 codebase cleanup (behaviour-neutral)

- **Status:** done 2026-10-06 (step 2 desk checks ride the Thursday deploy)
- **Created:** 2026-10-06
- **Owner:** Andrew SG
- **CLAUDE.md ref:** "Low priority — observability / maintenance" → pre-v1.0 cleanup
- **Window:** Tue 6 → Thu 8 Oct, deployed to the desk fleet Thursday so the
  Friday 72 h test D re-run gates the final build.
- **Goal:** less code, one copy of each thing, comments that explain *why*
  rather than narrate history, and a lint gate that actually holds the line.
  **No behaviour change** -- anything that would change what the system does
  is out of scope (see Non-goals).

## Why now

Much of the codebase has been written with AI assistance. Measured
2026-10-06 (`src/`, excluding tests):

| Measure | Value |
|---|---|
| Python | ~45k lines; tests ~23k lines (1,700+ tests) |
| Largest files | `controller/web.py` 4,652 · `controller/recording.py` 3,633 · `modules/camera_base.py` 1,898 · `modules/module.py` 1,553 |
| Commentary (comments + docstrings) | 24 % of non-blank lines; 0.5-0.7 commentary lines per code line in `habitat_camera_module.py`, `modules/config.py`, `modules/communication.py`, `controller/modules.py` |
| Comments citing an incident date | 29 |
| `ruff check src` | ~2,400 findings, mostly E501 (1,274) and PLR2004 (529); CI gates only a subset |
| `CLAUDE.md` | 78 KB (~20k tokens of agent context), much of it changelog |

The cost is concrete: the update-staging named-pipe bug (2026-10-02) had to be
fixed twice because the logic exists twice.

## Steps (in order; each its own branch, full suite, merged to staging)

### 1. Dead code sweep

Delete, with a grep for callers first:

| Item | Where | Why it's dead |
|---|---|---|
| `get_samba_info()` | `controller/controller.py:719`, `controller/facade.py:52` | no callers; carries a hardcoded fallback password |
| `get_nas_recordings()` + its socket event | `controller/web.py:4150` | scans `/mnt/nas` while `mount_nas()` mounts `/mnt/controller_export`; never worked |
| `Export.unmount()` | `modules/export.py:834` | references undefined `self.current_mount` |
| `Export._ensure_export_folder_exists()` | `modules/export.py:470` | calls `_create_export_path()` with no args (TypeError) |
| duplicate `get_module_name()` | `modules/facade.py:33` and `:148` | second definition shadows the first (ruff F811) |
| inbound `module_status` socket event | `controller/web.py` | listed dead in CLAUDE.md |
| `scripts/regenerate_ssh_key.sh`, `scripts/configure_network.sh` | | broken / orphaned duplicates (CLAUDE.md low-priority list) |

Accept: suite green; `ruff check --select F` clean on touched files; frontend
builds (if a socket event is removed, confirm no frontend listener).

### 2. One copy of each thing

- **Update flow.** `web.py`'s `handle_git_pull_update` / `_do_pull`
  (`:3041`) and `_stage_current_version_zip` (`:2614`) re-implement
  `system_update.py` (`git_checkout_info`, fetch/reset, `stage_zip`, notify,
  snapshot). Make the Socket.IO handler call `system_update` and keep only
  the progress emits in `web.py`. Also fixes the latent root-`git fetch`
  publickey bug noted in CLAUDE.md (system_update runs git as the checkout
  owner; web.py doesn't).
- **NAS mount/unmount.** Four near-identical `umount` + `mount -t cifs` blocks
  in `web.py` (`_probe_nas` `:674`, `_try_write_metadata` `:781`, `mount_nas`
  `:4223`, `ensure_export_share_mounted` `:4277`) → one helper in
  `src/shared/cifs.py` beside `cifs_auth_option`, reused by all four.
- **Export path parsing.** `modules/recording.py:get_session_from_filename()`
  truncates underscore session names; `Export._extract_session_from_filename()`
  is the correct one -- route the former through the latter (CLAUDE.md
  low-priority item).

Accept: existing tests for these paths pass unchanged (add a characterisation
test first where a path has none); desk check: Settings → NAS probe, and a
web-UI "update from git" on the desk controller.

### 3. Comments: why, not history

Rule: a comment says what a reader needs to change the code safely -- the
constraint, the non-obvious reason, the trap. Incident narrative ("Test D,
2026-10-04: … so …", "found live on …", "previously this …") moves to the
commit message / `docs/CHANGELOG.md`; at most a short pointer stays
("see CHANGELOG 2026-10-04").

- Start with the densest files listed above, then the 29 dated comments.
- Docstrings: keep the contract (args, returns, side effects, threading);
  drop restated implementation.
- **`CLAUDE.md` → current state only, target ≤ 25 KB.** Keep: commands,
  architecture, conventions, threat model, hardware gotchas (condensed),
  open-work index (one line + plan link each). Move every ✅ write-up to
  `docs/CHANGELOG.md`. It is agent context loaded on every session: bloat
  there costs attention and drifts out of date.

Accept: diff is comments/docstrings only (`git diff -w` shows no code change
in the touched hunks); suite green.

### 4. A lint gate that holds

- `pyproject.toml` `[tool.ruff.lint]`: select `E,F,W,B,UP,I,N,RUF` +
  pylint's correctness rules (`PLE`, `PLW`); ignore the noise classes
  (`E501` line length -- the formatter's job, `PLR2004` magic values,
  `PLR09xx` size/complexity counts, `PLC0415` deliberate lazy imports).
- Fix the remainder (auto-fix where safe: `W293`, `RUF100`, import order;
  hand-fix the rest; `# noqa: <code>` with a reason only where intended).
- CI (`python-app.yml`): gate on the full configured set, not the F-subset.
- `npm run lint` already gates (2026-10-02); fix the 5 `react-hooks/
  exhaustive-deps` warnings only where provably safe, else annotate why.

Accept: `ruff check src` exits 0 with the new config; CI green on staging.

### 5. The 15:16 hailo stall

Test D run 1 saw 4.4 s and 1.2 s capture stalls on `hailo_camera_3606` at
15:16 on two consecutive days. Check `systemctl list-timers --all` and
`journalctl --since 15:15 --until 15:18` on that day for a daily timer
(apt-daily, man-db, fstrim, logrotate, a `Persistent=` catch-up) or a
SAVIOUR-side periodic job. If it's a system timer competing for CPU/IO,
decide between rescheduling it (setup.sh/mend.sh) and accepting it; note the
finding either way.

**Finding (2026-10-06):** not a SAVIOUR job and not memory (no swap in use,
2.9 GB free). `hailo_camera_3606` booted at 15:15:37 on 30 Sep; the desktop
panel (`wf-panel-pi`, updater widget) asks PackageKit for a cache refresh and
update check every 24 h after login, at 15:15:49, and the capture-health
warning followed 30-60 s later on every recording day (`detect()` max 4.8 s
on 4 Oct). Other "unstable capture" warnings line up with apt-daily
(22:55 → 22:56), man-db (00:08 → 00:10) and apt-daily-upgrade (~06:41).
The other modules run the same checks at their own boot times but have the
CPU headroom; the hailo sync client at `infer_every_n=1` does not.

**Decision:** modules mask `packagekit.service` and disable `apt-daily.timer`
/ `apt-daily-upgrade.timer` (`saviour-config` for new modules, `mend.sh` step
6 for deployed ones). `unattended-upgrades` isn't installed, so these only
refreshed package lists; module code arrives through SAVIOUR. Applied by hand
to the three desk modules the same day, so Friday's test D covers it.
Residual: man-db and logrotate still run daily, and `infer_every_n=1` leaves
the hailo client little headroom for any background load.

## Verification and schedule

- Every step: `pytest` (only the known 9 Windows-only failures), touched-file
  `ruff`, frontend build if touched, merged to staging separately.
- **Thursday:** deploy staging to the desk fleet, 30-min smoke session
  (record, rotate, stop, export) before the Friday run.
- If a step isn't done by Thursday it waits until after test D rather than
  landing late.

## Non-goals (after v1.0)

- Splitting `web.py` into Flask blueprints; the `Module` god-object
  composition refactor; Samba → rsync. Structural changes like these don't
  land right before a release gate.
- Behaviour changes of any kind, including "while I'm here" fixes -- those get
  their own branch and test D coverage.
- Reformatting the whole tree (`ruff format`): one huge diff that buries
  history; adopt the formatter for new/edited code only.
