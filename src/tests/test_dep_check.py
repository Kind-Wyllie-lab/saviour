"""Tests for src/shared/dep_check.py (offline dependency check run on update)."""

import pytest

from src.shared import dep_check


def _write(tmp_path, deps):
    lines = ",\n".join(f"    '{d}'" for d in deps)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "x"\ndependencies = [\n{lines}\n]\n')
    return str(tmp_path)


def test_all_installed(tmp_path):
    assert dep_check.missing_dependencies(_write(tmp_path, ["pytest>=1.0"])) == []


def test_not_installed(tmp_path):
    root = _write(tmp_path, ["pytest", "definitely-not-a-real-pkg-xyz>=1"])
    assert dep_check.missing_dependencies(root) == ["definitely-not-a-real-pkg-xyz>=1"]


def test_version_too_old(tmp_path):
    if dep_check.Requirement is None:
        pytest.skip("needs packaging")
    (missing,) = dep_check.missing_dependencies(_write(tmp_path, ["pytest>=9999"]))
    assert missing.startswith("pytest>=9999 (installed ")


def test_marker_not_applicable(tmp_path):
    if dep_check.Requirement is None:
        pytest.skip("needs packaging")
    root = _write(tmp_path, ['nope-xyz; python_version < "3.0"'])
    assert dep_check.missing_dependencies(root) == []


def test_name_only_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(dep_check, "Requirement", None)
    root = _write(tmp_path, ["pytest>=9999", "nope-xyz"])
    assert dep_check.missing_dependencies(root) == ["nope-xyz"]


def test_repo_pyproject_parses():
    # The real file must load; whatever it reports is environment-dependent.
    assert isinstance(dep_check.missing_dependencies("."), list)
