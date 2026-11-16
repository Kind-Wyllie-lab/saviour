#!/usr/bin/env python3
"""
Check a TTL session-protocol run against its plan (plans/session-protocol.md).

Reads a TTL module's exported files from one session/date folder:
  <...>_protocol.json  the plan (session_start_at + every planned edge)
  <...>.csv            the TTL events (protocol output rows, input edges)

and reports, per protocol output edge:
  - output lateness: the module's own stamp after setting the pin, minus
    session_start_at + t (what the runner logs as late_ms);
  - with --loopback-pin N (an input pin jumpered to the scheduled output):
    the matching input edge, so lateness as seen by an independent input,
    and missing/extra edges.

Main env only (stdlib). Usage:
  python3 tools/ttl_protocol_check.py <session>/<DATE>/<ttl_module> \
      [--loopback-pin 3] [--active-logic active_low] [--csv out.csv]
"""

import argparse
import csv
import glob
import json
import os
import statistics
import sys


def _pct(values, p):
    if not values:
        return float("nan")
    s = sorted(values)
    k = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[k]


def _summary(name, values_ms):
    if not values_ms:
        return f"{name}: no data"
    return (f"{name}: n={len(values_ms)} median={statistics.median(values_ms):.3f} "
            f"p95={_pct(values_ms, 95):.3f} p99={_pct(values_ms, 99):.3f} "
            f"max={max(values_ms):.3f} min={min(values_ms):.3f} ms")


def load(folder):
    plans = sorted(glob.glob(os.path.join(folder, "*_protocol.json")))
    if not plans:
        sys.exit(f"no *_protocol.json in {folder}")
    csvs = sorted(p for p in glob.glob(os.path.join(folder, "*.csv"))
                  if "health_metadata" not in p)
    if not csvs:
        sys.exit(f"no TTL events CSV in {folder}")
    rows = []
    for path in csvs:  # segments, in name order
        with open(path, newline="") as f:
            reader = csv.reader(f)
            next(reader, None)
            for r in reader:
                if len(r) >= 4:
                    rows.append((int(r[0]), int(r[1]), r[3], ",".join(r[4:])))
    rows.sort()
    return [json.load(open(p)) for p in plans], rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder")
    ap.add_argument("--loopback-pin", type=int)
    ap.add_argument("--active-logic", default="active_low",
                    choices=["active_low", "active_high"])
    ap.add_argument("--match-window-ms", type=float, default=50.0,
                    help="max output→input delay to pair an edge (default 50)")
    ap.add_argument("--csv", help="write per-edge results here")
    args = ap.parse_args()

    plans, rows = load(args.folder)
    plan = plans[0]
    if len(plans) > 1:
        print(f"note: {len(plans)} protocol records (resumed run); "
              f"using the first for the plan, t0 is shared")
    t0 = plan["session_start_at"]
    edges = plan["edges"]
    out_pins = {e["pin"] for e in edges}
    outputs = [r for r in rows if r[1] in out_pins and "protocol step=" in r[3]]
    other = [r for r in rows if r[1] in out_pins and "protocol step=" not in r[3]]
    print(f"plan: {len(edges)} edges on pins {sorted(out_pins)}, "
          f"t0={t0:.6f}, end_state={plan.get('end_state')}, "
          f"resumed={plan.get('resumed')}, missed={plan.get('missed_edges')}")
    print(f"logged: {len(outputs)} protocol output rows, "
          f"{len(other)} other rows on those pins "
          f"({sorted({r[3].split('[')[-1].rstrip(']') for r in other})})")

    # Pair output rows with planned edges by step path and order.
    planned = {}
    for e in edges:
        planned.setdefault((e["pin"], e["step"]), []).append(e)
    results = []
    for ts, pin, _state, desc in outputs:
        step = desc.split("step=")[1].split()[0]
        queue = planned.get((pin, step))
        if not queue:
            print(f"unplanned output row: {ts} pin {pin} {desc}")
            continue
        e = queue.pop(0)
        target = t0 + e["t_s"]
        results.append({"t_s": e["t_s"], "pin": pin, "level": e["level"],
                        "step": step, "out_ns": ts,
                        "out_late_ms": (ts / 1e9 - target) * 1000})
    unfired = sum(len(q) for q in planned.values())
    out_late = [r["out_late_ms"] for r in results]
    print(_summary("output lateness", out_late))
    print(f"planned edges never logged: {unfired}")

    if args.loopback_pin is not None:
        inputs = [r for r in rows if r[1] == args.loopback_pin]
        # Input rows name the electrical level; active "high" is electrical
        # LOW under active_low.
        def electrical(level):
            high = level == "high"
            if args.active_logic == "active_low":
                high = not high
            return "TTLValue.HIGH" if high else "TTLValue.LOW"
        j = 0
        window = args.match_window_ms * 1e6
        for r in results:
            want = electrical(r["level"])
            while j < len(inputs) and inputs[j][0] < r["out_ns"] - 1e6:
                j += 1
            k = j
            while k < len(inputs) and inputs[k][0] <= r["out_ns"] + window:
                if inputs[k][2] == want:
                    r["in_ns"] = inputs[k][0]
                    r["in_delay_ms"] = (inputs[k][0] - r["out_ns"]) / 1e6
                    r["in_late_ms"] = (inputs[k][0] / 1e9 - (t0 + r["t_s"])) * 1000
                    j = k + 1
                    break
                k += 1
        matched = [r for r in results if "in_ns" in r]
        print(f"loopback pin {args.loopback_pin}: {len(inputs)} input edges, "
              f"{len(matched)}/{len(results)} output edges matched, "
              f"{len(inputs) - len(matched)} unmatched input edges")
        print(_summary("input lateness vs plan", [r["in_late_ms"] for r in matched]))
        print(_summary("output→input callback delay",
                       [r["in_delay_ms"] for r in matched]))

    if results:
        # Drift: lateness over the run, first vs last tenth.
        n = max(1, len(results) // 10)
        first = statistics.median(out_late[:n])
        last = statistics.median(out_late[-n:])
        print(f"drift: median output lateness first 10% {first:.3f} ms, "
              f"last 10% {last:.3f} ms (run {results[-1]['t_s']:.1f} s)")
        worst = sorted(results, key=lambda r: -r["out_late_ms"])[:5]
        print("worst output edges: " + ", ".join(
            f"t={r['t_s']:.3f}s step {r['step']} {r['out_late_ms']:.2f}ms"
            for r in worst))

    if args.csv:
        keys = ["t_s", "pin", "level", "step", "out_ns", "out_late_ms",
                "in_ns", "in_delay_ms", "in_late_ms"]
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in results:
                w.writerow({k: r.get(k, "") for k in keys})


if __name__ == "__main__":
    main()
