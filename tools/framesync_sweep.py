#!/usr/bin/env python3
"""
framesync_sweep.py -- automated hailo-camera inference-load vs frame-sync sweep.

Drives the controller REST API (config read/write + session lifecycle) and
pulls each session's provenance sidecars back over SSH, to characterise how
much the hailo preview-inference thread costs the H264 encoder on the
sync-*client* camera -- the suspected driver of the CSV-vs-.ts frame deficit
that makes the "ai camera" lag the frameserver in Post-Process compose.

See plans/multicam-frame-alignment-and-sync-provenance.md (item 3) and
docs/REST_API.md (GET/PATCH /api/v1/modules/<id>/config).

Per sweep point:
  1. PATCH both cameras' config (fps on both; hailo.infer_enabled /
     infer_every_n on the client), wait for config_sync_status == SYNCED.
  2. Gate on /readiness + /ptp (retry until PTP is under the start gate).
  3. POST /sessions {duration_minutes, autostart:true}; poll to "stopped".
  4. Wait for export; SSH-pull framesync_report.json + each camera's
     <stem>_recording.json; ffprobe -count_packets each .ts.
  5. Append one row to results.csv.
Original config on both cameras is snapshotted at the start and restored at
the end (also on Ctrl-C / error).

Auth: --token, or $SAVIOUR_TOKEN. Bearer = the admin password or an API
token (see docs/REST_API.md). Plain HTTP, LAN only.

Example:
  export SAVIOUR_TOKEN=desktoppi
  python tools/framesync_sweep.py --repeats 3 --duration-min 1 \
      --out ./sweep_run1

Dependencies: requests. SSH must be key-based to --ssh-host (no password
prompt). ffprobe must be on the controller PATH.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import posixpath
import random
import subprocess
import sys
import time
from datetime import UTC, datetime

try:
    import requests
except ImportError:
    sys.exit("pip install requests")


# --------------------------------------------------------------------------- #
# small API client
# --------------------------------------------------------------------------- #

class Api:
    def __init__(self, base_url: str, token: str, timeout: float = 15.0):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout

    def get(self, path: str, **params):
        r = self.s.get(self.base + path, params=params or None, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict | None = None):
        r = self.s.post(self.base + path, json=body or {}, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} -> {r.status_code} {r.text}")
        return r.json()

    def patch(self, path: str, body: dict, wait: float | None = None):
        params = {"wait": wait} if wait is not None else None
        r = self.s.patch(self.base + path, json=body, params=params,
                         timeout=self.timeout + (wait or 0))
        if r.status_code >= 400:
            raise RuntimeError(f"PATCH {path} -> {r.status_code} {r.text}")
        return r.json()

    # -- config helpers --------------------------------------------------- #

    def get_config(self, module_id: str) -> dict:
        return self.get(f"/modules/{module_id}/config")

    def patch_config(self, module_id: str, patch: dict, wait: float = 20.0) -> str:
        body = self.patch(f"/modules/{module_id}/config", patch, wait=wait)
        return body.get("config_sync_status", "UNKNOWN")

    def wait_synced(self, module_ids: list[str], timeout: float = 45.0) -> None:
        deadline = time.monotonic() + timeout
        pending = set(module_ids)
        while pending and time.monotonic() < deadline:
            for mid in list(pending):
                st = self.get_config(mid).get("config_sync_status")
                if st == "SYNCED":
                    pending.discard(mid)
                elif st == "FAILED":
                    diffs = self.get_config(mid).get("config_diffs")
                    raise RuntimeError(f"{mid} config FAILED: {diffs}")
            if pending:
                time.sleep(1.0)
        if pending:
            raise RuntimeError(f"config never SYNCED: {sorted(pending)}")


# --------------------------------------------------------------------------- #
# ssh
# --------------------------------------------------------------------------- #

def ssh(host: str, cmd: str, timeout: float = 60.0) -> str:
    r = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, cmd],
        capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(
            f"ssh {host} '{cmd[:60]}...' rc={r.returncode}: {r.stderr.strip()}")
    return r.stdout


def ffprobe_frames(host: str, path: str) -> int | None:
    """Video frame count. Uses -count_packets (reads the container index,
    no decode) -- for this clean h264-in-mpegts stream packets == frames
    (verified), and it's ~3 s vs ~40 s for -count_frames."""
    q = (f'ffprobe -v error -count_packets -select_streams v:0 '
         f'-show_entries stream=nb_read_packets -of csv=p=0 "{path}"')
    try:
        out = ssh(host, q, timeout=60).strip().splitlines()
        return int(out[0]) if out and out[0].isdigit() else None
    except Exception as e:                              # noqa: BLE001
        print(f"    ! ffprobe failed on {posixpath.basename(path)}: {e}")
        return None


