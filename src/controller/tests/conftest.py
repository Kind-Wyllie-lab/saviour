"""Shared fixtures for the controller tests."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_storage_history(tmp_path, monkeypatch):
    """Web loads and saves the Storage page's history at a fixed path under
    /var/lib/saviour; on a dev machine that folder can exist (other tests
    create it), so point it at a per-test file instead of sharing state."""
    try:
        from src.controller.web import Web
    except Exception:  # noqa: BLE001 -- tests that don't need web still run
        return
    monkeypatch.setattr(Web, "_STORAGE_HISTORY_FILE",
                        str(tmp_path / "storage_history.json"))
