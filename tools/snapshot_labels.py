#!/usr/bin/env python3
"""
Snapshot current module name/label assignments from the running controller.

Outputs three tables:
  1. Camera modules       — module_id, name (A1–D4), IP, version
  2. Microphone modules   — module_id, name (Col_1–Col_4), IP, version
  3. AudioMoth labels     — serial number, label (A1–D4), microphone module

Also writes a timestamped JSON archive to the same directory.

Usage (run on the controller):
    python3 tools/snapshot_labels.py [--url http://localhost:5000]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import urllib.request
except ImportError:
    print("ERROR: urllib not available", file=sys.stderr)
    sys.exit(1)


def fetch_modules(url: str) -> dict:
    req = urllib.request.urlopen(f"{url}/facade/list_modules", timeout=5)
    data = json.loads(req.read().decode())
    return data.get("modules", {})


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:5000",
                        help="Controller base URL (default: http://localhost:5000)")
    parser.add_argument("--out", default=None,
                        help="Directory to write JSON archive (default: same dir as this script)")
    args = parser.parse_args()

    try:
        modules = fetch_modules(args.url)
    except Exception as e:
        print(f"ERROR: could not reach controller at {args.url}: {e}", file=sys.stderr)
        sys.exit(1)

    cameras = sorted(
        [m for m in modules.values() if m.get("type") == "camera"],
        key=lambda m: m.get("name", m["id"])
    )
    microphones = sorted(
        [m for m in modules.values() if m.get("type") == "microphone"],
        key=lambda m: m.get("name", m["id"])
    )

    print(f"\n=== Snapshot taken {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    # ── Cameras ──────────────────────────────────────────────────────────────
    print(f"CAMERAS ({len(cameras)})")
    cam_rows = [
        [m["id"], m.get("name", "—"), m.get("ip", "—"), m.get("version", "—"), m.get("status", "—")]
        for m in cameras
    ]
    print_table(["Module ID", "Name", "IP", "Version", "Status"], cam_rows)

    # ── Microphone modules ────────────────────────────────────────────────────
    print(f"\nMICROPHONE MODULES ({len(microphones)})")
    mic_rows = [
        [m["id"], m.get("name", "—"), m.get("ip", "—"), m.get("version", "—"), m.get("status", "—")]
        for m in microphones
    ]
    print_table(["Module ID", "Name", "IP", "Version", "Status"], mic_rows)

    # ── AudioMoth labels ──────────────────────────────────────────────────────
    audiomoth_rows = []
    for m in microphones:
        labels: dict = m.get("config", {}).get("audiomoth_labels", {})
        for serial, label in sorted(labels.items(), key=lambda kv: kv[1]):
            audiomoth_rows.append([label, serial, m.get("name", m["id"]), m["id"]])

    audiomoth_rows.sort(key=lambda r: r[0])

    total_moths = sum(len(m.get("config", {}).get("audiomoth_labels", {})) for m in microphones)
    print(f"\nAUDIOMOTH LABELS ({total_moths})")
    print_table(["Label", "Serial", "Mic module name", "Mic module ID"], audiomoth_rows)

    # ── JSON archive ──────────────────────────────────────────────────────────
    out_dir = Path(args.out) if args.out else Path(__file__).parent
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_path = out_dir / f"label_snapshot_{timestamp}.json"

    archive = {
        "snapshot_time": datetime.now().isoformat(),
        "cameras": {m["id"]: {"name": m.get("name"), "ip": m.get("ip"), "version": m.get("version")} for m in cameras},
        "microphones": {m["id"]: {"name": m.get("name"), "ip": m.get("ip"), "version": m.get("version")} for m in microphones},
        "audiomoth_labels": {
            m["id"]: {
                "module_name": m.get("name"),
                "labels": m.get("config", {}).get("audiomoth_labels", {})
            }
            for m in microphones
        },
    }

    with open(archive_path, "w") as f:
        json.dump(archive, f, indent=2)

    print(f"\nArchive written to: {archive_path}\n")


if __name__ == "__main__":
    main()
