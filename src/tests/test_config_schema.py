"""
Config-schema regression tests (v1.0 roadmap C4).

Runs every *shipped* config file -- src/{modules,controller}/base_config.json
and each variant's <variant>[_controller]_config.json -- through the real
Config loaders, rather than the synthetic dicts test_config.py uses, so a
renamed / removed / malformed key in a real file fails CI instead of
silently turning into a `config.get() returning None` on a deployed Pi.

Four checks, each parametrised over every deployable variant (a directory
with a variant.conf -- what saviour-config actually offers):

1. Every shipped config file parses as a JSON object.
2. A variant config never replaces a base section (dict) with a scalar or
   vice versa -- the merge would silently discard one side.
3. First boot (no active_config.json) and upgrade (an old active config
   missing a since-added key and carrying a since-removed one) both end up
   with every schema key present.
4. Every literal `self.config.get("a.b")` read *without* a default in
   non-test code names a key that exists in the schema that code runs
   against: base + its own variant for code under variants/<v>/, base + any
   variant for shared code. A read with an explicit default is treated as
   deliberately optional.
"""

import json
import os
import re
from pathlib import Path

import pytest

from src.controller.config import Config as ControllerConfig
from src.modules.config import Config as ModuleConfig

REPO = Path(__file__).resolve().parents[2]
SIDES = {
    "modules": {"root": REPO / "src" / "modules", "suffix": "_config.json"},
    "controller": {"root": REPO / "src" / "controller", "suffix": "_controller_config.json"},
}

# `<receiver>.config.get("literal"` -- only the Config object, not any dict
# that happens to be named *config (pin_config.get(...) etc.).
_GET_RE = re.compile(
    r"""(?<![A-Za-z_])(?:self|self\.module|self\.controller|web|controller|module)"""
    r"""\.config\.get\(\s*["']([A-Za-z0-9_.]+)["']\s*(\)|,)"""
)


def _variants(side: str) -> list[str]:
    root = SIDES[side]["root"] / "variants"
    return sorted(p.parent.name for p in root.glob("*/variant.conf"))


def _variant_config_path(side: str, variant: str) -> Path:
    return SIDES[side]["root"] / "variants" / variant / f"{variant}{SIDES[side]['suffix']}"


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _keys(d: dict, prefix: str = "") -> set[str]:
    """Every dotted path in d (sections and leaves). A `_key` is also
    reachable as `key`, mirroring Config.get()'s underscore fallback."""
    out = set()
    for k, v in d.items():
        for name in {k, k.lstrip("_")}:
            path = f"{prefix}{name}"
            out.add(path)
            if isinstance(v, dict):
                out |= _keys(v, path + ".")
    return out


def _leaves(d: dict, prefix: str = "") -> set[str]:
    out = set()
    for k, v in d.items():
        path = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out |= _leaves(v, path + ".")
        else:
            out.add(path)
    return out


def _has(d: dict, dotted: str) -> bool:
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return False
        d = d[part]
    return True


def _drop(d: dict, dotted: str) -> None:
    *parents, last = dotted.split(".")
    for part in parents:
        d = d[part]
    del d[last]


def _merged(base: dict, variant: dict) -> dict:
    out = json.loads(json.dumps(base))

    def merge(dst, src):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            else:
                dst[k] = v

    merge(out, variant)
    return out


_CASES = [(side, v) for side in SIDES for v in _variants(side)]
_IDS = [f"{side}:{v}" for side, v in _CASES]


def _make_config(side: str, variant: str, active_path: str):
    """Load base + variant through the real Config class, the way each
    variant's entrypoint does at startup."""
    base_path = str(SIDES[side]["root"] / "base_config.json")
    variant_path = str(_variant_config_path(side, variant))
    if side == "modules":
        cfg = ModuleConfig(base_config_path=base_path, active_config_path=active_path)
        cfg.load_module_config(variant_path)
    else:
        cfg = ControllerConfig(base_config_path=base_path, active_config_path=active_path)
        cfg.load_controller_config(variant_path)
    return cfg


# ── 1. parse ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("side", list(SIDES))
def test_base_config_is_a_json_object(side):
    assert isinstance(_load(SIDES[side]["root"] / "base_config.json"), dict)


@pytest.mark.parametrize(("side", "variant"), _CASES, ids=_IDS)
def test_variant_config_exists_and_is_a_json_object(side, variant):
    path = _variant_config_path(side, variant)
    assert path.is_file(), f"{variant} has a variant.conf but no {path.name}"
    assert isinstance(_load(path), dict), f"{path} must be a JSON object (use {{}} if empty)"


