"""Offline check of pyproject.toml's runtime dependencies against what is
installed. Replaces the `pip install --no-index <src>/` step both update paths
used to run: with no index pip can't install anything new anyway, and it
failed every time before getting that far, because pip's build isolation tries
to download the build-system requirements (`ERROR: Failed to build ... when
installing build dependencies`). Checking metadata needs no build, no network
and no git, and names exactly what a manual online install has to add.
"""

import importlib.metadata
import os
import re
import tomllib

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

try:
    from packaging.requirements import Requirement
except ImportError:  # name-only check without packaging
    Requirement = None


def missing_dependencies(src_root: str) -> list[str]:
    """Requirement strings from `[project].dependencies` that aren't
    installed, or (when `packaging` is importable) are installed at a version
    outside the specifier. Raises OSError / tomllib.TOMLDecodeError if
    pyproject.toml can't be read."""
    with open(os.path.join(src_root, "pyproject.toml"), "rb") as f:
        deps = tomllib.load(f).get("project", {}).get("dependencies", [])

    missing = []
    for dep in deps:
        if Requirement is not None:
            req = Requirement(dep)
            if req.marker is not None and not req.marker.evaluate():
                continue
            name, spec = req.name, req.specifier
        else:
            m = _NAME_RE.match(dep)
            if not m:
                continue
            name, spec = m.group(1), None
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(dep)
            continue
        if spec is not None and not spec.contains(installed, prereleases=True):
            missing.append(f"{dep} (installed {installed})")
    return missing
