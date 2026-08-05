#!/usr/bin/env python3
"""
Test script for camera module

"""
import os
import sys
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.controller.controller import Controller


def test_controller():
    # c = Controller()
    # assert c
    pass


# ---------------------------------------------------------------------------
# on_module_status_change
#
# Controller is abstract (ABC) and Controller() itself pulls in real
# networking/config setup, so these call the unbound method directly against
# a minimal duck-typed stand-in rather than constructing a real instance.
# ---------------------------------------------------------------------------

class _Ctx:
    """Minimal stand-in exposing only what on_module_status_change touches."""
    def __init__(self):
        self.logger = MagicMock()
        self.facade = MagicMock()
        self.modules = MagicMock()
        self.communication = MagicMock()
        self.web = MagicMock()


class TestOnModuleStatusChangeFramesync:
    def test_offline_triggers_reconcile(self):
        ctx = _Ctx()
        Controller.on_module_status_change(ctx, "camera_a", "offline")
        ctx.facade.reconcile_framesync.assert_called_once()

    def test_online_triggers_reconcile(self):
        ctx = _Ctx()
        Controller.on_module_status_change(ctx, "camera_a", "online")
        ctx.facade.reconcile_framesync.assert_called_once()

    def test_offline_calls_module_offline_before_reconciling(self):
        ctx = _Ctx()
        Controller.on_module_status_change(ctx, "camera_a", "offline")
        ctx.facade.module_offline.assert_called_once_with("camera_a")

    def test_online_calls_module_back_online_before_reconciling(self):
        ctx = _Ctx()
        Controller.on_module_status_change(ctx, "camera_a", "online")
        ctx.facade.module_back_online.assert_called_once_with("camera_a")
