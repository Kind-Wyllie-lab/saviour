"""
Tests for src/modules/variants/external_host/external_host_module.py.

ExternalHostModule.__init__ builds a full Module stack (real Config,
Network, ZMQ, PTP...), so this constructs via __new__ (same pattern as
test_module_readiness.py's _DummyModule) rather than exercising __init__.
The module has no per-type behaviour of its own to speak of -- these tests
exist mainly to pin down the one thing that matters: the recording hooks
return True (not False, not None-that-happens-to-work), which is what lets
Recording._create_initial_recording_segment treat it as a normal, if
data-less, session participant rather than a failed start.
"""

from src.modules.variants.external_host.external_host_module import ExternalHostModule


def _make() -> ExternalHostModule:
    return ExternalHostModule.__new__(ExternalHostModule)


class TestNoAbstractMethodsLeft:
    def test_class_is_concrete(self):
        """Module is an ABC with 4 abstract hooks -- a class that hasn't
        implemented all of them can't even be __new__'d."""
        assert ExternalHostModule.__abstractmethods__ == frozenset()


class TestRecordingHooksAreNoOps:
    """Recording._create_initial_recording_segment only treats a literal
    False as "could not start" -- these must return True, not just
    something truthy-by-accident, so a future edit can't silently flip this
    module into blocking every session it's part of."""

    def test_start_new_recording_returns_true(self):
        assert _make()._start_new_recording() is True

    def test_start_next_recording_segment_returns_true(self):
        assert _make()._start_next_recording_segment() is True

    def test_stop_recording_returns_true(self):
        assert _make()._stop_recording() is True


class TestConfigureModuleSpecial:
    def test_is_a_no_op(self):
        # Must not raise regardless of what's passed -- called by the base
        # Module whenever module-specific config changes, including None.
        assert _make().configure_module_special(None) is None
        assert _make().configure_module_special(["external_host.x"]) is None
