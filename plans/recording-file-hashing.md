# SHA-256 hashes for recorded files

- **Status:** proposed
- **Created:** 2026-10-09
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → v1.0 / data loss" (export has no content
  verification before the local copy is deleted).

## Why

Two separate motivations, which want slightly different things:

1. **Silent data loss on export (v1.0).** `ModuleExport.export_staged()`
   (`src/modules/export.py`) does `shutil.copy2` → `fsync` → rename onto the
   share, then moves the source to `exported/` and, with
   `export.delete_on_export` (default `True`), deletes it in the same pass.
   Nothing checks that the share copy matches the source. A truncated or
   corrupted copy (flaky CIFS link with no SMB3 `seal`, power cut on the NAS,
   NAS-side fault) is undetectable, and the only good copy is gone moments
   later. That is exactly the "silent data loss" v1.0 is meant to rule out.
2. **Research data integrity / provenance.** A checksum taken at the moment
   of capture lets anyone prove, at any later hop (share → NAS → archive →
   DataShare / cloud), that a file is bit-for-bit what the sensor produced,
   and catch bit rot years later. Funder / repository / journal policies
   increasingly expect this.

(1) needs hashing *plus a verify-before-delete gate*. (2) needs hashing
*early* and a hash file that travels with the data in a standard format.
Neither needs cryptographic authenticity; see "Tamper-evidence" below for
what it would take if that is actually being asked for.

## Design

### Where to hash: at staging, on the module

`ModuleExport.stage_file_for_export()` is the single choke point every
finished file passes through (camera segments + CSVs, health segments,
microphone, TTL, RFID, APA shock events, basler, journal snapshot). Callers
already close/fsync before staging (e.g. `camera_base.py` CSV path), so the
file is final at this point. Hashing here covers the most hops: local disk
while the file waits in `to_export/` (can be days if the share is down), the
copy, and everything downstream.

- Hash in 1 MiB chunks with `hashlib.sha256` (OpenSSL-backed; the Pi 5's
  Cortex-A76 has ARMv8 SHA extensions). A freshly closed segment is usually
  still in page cache, so this is mostly CPU.
- Run it on the staging caller's thread for now (segments are ≤ ~1-2 GB,
  expected ≲ 2 s; Phase 0 measures). If it shows up as a capture stall on a
  loaded camera, move it to a single low-priority worker thread that hashes
  `to_export/` entries lacking a ledger entry. `export_staged` must then wait
  for / compute any missing hash itself.

### Local ledger

`<recording_folder>/hashes/<filename>.sha256`, one file per data file, in
`sha256sum` format (`<hex>  <filename>\n`), written atomically
(temp + fsync + rename). Kept **outside** `to_export/` so `export_staged`'s
`os.listdir` doesn't pick sidecars up as data. Survives a module restart.
Removed together with the data file in `_delete_local_files()`.

Files staged before this ships (or whose ledger entry is missing for any
reason) are hashed at export time instead, and logged as such
(`hash_source: export`), so nothing is ever exported unhashed.

### Export: hash-checked copy + read-back verify

Replace the `shutil.copy2` in `export_staged` with a helper that:

1. Copies in chunks while hashing the **source** stream. Mismatch with the
   ledger hash → the local file changed since staging (disk fault). Do not
   export; move it to `<recording_folder>/quarantine/`, raise a fault (see
   below). Never silently "fix" the ledger.
2. `fsync`s the destination (as today), then **re-reads** it from the share
   and hashes it. The CIFS mount uses `cache=none` (`src/shared/cifs.py`), so
   this read goes to the server, not the local page cache, and genuinely
   verifies what the share stored.
3. Only on a match: rename `PENDING_` → final name, append the line to the
   share-side `SHA256SUMS` (below), and let the file proceed to `exported/`
   and deletion.
4. On a read-back mismatch: delete the share copy, leave the source in
   `to_export/`, mark the session result failed. The existing 5-minute
   re-signal loop in `recording.py` retries it. After N consecutive
   mismatches for the same file (config, default 3), raise a fault so an
   operator finds out rather than it retrying forever.

The read-back doubles the bytes moved per export. It has to run on the
module: the share may be a separate NAS (`controller.py`
`export.share_ip`), so the controller can't in general hash the received
file locally. Read-back is
ingress to the module and isn't covered by the existing `tc` egress shaping
on port 445, so it needs its own throttle (chunked reads with a sleep to a
configured MB/s) to avoid adding PTP jitter on a recording module. Phase 0
checks `ptp4l` offset during an export with read-back on vs off.

Config (module `base_config.json`):

| Key | Default | Meaning |
|-----|---------|---------|
| `export.hash_algorithm` | `"sha256"` | Kept configurable only so the sums filename/format is explicit; not expected to change. |
| `export.verify_readback` | `true` | Re-read and hash the share copy before deleting the source. |
| `export.verify_readback_mbps` | TBD (Phase 0) | Read-back throttle. |
| `export.verify_max_retries` | `3` | Consecutive mismatches before a fault. |

