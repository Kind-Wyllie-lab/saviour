# scripts/

Fleet provisioning and repair tools for SD-card imaging and network setup. Run from the repo root (e.g. `sudo scripts/multiclone.sh ...`).

The core install/uninstall/update path (`setup.sh`, `install.sh`, `uninstall.sh`, `mend.sh`, `switch_role.sh`, `saviour-config`) stays at the repo root — see the top-level CLAUDE.md and README.

## Imaging a fleet of devices

1. **`capture_master_image.sh`** — capture a template SD card (booted, `install.sh` run, role left unset) into a shrunk `.img` file.
2. **`multiclone.sh`** — flash that image to multiple target devices in parallel.
3. **`fix_ssh_and_hostname.sh`** — repair SD cards flashed with a pre-fix `multiclone.sh` (missing SSH host keys / empty hostname). Safe to run without reflashing.
4. **`clone_prep.sh`** — run *on* a Pi whose SD card was copied from another SAVIOUR device, to reset instance-specific state before `switch_role.sh`/`saviour-config`.
5. **`push_credentials.sh`** — run on the controller after `switch_role.sh` to push Samba credentials and the controller IP to a module.

## One-off repair tools

- **`configure_network.sh`** — (re)configure network settings.
- **`regenerate_ssh_key.sh`** — regenerate the SSH host key, typically after cloning an image.
- **`repair_null_bytes.sh`** — detect and restore git-tracked files corrupted by an ungraceful power-off (null bytes from an interrupted SD card write).