# --------------------------------------------------------------------------- #
# sweep matrix
# --------------------------------------------------------------------------- #

def default_matrix(repeats: int, fps_values: list[int], seed: int = 0) -> list[dict]:
    """fps is the OUTER loop (one phc2sys re-settle per fps value). Within a
    repeat the inference conditions are SHUFFLED so a slow thermal drift over
    the run doesn't masquerade as an inference effect -- each condition lands
    at a different point in the warm-up across repeats."""
    infer_variants = [
        ("off", {"infer_enabled": False}),
        ("n1", {"infer_enabled": True, "infer_every_n": 1}),
        ("n2", {"infer_enabled": True, "infer_every_n": 2}),
        ("n8", {"infer_enabled": True, "infer_every_n": 8}),
    ]
    rng = random.Random(seed)
    points = []
    for fps in fps_values:
        for r in range(repeats):
            order = infer_variants[:]
            rng.shuffle(order)
            for name, hailo in order:
                points.append({
                    "label": f"fps{fps}_{name}_r{r}",
                    "fps": fps,
                    "infer": name,
                    "server_patch": {"camera": {"fps": fps}},
                    "client_patch": {"camera": {"fps": fps}, "hailo": dict(hailo)},
                })
    return points


# --------------------------------------------------------------------------- #
# per-run
# --------------------------------------------------------------------------- #

RESULT_FIELDS = [
    "ts", "label", "fps", "infer", "session_name",
    "server_id", "client_id",
    "server_csv_rows", "server_ts_frames", "server_deficit", "server_dropped_before",
    "client_csv_rows", "client_ts_frames", "client_deficit", "client_dropped_before",
    "client_rate_cv", "client_real_fps", "client_dropped_frac",
    "pair_mean_offset_us", "pair_p95_offset_us", "pair_detrended_p95_us",
    "pair_drift_us_per_s", "pair_pct_within_half_frame",
    "framesync_status", "framesync_reasons", "notes",
]


def find_session_dir(host: str, share: str, session_name: str) -> str:
    out = ssh(host, f'ls -d "{share}/{session_name}"/*/ 2>/dev/null | head -1').strip()
    if not out:
        raise RuntimeError(f"no date dir under {share}/{session_name}")
    return out.rstrip("/")


def collect(host: str, date_dir: str) -> dict:
    """Read framesync_report.json + every <stem>_recording.json under the
    session date dir, and ffprobe each referenced .ts."""
    listing = ssh(host, f'find "{date_dir}" -name "*_recording.json" '
                        f'-o -name "framesync_report.json"').splitlines()
    rec_jsons = [p for p in listing if p.endswith("_recording.json")]
    fr_path = next((p for p in listing if p.endswith("framesync_report.json")), None)

    cams = {}   # sync_mode -> {rec:..., ts_frames:...}
    for rj in rec_jsons:
        data = json.loads(ssh(host, f'cat "{rj}"'))
        mode = data.get("sync_mode", "?")
        # remote path is always POSIX -- os.path.join would use "\" on Windows
        ts_file = posixpath.join(posixpath.dirname(rj), data["video_file"])
        data["_ts_frames"] = ffprobe_frames(host, ts_file)
        cams[mode] = data

    framesync = json.loads(ssh(host, f'cat "{fr_path}"')) if fr_path else {}
    return {"cams": cams, "framesync": framesync}


