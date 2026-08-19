#!/usr/bin/env python3
"""
Download a labelled dataset from Roboflow into the standard Ultralytics
YOLO layout that train.py expects.

Roboflow's own YOLO export already produces a compatible directory shape
(train/images + train/labels, valid/images + valid/labels, plus a
data.yaml with train/val paths and class names) -- Ultralytics reads it
directly, no manual reshaping needed. This module automates the download
and patches data.yaml's `path` to an absolute path so training works
regardless of the working directory you later run train.py from (matching
the pattern in dataset.yaml.example).

Usable two ways:
  - Standalone, to just fetch (or list) a dataset without training yet --
    see the CLI usage below.
  - train.py imports download_dataset()/get_project() directly so
    `python train.py --roboflow-workspace ... --roboflow-project ...
    --roboflow-version N` can download and train in one command.

Requires the `roboflow` and `pyyaml` packages, plus an API key:
    pip install roboflow pyyaml

Get an API key from https://app.roboflow.com/settings/api and set it in
the environment (never pass it on the command line -- it'll end up in
shell history):
    export ROBOFLOW_API_KEY=...        # bash
    $env:ROBOFLOW_API_KEY = "..."      # PowerShell

Usage:
    # list available versions for a project, then exit
    python download_roboflow_dataset.py --workspace sidb-workshop \
        --project rat-tracker-zh4ex

    # download a specific version
    python download_roboflow_dataset.py --workspace sidb-workshop \
        --project rat-tracker-zh4ex --version 1 --out training_data

Then train:
    python train.py --data training_data/data.yaml
"""

import argparse
import os
import sys
from pathlib import Path


def get_roboflow_client(api_key: str | None = None):
    try:
        from roboflow import Roboflow
    except ImportError:
        raise RuntimeError("roboflow not installed: pip install roboflow")

    api_key = api_key or os.environ.get("ROBOFLOW_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ROBOFLOW_API_KEY not set in the environment. Get a key from "
            "https://app.roboflow.com/settings/api and export it."
        )
    return Roboflow(api_key=api_key)


def get_project(workspace: str, project: str, api_key: str | None = None):
    rf = get_roboflow_client(api_key)
    return rf.workspace(workspace).project(project)


def list_versions(project, flag_name: str = "--version") -> None:
    versions = project.versions()
    if not versions:
        print("[ERROR] No versions found for this project -- generate one "
              "from the Roboflow project's 'Versions' tab first.")
        return
    print(f"Available versions for {project.id}:")
    for v in versions:
        # v.id is like "workspace/project/N"
        num = v.id.rsplit("/", 1)[-1]
        print(f"  {flag_name} {num}   ({v.name}, {v.images} images)")
    print(f"\nRe-run with {flag_name} <N> to download.")


def patch_data_yaml_path(data_yaml: Path, dataset_root: Path) -> None:
    try:
        import yaml
    except ImportError:
        raise RuntimeError("pyyaml not installed: pip install pyyaml")
    with open(data_yaml) as f:
        config = yaml.safe_load(f)
    config["path"] = str(dataset_root.resolve())
    with open(data_yaml, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)


def download_dataset(project, version: int, fmt: str, out: Path) -> Path:
    """Download `project`'s given version into `out`, patch data.yaml's
    `path` to absolute, and return the path to that data.yaml."""
    print(f"Downloading {project.id} v{version} ({fmt}) -> {out}")
    dataset = project.version(version).download(fmt, location=str(out))

    data_yaml = Path(dataset.location) / "data.yaml"
    if not data_yaml.exists():
        raise RuntimeError(
            f"Expected {data_yaml} not found -- check {dataset.location} "
            f"for whatever yaml Roboflow produced and pass it to "
            f"train.py --data directly."
        )
    patch_data_yaml_path(data_yaml, Path(dataset.location))
    return data_yaml


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", required=True,
                         help="Roboflow workspace slug, e.g. sidb-workshop")
    parser.add_argument("--project", required=True,
                         help="Roboflow project slug, e.g. rat-tracker-zh4ex")
    parser.add_argument("--version", type=int, default=None,
                         help="Dataset version number. Omit to list "
                              "available versions and exit.")
    parser.add_argument("--format", default="yolov11",
                         help="Roboflow export format (default: yolov11). "
                              "Ultralytics can also read a yolov8 export "
                              "unchanged if yolov11 isn't offered for this "
                              "project.")
    parser.add_argument("--out", type=Path, default=Path("training_data"),
                         help="Destination directory (default: training_data)")
    args = parser.parse_args()

    try:
        project = get_project(args.workspace, args.project)

        if args.version is None:
            list_versions(project)
            return

        data_yaml = download_dataset(project, args.version, args.format, args.out)
    except RuntimeError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    print(f"""
Done! Dataset downloaded to:
  {data_yaml.parent}

Next: train --
  python train.py --data {data_yaml}
""")


if __name__ == "__main__":
    main()
