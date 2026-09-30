# scripts/

Fleet provisioning and repair tools for SD-card imaging and network setup. Run from the repo root (e.g. `sudo scripts/multiclone.sh ...`).

The core install/uninstall/update path (`setup.sh`, `install.sh`, `uninstall.sh`, `mend.sh`, `switch_role.sh`, `saviour-config`) stays at the repo root — see the top-level CLAUDE.md and README.

## Imaging a fleet of devices

All three imaging scripts (`clone_direct.sh`, `capture_master_image.sh`, `multiclone.sh`) run as an interactive `whiptail` TUI when invoked with no arguments, or non-interactively when given the old positional args (for scripting). The TUI device pickers briefly mount each candidate card read-only and show its actual hostname/role/type/version (via `lib/identify_disk.sh`) instead of just size/model — so two identical-looking SanDisk cards in a USB hub are distinguishable by what's actually on them, not by guessing which port is which.

Two paths, pick one:

- **One-off clone of a handful of cards, no reusable image needed** — `clone_direct.sh`. Reads the source card once per target and writes straight to each target device; no intermediate `.img` file, so the host needs no spare disk space. Slower per-target than writing a pre-shrunk image (copies the full raw card, not just used space) and ties up the source card for the run.
- **Reusable master image, cloned repeatedly** — `capture_master_image.sh` then `multiclone.sh`. Needs a host with free disk space >= the source card's *full* raw capacity (it dd's the whole device before shrinking) — run it on the controller (NVMe) or another machine with real spare storage, not another Pi's own SD card, or the capture runs out of space mid-write. `capture_master_image.sh`'s TUI checks free space against the source card's full size before starting and refuses upfront rather than failing mid-copy; the scriptable form checks too.

1. **`clone_direct.sh`** — clone a source SD card straight to N target SD cards in one step, no intermediate image file. `sudo scripts/clone_direct.sh` for the TUI, or `sudo scripts/clone_direct.sh <source_device> <target1> [target2] ...` to script it.
2. **`capture_master_image.sh`** — capture a template SD card (booted, `install.sh` run, role left unset) into a shrunk `.img` file. `sudo scripts/capture_master_image.sh` for the TUI (picks source + output path, checks free space), or `sudo scripts/capture_master_image.sh <source_device> <output.img>` to script it.
3. **`multiclone.sh`** — flash that image to multiple target devices in parallel. `sudo scripts/multiclone.sh` for the TUI (picks the `.img` + targets), or `sudo scripts/multiclone.sh <image.img> <device1> [device2] ...` to script it.
4. **`configure_card.sh`** — set a freshly cloned card's role/type while it's still in the USB hub, so it configures itself on first boot with no monitor/SSH. Writes `/etc/saviour/config` on the card's root partition and (unless `--keep-provisioned`) deletes `/etc/saviour/.provisioned`, so `saviour-provision.service` runs a full `saviour-config --apply` on boot. `sudo scripts/configure_card.sh` for the TUI (type menu built from the card's own `variant.conf` files; asks controller network settings), or `sudo scripts/configure_card.sh module camera sda sdb ...` to script it. Linux host only — Windows can't see the ext4 root partition.
5. **`fix_ssh_and_hostname.sh`** — repair SD cards flashed with a pre-fix `multiclone.sh`/`clone_direct.sh` (missing SSH host keys / empty hostname). Safe to run without reflashing.
6. **`clone_prep.sh`** — older, manual alternative to `saviour-config`'s built-in clone-detect/"Refresh Identity" flow (run `sudo saviour-config` on a freshly cloned Pi — it auto-detects the mismatched hostname and offers to fix it). Still useful for scripted resets or if that flow doesn't trigger.
7. **`push_credentials.sh`** — run on the controller if Samba credentials were rotated since the source device was cloned, to push the new password + controller IP to a module.
8. **`lib/identify_disk.sh`** / **`lib/dd_progress.sh`** — shared helpers sourced by the imaging scripts above (`configure_card.sh` uses `identify_disk.sh` only); not run directly. `dd_progress.sh` redraws a live per-device write-progress dashboard (percent, rate, ETA) during the parallel `dd` step in `clone_direct.sh`/`multiclone.sh` (each `dd`'s `status=progress` output goes to its own log file to avoid N processes mangling one shared terminal line, so without this nothing shows on screen until a job finishes or fails; `dd` itself reports no percent/ETA since it doesn't know the target's total size, so the dashboard derives them from the byte count it does report against the total the caller already knows).

## One-off repair tools

- **`configure_network.sh`** — (re)configure network settings.
- **`regenerate_ssh_key.sh`** — regenerate the SSH host key, typically after cloning an image.
- **`repair_null_bytes.sh`** — detect and restore git-tracked files corrupted by an ungraceful power-off (null bytes from an interrupted SD card write).
