#!/usr/bin/env python3
"""
Controller self-update primitives.

`git fetch` + hard-reset the controller's checkout to `origin/<current
branch>`, stage the result as the module update package, and optionally
(a) rebuild + restart the controller onto the new code and/or (b) tell every
module to pull the package.

Standalone (no Flask / no `web` import) so it can be driven from either
`web.py`'s Socket.IO `git_pull_update` handler or the REST endpoint
`POST /api/v1/system/update` without those importing each other. web.py
currently keeps its own older closure-based copy of this flow; new callers
should use this module and web.py can be migrated onto it later.

The flow deliberately only ever pulls the checkout's own already-configured
`origin`/branch -- never a caller-supplied URL or ref -- so it adds no new
untrusted-input path (contrast `update_saviour`'s caller-supplied
`controller_url`). A hard reset (not a merge) is used because a device that
has ever taken a ZIP update has a working tree git never checked out, which
a merge would spuriously conflict against.
"""

import json
import logging
import os
import subprocess
import zipfile
from datetime import UTC, datetime

_LOG = logging.getLogger(__name__)

SRC_ROOT = "/usr/local/src/saviour"
UPDATE_STORE = "/var/lib/saviour/updates"
UPDATE_ZIP = os.path.join(UPDATE_STORE, "saviour-latest.zip")
UPDATE_META = os.path.join(UPDATE_STORE, "update_meta.json")
# Mirrors web.py's _STAGE_SKIP_DIRS — dirs never shipped in the module package.
STAGE_SKIP_DIRS = {".git", "env", "__pycache__", ".pytest_cache", "dist",
                   ".eggs", "node_modules"}
_ENV_PIP = "/usr/local/src/saviour/env/bin/pip"

# Same location + layout web.py's revert-from-UI (revert_controller_update)
# reads, so a snapshot taken here is revertible from the web UI unchanged.
CTRL_BACKUP_DIR = "/var/lib/saviour/controller_backups"
CTRL_BACKUP_KEEP = 3
_CTRL_BACKUP_EXCLUDES = ["env/", ".git/", "node_modules/", "__pycache__/",
                         ".pytest_cache/", ".eggs/"]


