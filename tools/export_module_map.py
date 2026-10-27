#!/usr/bin/env python3
"""
export_module_map.py -- dump a controller's module registry to CSV.

One row per module: module_id, name, type, plus the habitat grid cell (A1-D4)
when the module's name is one -- the habitat UI places a module in a cell by
name, so this is the current id -> box mapping. Also group, ip, online,
status and version.

This is the live registry (GET /api/v1/modules), not a record of what a past
session used; for that, the share's <session>/<date>/<module_name>/ folders
are the history.

Auth: --token, or $SAVIOUR_TOKEN. Bearer = the admin password or an API
token (see docs/REST_API.md). Plain HTTP, LAN only.

Example:
  export SAVIOUR_TOKEN=...
  python tools/export_module_map.py --url http://10.0.0.1:5000 --out modules.csv

Dependencies: requests.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

FIELDS = ["module_id", "name", "type", "cell", "group", "ip", "online",
          "status", "version"]
CELL_RE = re.compile(r"^[A-D][1-4]$")


def fetch_modules(base_url: str, token: str, timeout: float) -> dict:
    r = requests.get(base_url.rstrip("/") + "/api/v1/modules",
                     headers={"Authorization": f"Bearer {token}"},
                     timeout=timeout)
    r.raise_for_status()
    return r.json()


def to_rows(modules: dict) -> list[dict]:
    rows = []
    for module_id, m in modules.items():
        name = m.get("name") or module_id
        # Exact match, as HabitatLivestreamGrid does: "a1" is not placed in A1
        cell = name
        rows.append({
            "module_id": module_id,
            "name": name,
            "type": m.get("type", ""),
            "cell": cell if CELL_RE.match(cell) else "",
            "group": m.get("group", ""),
            "ip": m.get("ip", ""),
            "online": m.get("online", ""),
            "status": m.get("status", ""),
            "version": m.get("version", ""),
        })
    # Grouped by type, then grid order (A1, A2, ... D4), then unplaced by name
    rows.sort(key=lambda r: (r["type"], r["cell"] == "", r["cell"], r["name"]))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--url", default="http://10.0.0.1:5000",
                    help="controller base URL (default %(default)s)")
    ap.add_argument("--token", default=os.environ.get("SAVIOUR_TOKEN"),
                    help="bearer token (default $SAVIOUR_TOKEN)")
    ap.add_argument("--out", help="CSV path (default stdout)")
    ap.add_argument("--type", action="append",
                    help="only these module types (repeatable), e.g. "
                         "--type habitat_camera --type microphone")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    if not args.token:
        sys.exit("no token: pass --token or set SAVIOUR_TOKEN")

    try:
        modules = fetch_modules(args.url, args.token, args.timeout)
    except requests.RequestException as e:
        sys.exit(f"GET /api/v1/modules failed: {e}")

    rows = to_rows(modules)
    if args.type:
        rows = [r for r in rows if r["type"] in args.type]

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} modules to {args.out}", file=sys.stderr)
    else:
        writer = csv.DictWriter(sys.stdout, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
