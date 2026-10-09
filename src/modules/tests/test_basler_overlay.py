"""
Tests for BaslerCameraModule._overlay_timestamp placement
(basler.timestamp_position). Built via __new__ so no Basler device or
pypylon is needed -- the overlay is pure OpenCV.
"""

from unittest.mock import MagicMock

import numpy as np
import pytest

from src.modules.variants.basler_camera.basler_camera_module import (
    BaslerCameraModule,
)


def _module(position):
    m = BaslerCameraModule.__new__(BaslerCameraModule)
    cfg = {"basler.text_size": "medium"}
    if position is not None:
        cfg["basler.timestamp_position"] = position
    m.config = MagicMock()
    m.config.get.side_effect = lambda k, d=None: cfg.get(k, d)
    return m


@pytest.mark.parametrize("position,expect_top", [
    (None, True), ("top", True), ("bottom", False),
])
def test_overlay_position(position, expect_top):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    out = _module(position)._overlay_timestamp(frame, 1_791_000_000_123_000_000)
    rows = np.where(out.any(axis=(1, 2)))[0]
    assert rows.size
    if expect_top:
        assert rows.max() < 240
    else:
        assert rows.min() > 240 and rows.max() < 480