def snapshot(reason: str, version: str = "unknown",
             src_root: str = SRC_ROOT, backup_dir: str = CTRL_BACKUP_DIR) -> dict:
    """rsync `src_root` into a fresh timestamped dir under `backup_dir` before
    an update overwrites it (`--link-dest` against the newest existing so
    keeping several is cheap). Best-effort: logs and returns
    `{"ok": False, ...}` on failure rather than aborting the update. Mirrors
    web.py's `_snapshot_controller`."""
    import shutil
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"{ts}_{version}"
    dest = os.path.join(backup_dir, name)
    os.makedirs(backup_dir, exist_ok=True)
    existing = sorted(
        (d for d in os.listdir(backup_dir)
         if os.path.isdir(os.path.join(backup_dir, d))), reverse=True)
    cmd = ["rsync", "-a", "--delete"]
    cmd += [f"--exclude={e}" for e in _CTRL_BACKUP_EXCLUDES]
    if existing:
        cmd.append("--link-dest=" + os.path.join(backup_dir, existing[0]) + "/")
    cmd += [f"{src_root}/", f"{dest}/"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        meta = {"version": version,
                "created_at": datetime.now(UTC).isoformat(), "reason": reason}
        with open(os.path.join(dest, ".backup_meta.json"), "w") as f:
            json.dump(meta, f, indent=2)
        for old in existing[CTRL_BACKUP_KEEP - 1:]:
            shutil.rmtree(os.path.join(backup_dir, old), ignore_errors=True)
        _LOG.info("snapshot: saved %s (%s)", name, reason)
        return {"ok": True, "name": name, **meta}
    except Exception as e:                                  # noqa: BLE001
        _LOG.error("snapshot failed (%s): %s", reason, e)
        shutil.rmtree(dest, ignore_errors=True)
        return {"ok": False, "error": str(e)}


def git_checkout_info(src_root: str = SRC_ROOT) -> dict:
    """`{"available": True, "branch": ..., "remote": ...}` when `src_root` is a
    git checkout on a named branch with an `origin` remote, else
    `{"available": False, "reason": ...}`. Mirrors web.py's `_git_checkout_info`."""
    if not os.path.isdir(os.path.join(src_root, ".git")):
        return {"available": False, "reason": "No git checkout on this device"}
    try:
        branch = subprocess.run(
            ["git", "-C", src_root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.strip()
        if not branch or branch == "HEAD":
            return {"available": False,
                    "reason": "Detached HEAD -- checkout a branch first"}
        remote = subprocess.run(
            ["git", "-C", src_root, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.strip()
        if not remote:
            return {"available": False, "reason": "No 'origin' remote configured"}
        return {"available": True, "branch": branch, "remote": remote}
    except Exception as e:                                  # noqa: BLE001
        return {"available": False, "reason": str(e)}


def pull_and_reset(branch: str, src_root: str = SRC_ROOT) -> dict:
    """`git fetch --prune origin <branch>` then `git reset --hard
    origin/<branch>`. Raises `subprocess.CalledProcessError` on git failure.
    Returns `{"branch", "old_commit", "new_commit"}`."""
    old = _short_head(src_root)
    subprocess.run(
        ["git", "-C", src_root, "fetch", "--prune", "origin", branch],
        check=True, capture_output=True, text=True, timeout=120)
    subprocess.run(
        ["git", "-C", src_root, "reset", "--hard", f"origin/{branch}"],
        check=True, capture_output=True, text=True, timeout=30)
    new = _short_head(src_root)
    _LOG.info("system_update: %s %s -> %s", branch, old, new)
    return {"branch": branch, "old_commit": old, "new_commit": new}


def _short_head(src_root: str) -> str:
    return subprocess.run(
        ["git", "-C", src_root, "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, timeout=10, check=False,
    ).stdout.strip()


def stage_zip(version: str = "unknown", src_root: str = SRC_ROOT,
              zip_path: str = UPDATE_ZIP, meta_path: str = UPDATE_META) -> dict:
    """Zip `src_root`'s working tree to `zip_path` (atomic via `.tmp` +
    `os.replace`) and write `meta_path`. This is what `GET /update/package`
    serves to modules. Mirrors web.py's `_stage_current_version_zip`."""
    os.makedirs(os.path.dirname(zip_path), exist_ok=True)
    tmp = zip_path + ".tmp"
    skipped = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for dirpath, dirnames, filenames in os.walk(src_root):
            dirnames[:] = [d for d in dirnames
                           if d not in STAGE_SKIP_DIRS
                           and not d.endswith(".egg-info")]
            for filename in filenames:
                if filename.endswith(".pyc"):
                    continue
                abs_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(abs_path, src_root)
                try:
                    zf.write(abs_path, rel_path)
                except Exception as fe:                     # noqa: BLE001
                    _LOG.warning("stage_zip: skipping %s: %s", rel_path, fe)
                    skipped += 1
    size = os.path.getsize(tmp)
    os.replace(tmp, zip_path)
    meta = {
        "version": version,
        "filename": f"saviour-{version}.zip",
        "size_bytes": size,
        "uploaded_at": datetime.now().isoformat(),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
    _LOG.info("stage_zip: staged %s (%d KiB, %d skipped)",
              version, size // 1024, skipped)
    return meta


def notify_modules(send_command, module_ids, controller_url: str) -> int:
    """Send `update_saviour` to each module id. `send_command` is
    `facade.send_command(module_id, command, params)`. Best-effort per module;
    returns the count attempted."""
    for mid in module_ids:
        try:
            send_command(mid, "update_saviour",
                         {"controller_url": controller_url})
        except Exception as e:                              # noqa: BLE001
            _LOG.error("notify_modules: %s failed: %s", mid, e)
    return len(list(module_ids))


def build_and_restart(src_root: str = SRC_ROOT, rebuild_frontend: bool = True) -> None:
    """`pip install --no-index` + (optional) `npm install && npm run build` +
    `systemctl restart saviour.service`. Blocking except the final restart
    (spawned detached) -- run this on a worker thread; the restart kills the
    caller. Mirrors web.py's `_controller_build_and_restart`."""
    import glob
    import shutil
    import time

    try:
        pip = subprocess.run([_ENV_PIP, "install", "-q", "--no-index", src_root + "/"],
                             capture_output=True, text=True)
        if pip.returncode != 0:
            _LOG.warning("build_and_restart: pip --no-index failed "
                         "(new deps need a manual online `pip install .`)")
        if rebuild_frontend:
            frontend_dir = os.path.join(src_root, "src/controller/frontend")
            npm = shutil.which("npm")
            if not npm:
                cands = sorted(glob.glob("/home/pi/.nvm/versions/node/*/bin/npm"))
                npm = cands[-1] if cands else None
            if npm and os.path.isdir(frontend_dir):
                _LOG.info("build_and_restart: rebuilding frontend")
                subprocess.run([npm, "install", "--silent"],
                               cwd=frontend_dir, capture_output=True)
                b = subprocess.run([npm, "run", "build"],
                                   cwd=frontend_dir, capture_output=True, text=True)
                if b.returncode != 0:
                    _LOG.warning("build_and_restart: frontend build failed: %s",
                                 b.stderr)
            else:
                _LOG.warning("build_and_restart: npm not found -- frontend not rebuilt")
    except Exception as e:                                  # noqa: BLE001
        _LOG.error("build_and_restart: build step failed: %s", e)
        return
    _LOG.info("build_and_restart: restarting saviour.service")
    time.sleep(2)
    subprocess.Popen(["sudo", "systemctl", "restart", "saviour.service"])