def row_from(collected: dict, point: dict, session_name: str, notes: str) -> dict:
    cams = collected["cams"]
    fr = collected["framesync"]
    srv = cams.get("server", {})
    cli = cams.get("client", {})

    def deficit(c):
        rows, frames = c.get("csv_rows_written"), c.get("_ts_frames")
        return (rows - frames) if (rows is not None and frames is not None) else None

    fr_cams = {c.get("sync_mode"): c for c in fr.get("cameras", [])}
    fr_client = fr_cams.get("client", {})
    pair = (fr.get("pairs") or [{}])[0]

    return {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "label": point["label"], "fps": point["fps"], "infer": point["infer"],
        "session_name": session_name,
        "server_id": srv.get("video_file", "").split("_(")[0].split("_", 1)[-1] or "?",
        "client_id": cli.get("video_file", "").split("_(")[0].split("_", 1)[-1] or "?",
        "server_csv_rows": srv.get("csv_rows_written"),
        "server_ts_frames": srv.get("_ts_frames"),
        "server_deficit": deficit(srv),
        "server_dropped_before": srv.get("dropped_before_total"),
        "client_csv_rows": cli.get("csv_rows_written"),
        "client_ts_frames": cli.get("_ts_frames"),
        "client_deficit": deficit(cli),
        "client_dropped_before": cli.get("dropped_before_total"),
        "client_rate_cv": fr_client.get("rate_cv"),
        "client_real_fps": fr_client.get("real_fps"),
        "client_dropped_frac": fr.get("worst", {}).get("max_dropped_frac"),
        "pair_mean_offset_us": pair.get("mean_offset_us"),
        "pair_p95_offset_us": pair.get("p95_offset_us"),
        "pair_detrended_p95_us": pair.get("detrended_p95_us"),
        "pair_drift_us_per_s": pair.get("drift_us_per_sec"),
        "pair_pct_within_half_frame": pair.get("pct_within_half_frame"),
        "framesync_status": fr.get("status"),
        "framesync_reasons": " | ".join(fr.get("reasons", [])),
        "notes": notes,
    }


def run_point(api: Api, args, point: dict, prev_fps: int | None) -> tuple[dict, int]:
    label = point["label"]
    print(f"\n=== {label} ===")

    # 1. config
    print(f"  PATCH {args.server_cam} <- {point['server_patch']}")
    api.patch_config(args.server_cam, point["server_patch"], wait=args.patch_wait)
    print(f"  PATCH {args.client_cam} <- {point['client_patch']}")
    api.patch_config(args.client_cam, point["client_patch"], wait=args.patch_wait)
    api.wait_synced([args.server_cam, args.client_cam], timeout=args.sync_timeout)
    print("  both SYNCED")

    # 2. settle + ptp gate
    if prev_fps is not None and point["fps"] != prev_fps:
        print(f"  fps {prev_fps} -> {point['fps']}: settling "
              f"{args.settle_secs}s for phc2sys")
        time.sleep(args.settle_secs)
    gate_ptp(api, args)

    # 3. session
    name = f"{args.session_prefix}_{label}"
    body = api.post("/sessions", {
        "name": name, "target": args.target,
        "duration_minutes": args.duration_min, "autostart": True,
        "researcher": "framesync_sweep",
    })
    session_name = body["session_name"]
    if body.get("autostart_error"):
        raise RuntimeError(f"autostart rejected: {body['autostart_error']}")
    print(f"  recording {session_name} ({args.duration_min} min)")
    wait_stopped(api, session_name, args.duration_min * 60 + args.stop_grace)

    # 4. export + collect
    print("  waiting for export ...")
    date_dir = wait_for_report(args, session_name, args.export_timeout)
    collected = collect(args.ssh_host, date_dir)
    notes = ""
    if "client" not in collected["cams"] or "server" not in collected["cams"]:
        notes = f"missing sync_mode sidecar: got {list(collected['cams'])}"
        print(f"  ! {notes}")
    row = row_from(collected, point, session_name, notes)
    print(f"  -> client deficit={row['client_deficit']} "
          f"dropped_before={row['client_dropped_before']} "
          f"rate_cv={row['client_rate_cv']} "
          f"detrended_p95_us={row['pair_detrended_p95_us']} "
          f"status={row['framesync_status']}")
    return row, point["fps"]


def gate_ptp(api: Api, args) -> None:
    deadline = time.monotonic() + args.ptp_gate_timeout
    while time.monotonic() < deadline:
        ready = api.get("/readiness", target=args.target)
        checks = ready.get("checks", {})
        ptp_ok = checks.get("ptp", {}).get("ok")
        online_ok = checks.get("modules_online", {}).get("ok")
        if ptp_ok and online_ok:
            return
        detail = (checks.get("ptp", {}).get("detail")
                  or checks.get("modules_online", {}).get("detail"))
        print(f"  ptp/online not ready ({detail}); retry in 15s")
        time.sleep(15)
    raise RuntimeError("PTP/online gate never passed")


