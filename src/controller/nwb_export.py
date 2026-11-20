"""
NWB export, controller side (plans/nwb-export.md).

The NWB libraries (pynwb, h5py, hdmf, pandas) aren't part of a stock
install. They go into a separate venv, ``env-nwb`` beside the main ``env``,
installed on demand from the Post-processing page; a ``.pth`` file lets that
venv also see the main env's packages (numpy, soundfile, OpenCV) and the
repo, so nothing is installed twice and the converter can reuse
``compose`` / ``audio_align``.

This process never imports pynwb: a conversion runs
``env-nwb/bin/python -m src.controller.nwb_convert`` as a child process
(low priority) and reads its JSON progress lines. One job at a time
(install or export); jobs are kept in memory, the ``.nwb`` files on the
share are the durable result.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import sysconfig
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from src.controller import nwb_convert

REPO = Path(__file__).resolve().parents[2]
NWB_ENV = Path(os.environ.get("SAVIOUR_NWB_ENV", REPO / "env-nwb"))
PACKAGES = ["pynwb"]
MAX_JOBS = 20
MAX_LOG_LINES = 400
DEFAULT_SPECIES = "Rattus norvegicus"


class NwbError(ValueError):
    """A request that can't be carried out; the message is for the user."""


def env_python(env: Path = NWB_ENV) -> Path:
    return env / "bin" / "python"