# ── 2. no section/scalar clashes ──────────────────────────────────────────────

@pytest.mark.parametrize(("side", "variant"), _CASES, ids=_IDS)
def test_variant_does_not_change_the_shape_of_a_base_key(side, variant):
    base = _load(SIDES[side]["root"] / "base_config.json")
    var = _load(_variant_config_path(side, variant))
    clashes = []

    def walk(b, v, prefix):
        for k, vv in v.items():
            if k in b and isinstance(b[k], dict) != isinstance(vv, dict):
                clashes.append(prefix + k)
            elif k in b and isinstance(vv, dict):
                walk(b[k], vv, prefix + k + ".")

    walk(base, var, "")
    assert not clashes, f"section/scalar mismatch vs base_config.json: {clashes}"


# ── 3. first boot + upgrade produce the full schema ──────────────────────────

@pytest.mark.parametrize(("side", "variant"), _CASES, ids=_IDS)
def test_first_boot_active_config_has_every_schema_key(side, variant, tmp_path):
    base = _load(SIDES[side]["root"] / "base_config.json")
    schema = _merged(base, _load(_variant_config_path(side, variant)))
    active = tmp_path / "active_config.json"

    cfg = _make_config(side, variant, str(active))

    missing = sorted(k for k in _leaves(schema) if not _has(cfg.config, k))
    assert not missing, f"missing after first-boot load: {missing}"
    assert active.is_file(), "load should persist active_config.json"


@pytest.mark.parametrize(("side", "variant"), _CASES, ids=_IDS)
def test_upgrade_from_an_older_active_config_fills_every_schema_key(
        side, variant, tmp_path):
    """An active_config.json written by an older release -- a key that has
    since been added is absent, and one that has since been removed is still
    there -- must still load with every current schema key present."""
    base = _load(SIDES[side]["root"] / "base_config.json")
    schema = _merged(base, _load(_variant_config_path(side, variant)))
    old = json.loads(json.dumps(schema))
    # "Since added": drop one public leaf from each top-level section.
    dropped = []
    for section, body in schema.items():
        if isinstance(body, dict):
            leaf = next((k for k, v in body.items()
                         if not k.startswith("_") and not isinstance(v, dict)), None)
            if leaf:
                _drop(old, f"{section}.{leaf}")
                dropped.append(f"{section}.{leaf}")
    # "Since removed": a key no current file declares.
    old["removed_in_an_earlier_release"] = {"stale": 1}
    active = tmp_path / "active_config.json"
    active.write_text(json.dumps(old), encoding="utf-8")

    cfg = _make_config(side, variant, str(active))

    missing = sorted(k for k in _leaves(schema) if not _has(cfg.config, k))
    assert not missing, f"missing after upgrade load (dropped {dropped}): {missing}"
    if side == "modules":
        # Only the module Config prunes keys no current file declares.
        assert "removed_in_an_earlier_release" not in cfg.config


# ── 4. code reads only keys the schema declares ──────────────────────────────

def _code_key_misses(side: str) -> dict[str, list[str]]:
    root = SIDES[side]["root"]
    base = _keys(_load(root / "base_config.json"))
    per_variant = {v: _keys(_load(_variant_config_path(side, v))) for v in _variants(side)}
    any_variant = set().union(*per_variant.values())
    misses = {}
    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(root).parts
        if "tests" in rel or "node_modules" in rel or "frontend" in rel:
            continue
        if rel[0] == "variants":
            if rel[1] not in per_variant:
                continue  # no variant.conf -- not deployable (template, arduino)
            schema = base | per_variant[rel[1]]
        else:
            schema = base | any_variant
        bad = sorted({key for key, tail in _GET_RE.findall(py.read_text(encoding="utf-8"))
                      if tail == ")" and key not in schema})
        if bad:
            misses[str(py.relative_to(REPO))] = bad
    return misses


@pytest.mark.parametrize("side", list(SIDES))
def test_code_only_reads_declared_config_keys(side):
    misses = _code_key_misses(side)
    assert not misses, (
        "config.get() of a key no shipped config declares (add it to the "
        "right *_config.json, or pass an explicit default if it's genuinely "
        f"optional): {misses}"
    )


def test_code_key_scan_actually_finds_reads():
    """Guard against the regex silently matching nothing."""
    text = (SIDES["modules"]["root"] / "recording.py").read_text(encoding="utf-8")
    assert len(_GET_RE.findall(text)) > 5
    assert os.path.basename(__file__) == "test_config_schema.py"