def wait_stopped(api: Api, session_name: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = api.get(f"/sessions/{session_name}").get("state")
        if st == "stopped":
            return
        if st in ("error",):
            raise RuntimeError(f"session {session_name} -> {st}")
        time.sleep(5)
    raise RuntimeError(f"session {session_name} not stopped after {timeout}s")


def wait_for_report(args, session_name: str, timeout: float) -> str:
    """Poll the share until the DATE-DIR framesync_report.json (the one
    collect() reads -- the session-root copy is a rollup with a thinner
    schema) and both per-camera _recording.json exist. Returns the date dir."""
    deadline = time.monotonic() + timeout
    sess = f"{args.share_path}/{session_name}"
    rec_q = f'find "{sess}" -mindepth 2 -name "*_recording.json" 2>/dev/null | wc -l'
    # the report inside a YYYYMMDD dir, not the one at the session root
    fr_q = (f'find "{sess}" -mindepth 2 -maxdepth 2 '
            f'-name "framesync_report.json" 2>/dev/null | head -1')
    last = ""
    while time.monotonic() < deadline:
        n_rec = ssh(args.ssh_host, rec_q).strip()
        fr = ssh(args.ssh_host, fr_q).strip()
        if n_rec.isdigit() and int(n_rec) >= 2 and fr:
            return posixpath.dirname(fr)
        last = f"recording.json={n_rec} datedir_report={'yes' if fr else 'no'}"
        time.sleep(10)
    print(f"  ! export/report wait timed out ({last}); collecting best-effort")
    return find_session_dir(args.ssh_host, args.share_path, session_name)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def restore(api: Api, snapshots: dict) -> None:
    print("\nRestoring original camera config ...")
    for mid, cfg in snapshots.items():
        try:
            api.patch_config(mid, cfg, wait=20)
            print(f"  {mid} restored")
        except Exception as e:                          # noqa: BLE001
            print(f"  ! {mid} restore failed: {e}")
            print(f"    -> restore {mid} by hand from config_snapshot.json")


def analyse(rows: list[dict]) -> None:
    if not rows:
        return
    print("\n" + "=" * 78)
    print("SUMMARY  (mean +/- sd over repeats)")
    print("=" * 78)
    from collections import defaultdict
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["fps"], r["infer"])].append(r)

    def col(g, field, scale=1.0):
        vals = []
        for r in g:
            v = r.get(field)
            if v in (None, "", "None"):
                continue
            vals.append(float(v) * scale)
        if not vals:
            return "   n/a "
        m = sum(vals) / len(vals)
        sd = (sum((x - m) ** 2 for x in vals) / len(vals)) ** 0.5
        return f"{m:6.1f}+/-{sd:4.1f}"

    hdr = (f"{'fps':>4} {'infer':>6} | {'clientDeficit':>13} {'droppedBefore':>13} "
           f"{'rate_cv(x1e3)':>13} {'detrended_p95us':>15} {'drift_us/s':>11}")
    print(hdr)
    print("-" * len(hdr))
    for key in sorted(groups):
        g = groups[key]
        print(f"{key[0]:>4} {key[1]:>6} | "
              f"{col(g, 'client_deficit'):>13} "
              f"{col(g, 'client_dropped_before'):>13} "
              f"{col(g, 'client_rate_cv', 1e3):>13} "
              f"{col(g, 'pair_detrended_p95_us'):>15} "
              f"{col(g, 'pair_drift_us_per_s'):>11}")
    print("\nInterpretation: client_deficit (CSV rows - .ts frames) is the compose "
          "lag in frames. If 'off' rows show ~0 and it climbs with inference load, "
          "the inference thread is starving the encoder.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://10.0.0.1:5000")
    p.add_argument("--token", default=os.environ.get("SAVIOUR_TOKEN", ""))
    p.add_argument("--ssh-host", default="pi@10.0.0.1")
    p.add_argument("--share-path", default="/home/pi/controller_share")
    p.add_argument("--server-cam", default="camera_d074",
                   help="module_id of the sync-server / frameserver camera")
    p.add_argument("--client-cam", default="hailo_camera_3606",
                   help="module_id of the sync-client (hailo) camera")
    p.add_argument("--target", default="all",
                   help="session target: 'all', a group, or a module_id "
                        "(must resolve to BOTH cameras). Ignored if --group is set.")
    p.add_argument("--group",
                   help="put both cameras in this module.group for the run and "
                        "target it, so only the 2 cameras record (no mic/ttl "
                        "export noise). Original group restored at the end.")
    p.add_argument("--session-prefix", default="fsweep")
    p.add_argument("--out", default=f"./framesync_sweep_{datetime.now():%Y%m%d_%H%M%S}")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--fps", default="30,60", help="comma list, outer loop")
    p.add_argument("--duration-min", type=int, default=1)
    p.add_argument("--matrix",
                   help="JSON file with a custom point list (see default_matrix)")
    p.add_argument("--settle-secs", type=int, default=300,
                   help="wait after an fps change for phc2sys to reconverge")
    p.add_argument("--patch-wait", type=float, default=20.0)
    p.add_argument("--sync-timeout", type=float, default=60.0)
    p.add_argument("--ptp-gate-timeout", type=float, default=360.0)
    p.add_argument("--stop-grace", type=float, default=120.0)
    p.add_argument("--export-timeout", type=float, default=360.0)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not args.token:
        sys.exit("no token: pass --token or set SAVIOUR_TOKEN")

    fps_values = [int(x) for x in args.fps.split(",") if x.strip()]
    if args.matrix:
        with open(args.matrix) as f:
            matrix = json.load(f)
    else:
        matrix = default_matrix(args.repeats, fps_values)

    # resume: skip labels already present (non-FAILED) in an existing
    # results.csv under --out, so a re-run continues after a network drop.
    os.makedirs(args.out, exist_ok=True)
    results_path = os.path.join(args.out, "results.csv")
    done_rows: list[dict] = []
    if os.path.exists(results_path):
        with open(results_path, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("label") and not (r.get("notes") or "").startswith("FAILED"):
                    done_rows.append(r)
        done = {r["label"] for r in done_rows}
        if done:
            matrix = [p for p in matrix if p["label"] not in done]
            print(f"resume: {len(done)} runs already in {results_path}, "
                  f"{len(matrix)} left")

    print(f"{len(matrix)} runs planned:")
    for pt in matrix:
        print(f"  {pt['label']}")
    if args.dry_run:
        return

    api = Api(args.base_url, args.token)

    # sanity: reachable + both cameras known
    state = api.get("/state")
    print(f"\ncontroller {args.base_url}  version={state.get('version')}  "
          f"modules online={state['modules']['online']}")
    for mid in (args.server_cam, args.client_cam):
        api.get(f"/modules/{mid}")   # 404s if unknown

    # snapshot
    snapshots = {}
    for mid in (args.server_cam, args.client_cam):
        cfg = api.get_config(mid)["config"]
        snapshots[mid] = cfg
    snap_path = os.path.join(args.out, "config_snapshot.json")
    with open(snap_path, "w") as f:
        json.dump(snapshots, f, indent=2)
    print(f"snapshot -> {snap_path}")
    if "hailo" not in snapshots[args.client_cam]:
        print("  ! WARNING: client camera config has no 'hailo' section -- "
              "is --client-cam really the hailo camera? continuing.")

    if args.group:
        print(f"putting both cameras in group '{args.group}' for the run")
        for mid in (args.server_cam, args.client_cam):
            api.patch_config(mid, {"module": {"group": args.group}}, wait=20)
        api.wait_synced([args.server_cam, args.client_cam], timeout=args.sync_timeout)
        args.target = args.group

    rows: list[dict] = list(done_rows)
    prev_fps: int | None = None
    new_file = not done_rows
    try:
        with open(results_path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
            if new_file:
                w.writeheader()
            for i, point in enumerate(matrix, 1):
                print(f"\n----- run {i}/{len(matrix)} -----")
                try:
                    row, prev_fps = run_point(api, args, point, prev_fps)
                except Exception as e:                  # noqa: BLE001
                    print(f"  !! run failed: {e}")
                    row = {k: "" for k in RESULT_FIELDS}
                    row.update({"label": point["label"], "fps": point["fps"],
                                "infer": point["infer"], "notes": f"FAILED: {e}"})
                rows.append(row)
                w.writerow(row)
                f.flush()
    finally:
        restore(api, snapshots)
        with open(os.path.join(args.out, "results.json"), "w") as f:
            json.dump(rows, f, indent=2)
        analyse(rows)
        print(f"\nresults: {results_path}")


if __name__ == "__main__":
    main()