def status(env: Path = NWB_ENV) -> dict:
    """``{installed, version, env, error}``: whether the NWB venv can
    import pynwb."""
    py = env_python(env)
    out = {"installed": False, "version": None, "env": str(env), "error": None}
    if not py.exists():
        return out
    try:
        r = subprocess.run(
            [str(py), "-c", "import pynwb, h5py; print(pynwb.__version__)"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        out["error"] = str(e)
        return out
    if r.returncode == 0:
        out["installed"] = True
        out["version"] = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else None
    else:
        out["error"] = (r.stderr.strip().splitlines() or ["pynwb not importable"])[-1]
    return out


def install_steps(env: Path = NWB_ENV) -> list[tuple[str, list[str] | Callable[[], str]]]:
    """(label, argv or callable) steps that build the venv."""
    py = str(env_python(env))

    def link_main_env() -> str:
        # The venv's own site-packages gets a .pth that adds the main env's
        # site dir (addsitedir, so its own .pth files apply too) and the
        # repo, so numpy / soundfile / src.* resolve. Like the main env, the
        # venv also sees the system packages (--system-site-packages).
        site = Path(subprocess.run(
            [py, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            capture_output=True, text=True, check=True).stdout.strip())
        main = sorted({sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]})
        lines = [f"import site; site.addsitedir({d!r})" for d in main] + [str(REPO)]
        (site / "saviour-main-env.pth").write_text("\n".join(lines) + "\n")
        return f"linked {', '.join([*main, str(REPO)])}"

    return [
        ("Create the NWB environment", [sys.executable, "-m", "venv", "--system-site-packages", str(env)]),
        ("Share the main environment's packages", link_main_env),
        ("Install " + ", ".join(PACKAGES),
         [py, "-m", "pip", "install", "--disable-pip-version-check",
          "--no-input", "--progress-bar", "off", *PACKAGES]),
    ]


def default_output(session_dir: str, session_name: str) -> str:
    return os.path.join(session_dir, f"{session_name}.nwb")


class NwbJobs:
    """The single install / export slot and its recent jobs."""

    def __init__(self, share_path: str, *, env: Path = NWB_ENV,
                 busy_check: Callable[[], str | None] | None = None,
                 on_update: Callable[[dict], None] | None = None,
                 logger: logging.Logger | None = None):
        self.share_path = share_path
        self.env = env
        self.busy_check = busy_check or (lambda: None)
        self.on_update = on_update or (lambda job: None)
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        self._proc: subprocess.Popen | None = None
        self._cancel = threading.Event()

    # ── queries ──────────────────────────────────────────────────────────
    def jobs(self) -> list[dict]:
        with self._lock:
            return [dict(j) for j in self._jobs.values()]

    def running(self) -> dict | None:
        with self._lock:
            return next((dict(j) for j in self._jobs.values()
                         if j["state"] == "running"), None)

    def session_dir(self, session_name: str) -> str:
        if not session_name or not nwb_convert.safe_name(session_name):
            raise NwbError("Invalid session name")
        share = os.path.realpath(self.share_path)
        path = os.path.realpath(os.path.join(share, session_name))
        if not path.startswith(share + os.sep) or not os.path.isdir(path):
            raise NwbError(f"No exported data for '{session_name}' on the share")
        return path

    def session_info(self, session_name: str) -> dict:
        """What the export form shows: streams found, size estimate and
        any .nwb already written."""
        d = self.session_dir(session_name)
        info = nwb_convert.inspect_session(d)
        out = default_output(d, session_name)
        info["existing"] = None
        if os.path.isfile(out):
            st = os.stat(out)
            info["existing"] = {"path": os.path.relpath(out, os.path.realpath(self.share_path)),
                                "size_bytes": st.st_size, "mtime": st.st_mtime}
        return info

    # ── jobs ─────────────────────────────────────────────────────────────
    def _new_job(self, kind: str, **extra) -> dict:
        with self._lock:
            if any(j["state"] == "running" for j in self._jobs.values()):
                raise NwbError("An NWB job is already running")
            job = {"id": uuid.uuid4().hex[:12], "kind": kind, "state": "running",
                   "created_at": time.time(), "finished_at": None,
                   "progress": 0.0, "stage": "Starting", "log": [],
                   "error": None, "warnings": [], "output": None, **extra}
            self._jobs[job["id"]] = job
            while len(self._jobs) > MAX_JOBS:
                oldest = min((j for j in self._jobs.values() if j["state"] != "running"),
                             key=lambda j: j["created_at"], default=None)
                if oldest is None:
                    break
                self._jobs.pop(oldest["id"])
            self._cancel.clear()
        self._emit(job["id"])
        return dict(job)

    def _update(self, job_id: str, *, log: str | None = None, **fields) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if log is not None:
                job["log"].append(log.rstrip()[:500])
                del job["log"][:-MAX_LOG_LINES]
            job.update(fields)
        self._emit(job_id)

    def _emit(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            snap = dict(job) if job else None
            if snap:
                snap["log"] = list(job["log"])
        if snap:
            try:
                self.on_update(snap)
            except Exception:                               # noqa: BLE001 -- a UI push must not kill the job
                self.logger.exception("nwb job update emit failed")

    def _finish(self, job_id: str, state: str, **fields) -> None:
        self._update(job_id, state=state, finished_at=time.time(), **fields)

    def cancel(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["state"] != "running":
                raise NwbError("That job isn't running")
            self._cancel.set()
            proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    def delete(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise NwbError("Unknown job")
            if job["state"] == "running":
                raise NwbError("Cancel the job first")
            self._jobs.pop(job_id)

    def _run(self, job_id: str, argv: list[str], *, cwd: str | None = None,
             env: dict | None = None, on_line: Callable[[str], None] | None = None) -> int:
        """Run argv, feeding each output line to on_line (default: the
        job log). Returns the exit code (-1 when cancelled)."""
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        with self._lock:
            self._proc = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if not line.strip():
                    continue
                (on_line or (lambda s: self._update(job_id, log=s)))(line)
            rc = proc.wait()
        finally:
            with self._lock:
                self._proc = None
        return -1 if self._cancel.is_set() else rc

    # install ----------------------------------------------------------------
    def start_install(self) -> dict:
        job = self._new_job("install", stage="Installing the NWB export package")
        threading.Thread(target=self._install, args=(job["id"],), daemon=True,
                         name="nwb-install").start()
        return job

    def _install(self, job_id: str) -> None:
        steps = install_steps(self.env)
        try:
            for i, (label, step) in enumerate(steps):
                self._update(job_id, stage=label, progress=i / len(steps), log=f"── {label}")
                if callable(step):
                    self._update(job_id, log=step())
                    continue
                rc = self._run(job_id, step)
                if rc == -1:
                    self._finish(job_id, "cancelled", stage="Cancelled")
                    return
                if rc != 0:
                    hint = (" The controller needs internet access to download it; on an"
                            " offline rig run tools/export_nwb.py on a workstation instead"
                            " (pip install pynwb).") if "pip" in step[2:4] else ""
                    raise NwbError(f"{label} failed (exit {rc}).{hint}")
            st = status(self.env)
            if not st["installed"]:
                raise NwbError(f"Installed, but pynwb won't import: {st['error']}")
            self._finish(job_id, "done", progress=1.0, stage=f"Installed pynwb {st['version']}",
                         output=st["version"])
            self.logger.info(f"NWB export package installed (pynwb {st['version']}) in {self.env}")
        except Exception as e:                              # noqa: BLE001 -- reported on the job
            self.logger.warning(f"NWB install failed: {e}")
            self._finish(job_id, "error", error=str(e), stage="Install failed")

    # export -----------------------------------------------------------------
    def start_export(self, session_name: str, options: dict,
                     session_record: dict | None = None) -> dict:
        if not status(self.env)["installed"]:
            raise NwbError("Install the NWB export package first")
        busy = self.busy_check()
        if busy:
            raise NwbError(busy)
        session_dir = self.session_dir(session_name)
        opts = nwb_convert.clean_options(options, session_name)
        rec = session_record or {}
        opts["session_start"] = rec.get("start_time")
        opts["session_end"] = rec.get("end_time")
        output = default_output(session_dir, session_name)
        if os.path.exists(output) and not opts.get("overwrite"):
            raise NwbError(f"{os.path.basename(output)} already exists")
        job = self._new_job("export", session_name=session_name,
                            stage="Reading the session")
        threading.Thread(target=self._export, daemon=True, name="nwb-export",
                         args=(job["id"], session_dir, output, opts)).start()
        return job

    def _export(self, job_id: str, session_dir: str, output: str, opts: dict) -> None:
        cmd = [str(env_python(self.env)), "-m", "src.controller.nwb_convert",
               session_dir, "--output", output, "--options-json", json.dumps(opts),
               "--json-progress"]
        if shutil.which("nice"):
            cmd = ["nice", "-n", "10", *cmd]
        env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONUNBUFFERED": "1"}
        result: dict = {}

        def on_line(line: str) -> None:
            try:
                msg = json.loads(line)
            except ValueError:
                self._update(job_id, log=line)
                return
            if not isinstance(msg, dict):
                return
            if "progress" in msg:
                self._update(job_id, progress=float(msg["progress"]),
                             stage=str(msg.get("stage", "")))
            if msg.get("log"):
                self._update(job_id, log=str(msg["log"]))
            if msg.get("warning"):
                with self._lock:
                    self._jobs[job_id]["warnings"].append(str(msg["warning"])[:500])
                self._update(job_id, log=f"warning: {msg['warning']}")
            if "done" in msg:
                result.update(msg)

        try:
            rc = self._run(job_id, cmd, cwd=str(REPO), env=env, on_line=on_line)
        except OSError as e:
            self._finish(job_id, "error", error=str(e), stage="Export failed")
            return
        if rc == -1:
            for partial in (output[:-4] + ".partial.nwb", output + ".partial"):
                if os.path.exists(partial):
                    os.remove(partial)
            self._finish(job_id, "cancelled", stage="Cancelled")
            return
        if rc != 0 or "done" not in result:
            self._finish(job_id, "error", stage="Export failed",
                         error=result.get("error") or f"converter exited with code {rc}")
            return
        share = os.path.realpath(self.share_path)
        size = os.path.getsize(output) if os.path.isfile(output) else None
        self._finish(job_id, "done", progress=1.0, stage="Done",
                     output=os.path.relpath(output, share), size_bytes=size,
                     validation=result.get("validation"))
        self.logger.info(f"NWB export written: {output} ({size} bytes)")


def register_socket_handlers(sio, jobs: NwbJobs, *, is_admin: Callable[[], bool],
                             get_session: Callable[[str], dict | None],
                             logger: logging.Logger) -> None:
    from flask import request

    def reply(event: str, payload: dict) -> None:
        sio.emit(event, payload, room=request.sid)

    def guarded(event: str, fn: Callable[[dict], None], admin: bool = False):
        def handler(data=None) -> None:
            d = data if isinstance(data, dict) else {}
            if admin and not is_admin():
                reply("nwb_error", {"error": "Sign in as admin to do that", "event": event})
                return
            try:
                fn(d)
            except NwbError as e:
                reply("nwb_error", {"error": str(e), "event": event})
            except Exception as e:                          # noqa: BLE001 -- reported to the caller
                logger.exception(f"{event} failed")
                reply("nwb_error", {"error": f"Unexpected error: {e}", "event": event})
        sio.on_event(event, handler)

    def get_status(d: dict) -> None:
        st = status(jobs.env)
        payload = {**st, "default_species": DEFAULT_SPECIES, "jobs": jobs.jobs()}
        name = d.get("session_name")
        if name:
            payload["session_name"] = name
            try:
                payload["session"] = jobs.session_info(name)
            except NwbError as e:
                payload["session"] = None
                payload["session_error"] = str(e)
            rec = get_session(name) or {}
            payload["metadata"] = nwb_convert.read_metadata(
                jobs.session_dir(name)) if payload.get("session") else {}
            payload["record"] = {k: rec.get(k) for k in ("start_time", "end_time", "state")}
        reply("nwb_status", payload)

    guarded("get_nwb_status", get_status)
    guarded("install_nwb_package", lambda d: reply("nwb_job", jobs.start_install()), admin=True)
    guarded("start_nwb_export", lambda d: reply("nwb_job", jobs.start_export(
        str(d.get("session_name") or ""), d.get("options") or {},
        get_session(str(d.get("session_name") or "")))))
    guarded("cancel_nwb_job", lambda d: jobs.cancel(str(d.get("id") or "")))

    def delete(d: dict) -> None:
        jobs.delete(str(d.get("id") or ""))
        sio.emit("nwb_jobs", {"jobs": jobs.jobs()})

    guarded("delete_nwb_job", delete)
