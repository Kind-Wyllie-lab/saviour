"""
Check an exported .nwb the way DANDI will: nwbinspector with its ``dandi``
config (what ``dandi validate`` / ``dandi upload`` run), plus the subject
checks DANDI's organiser needs that the inspector doesn't make.

Run in the NWB venv, like nwb_convert::

    env-nwb/bin/python -m src.controller.nwb_inspect /share/<session>/<session>.nwb [--json]

Prints the report (``--json``: one JSON object on the last line). The
controller saves it beside the file as ``<session>.nwb-inspection.json``
(nwb_export.inspection_path). Everything above ``inspect()`` imports
without pynwb / nwbinspector.

``dandi_ready`` is what DANDI blocks an upload on: no CRITICAL message and
no check that crashed (ERROR). Best-practice violations and suggestions are
reported but don't block.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

# nwbinspector Importance names, worst first.
IMPORTANCE = ("ERROR", "PYNWB_VALIDATION", "CRITICAL",
              "BEST_PRACTICE_VIOLATION", "BEST_PRACTICE_SUGGESTION")
BLOCKING = ("ERROR", "PYNWB_VALIDATION", "CRITICAL")
MAX_MESSAGES = 200
# What nwb_convert writes when the form leaves the subject ID blank.
PLACEHOLDER_SUBJECT_IDS = {"", "unknown", "none", "n/a", "na"}


def subject_messages(subject: dict | None) -> list[dict]:
    """DANDI requirements the inspector doesn't check: a real subject ID
    (DANDI files every asset under ``sub-<subject_id>``; a placeholder puts
    every session under one fake subject)."""
    out = []
    sid = str((subject or {}).get("subject_id") or "").strip()
    if sid.lower() in PLACEHOLDER_SUBJECT_IDS:
        out.append({
            "importance": "CRITICAL", "check": "saviour_subject_id",
            "message": ("Subject ID is missing (written as 'unknown'). DANDI files each "
                        "session under its subject ID: set it in the export form."),
            "location": "/general/subject", "object_type": "Subject",
            "object_name": "subject"})
    return out


def summarise(messages: list[dict], *, inspector_version: str | None) -> dict:
    """The saved report: counts per importance, the messages (worst first,
    capped) and the verdict."""
    rank = {name: i for i, name in enumerate(IMPORTANCE)}
    messages = sorted(messages, key=lambda m: rank.get(m["importance"], len(rank)))
    counts = {name: 0 for name in IMPORTANCE}
    for m in messages:
        counts[m["importance"]] = counts.get(m["importance"], 0) + 1
    return {
        "ran": True,
        "checked_at": time.time(),
        "inspector_version": inspector_version,
        "config": "dandi",
        "dandi_ready": not any(counts.get(k) for k in BLOCKING),
        "counts": counts,
        "messages": messages[:MAX_MESSAGES],
        "truncated": max(0, len(messages) - MAX_MESSAGES),
    }


def inspect(path: str) -> dict:
    """Run the checks on ``path`` (needs pynwb + nwbinspector)."""
    import nwbinspector
    from nwbinspector import inspect_nwbfile, load_config
    from pynwb import NWBHDF5IO

    messages = []
    for m in inspect_nwbfile(nwbfile_path=path, config=load_config("dandi")):
        messages.append({
            "importance": m.importance.name, "check": m.check_function_name,
            "message": str(m.message)[:500], "location": m.location,
            "object_type": m.object_type, "object_name": m.object_name})
    with NWBHDF5IO(path, "r") as io:
        s = io.read().subject
        subject = {"subject_id": s.subject_id} if s is not None else None
    messages += subject_messages(subject)
    return summarise(messages, inspector_version=getattr(nwbinspector, "__version__", None))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check an NWB file for DANDI")
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    try:
        report = inspect(args.path)
    except ImportError as e:
        report = {"ran": False, "error": f"nwbinspector is not installed ({e})"}
    except Exception as e:                                  # noqa: BLE001 -- reported to the caller
        report = {"ran": False, "error": f"{type(e).__name__}: {e}"}
    if args.json:
        print(json.dumps(report), flush=True)
    elif not report["ran"]:
        print(f"error: {report['error']}", file=sys.stderr)
    else:
        verdict = "ready for DANDI" if report["dandi_ready"] else "NOT ready for DANDI"
        print(f"{args.path}: {verdict}")
        for m in report["messages"]:
            print(f"  {m['importance']:<26} {m['check']}: {m['message']}")
    return 0 if report.get("ran") else 1


if __name__ == "__main__":
    sys.exit(main())