### On the share: `SHA256SUMS` per module folder

`<session>/<date>/<module_name>/SHA256SUMS`, appended by that module only
(one writer per file, so no cross-module locking). Standard `sha256sum`
format so anyone can check a folder with no SAVIOUR tooling:

```
cd /share/<session>/<date>/<module_name> && sha256sum -c SHA256SUMS
```

Append = read existing, write `SHA256SUMS.tmp` with the new line(s), fsync,
rename, done once per session group per export pass rather than per file.
Files routed to `_recovered/` get their own `SHA256SUMS` there.

The existing `export.manifest_enabled` manifest gains the hex digest per
file; it is otherwise unchanged.

### Faults / visibility

- New status message `export_integrity_fault` (`module_id`, filename,
  `kind: source_changed | readback_mismatch`, attempt count), handled by the
  controller like the other faults: a `FAULT` line in `session_events.log`,
  and surfaced via the existing `FaultAlertModal` path.
- Per-session export summary (already logged) adds counts: verified /
  hashed-at-export / quarantined.

### Verification tool

`tools/verify_session.py <session_dir>`: walks every
`<date>/<module>/SHA256SUMS` (and `_recovered/`), checks each listed file,
and also reports data files **with no hash entry** (which `sha256sum -c`
can't tell you). Stdlib only, so it runs anywhere, including on the NAS or
an archive copy years later. Exit non-zero on any mismatch / missing file.

## Tamper-evidence (only if actually required)

Hashes stored next to the data prove the data wasn't *accidentally*
changed. They don't prove it wasn't *deliberately* changed: anyone who can
edit a file can regenerate its line in `SHA256SUMS`. If that is the
requirement, the hashes also need to be recorded somewhere the people
handling the data can't rewrite:

- **Cheap:** the module includes the digest in its export-completion
  message; the controller appends it to its own session record
  (`session_metadata.json` and/or the controller DB) at export time. A
  later mismatch between the share's `SHA256SUMS` and the controller record
  is evidence of an edit. Still only as strong as access to the controller.
- **Stronger:** sign the per-session `SHA256SUMS` with a controller key, or
  deposit the digest list with a third party (institutional repository,
  emailed digest, RFC 3161 timestamping service) at session end.

Out of scope until someone confirms it's needed; the cheap option is a
small add-on to Phase 2 if so.

## Phases

**Phase 0: measure (½ day, on hardware).**
`hashlib.sha256` throughput on a Pi 5 (cold and page-cached); added staging
latency on a loaded `hailo_camera` at 30 fps; `ptp4l` offset during an
export with read-back on vs off. Settles the threaded-vs-inline question and
the read-back throttle default.

**Phase 1: hash at source + `SHA256SUMS` (~½-1 day).**
Ledger in `stage_file_for_export`, hash-at-export fallback, `SHA256SUMS`
on the share, digest in the manifest, `tools/verify_session.py`. Already
delivers the provenance value (motivation 2), with no behaviour change to
the export path.

**Phase 2: verify-before-delete (~1-2 days).**
Hash-checked copy, read-back, quarantine, retry cap, `export_integrity_fault`
plus the controller handler and frontend alert. Delivers motivation 1.

**Phase 3 (optional): controller-side digest record** (tamper-evidence,
cheap option), if requested.

## Tests

- Unit (`src/modules/tests/`): ledger written atomically and in
  `sha256sum` format; missing ledger → hashed at export; source modified
  after staging → quarantined, not exported, fault sent; read-back mismatch
  (patch the read to flip a byte) → share copy removed, source kept in
  `to_export/`, not deleted; N mismatches → fault; `SHA256SUMS` append is
  idempotent across a retried pass (no duplicate lines); `_recovered/` path
  gets its own sums; `_delete_local_files` removes the ledger entry.
- `verify_session.py`: good folder passes; flipped byte, missing file and
  unlisted file each reported.
- Hardware: one desk-fleet session with all module types, then
  `sha256sum -c` on every module folder; pull the share's network cable
  mid-export and confirm nothing is deleted locally and the retry
  completes with matching hashes. Fold into the test D soak.

## Rollback

`export.verify_readback: false` restores the current copy-then-delete
behaviour (hashes and `SHA256SUMS` are still written). Phase 1 on its own
changes no deletion behaviour.

## Open questions

- What does the requester need: integrity only (Phases 1-2) or
  tamper-evidence (Phase 3)? Does a specific repository/policy dictate the
  algorithm or sums format?
- Controller-written files (`session_metadata.json`, `session_gaps.json`,
  `session_events.log`, framesync reports) are mutable during and after the
  session and don't go through module export. Hash them once at session
  close into a session-root `SHA256SUMS`? Probably yes, but they're
  rewritten by later tooling (re-compose, gap edits), so they'd need to be
  marked "as of session close".
