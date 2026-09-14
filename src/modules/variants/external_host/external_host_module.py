#!/usr/bin/env python3
"""
SAVIOUR System - External Host Module

For a device on the fleet that needs to be genuinely visible and PTP-time-
disciplined the same way every camera/mic/TTL module is, but has no
recording data of its own to contribute. The motivating case: a Raspberry
Pi running pyControl (or any other third-party acquisition/behaviour
software) alongside a SAVIOUR rig, so its own event log can be compared
against SAVIOUR's recordings after the fact -- the two only need to agree
on wall-clock time, which PTP already gives for free once this Pi is on the
same domain. Not pyControl-specific: any lab PC/Pi that wants fleet-shared
time + dashboard visibility without owning a data stream fits the same
shape.

Deliberately has no per-type recording of its own -- see
_start_new_recording/_start_next_recording_segment/_stop_recording below.
It's still a completely normal session participant otherwise: included in
target "all" like anything else, PTP-gated at session start
(Recording._check_ptp_sync), watched for mid-session PTP degradation
(Recording._check_ptp_mid_recording on the controller), and -- this is the
part that actually matters for a timestamp comparison -- every session it's
part of still gets Recording._record_health_metadata's per-second
ptp4l_offset_ns/phc2sys_offset_ns CSV, generic to every module type,
exported alongside the real data. That CSV is the durable per-session
record of "was this Pi's clock trustworthy for the whole recording" the
comparison actually leans on; nothing module-specific needed to produce it.

Author: Andrew SG
Created: 14/09/2026
"""

import os
import sys
import time

# Saviour Imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from modules.module import Module


class ExternalHostModule(Module):
    def __init__(self, module_type="external_host"):
        super().__init__(module_type)

        self.config.load_module_config("external_host_config.json")

        self.description = (
            "PTP-synced fleet member with no recording data of its own "
            "(e.g. a pyControl host) -- see external_host_module.py docstring"
        )


    """Config"""
    def configure_module_special(self, updated_keys: list[str] | None):
        # No module-specific config of its own to react to.
        pass


    """Recording -- intentionally no-ops.

    This module has no data of its own to capture. Returning True (not
    False, which Recording._create_initial_recording_segment treats as "the
    module could not start") lets the base Recording class's generic
    machinery run exactly as it would for any other module: the per-second
    health/PTP-offset CSV, the session-start PTP gate, the mid-session
    degradation warning, and the session journal snapshot on stop -- all of
    it keyed only on session participation, none of it dependent on these
    hooks doing anything.
    """
    def _start_new_recording(self) -> bool:
        return True


    def _start_next_recording_segment(self) -> bool:
        return True


    def _stop_recording(self) -> bool:
        return True


def main():
    external_host = ExternalHostModule()
    external_host.start()

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nShutting down...")
        external_host.stop()

if __name__ == "__main__":
    main()
