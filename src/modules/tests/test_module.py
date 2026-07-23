"""
Tests for src/modules/module.py

Covers auto-registration of @command()/@check()-decorated methods, which
replaces the manual per-subclass command/check dict boilerplate.
"""

import pytest

from src.modules.module import Module, command, check


class _DummyModule(Module):
    """Minimal concrete Module — never __init__'d, only used to exercise
    _auto_register_decorated_methods() directly on a bare instance."""

    @command()
    def do_thing(self):
        return {"result": "success"}

    @command(name="custom_name")
    def _renamed_thing(self):
        return {"result": "success"}

    @check()
    def _check_widget(self):
        return True, "widget ok"

    def _start_new_recording(self):
        return True

    def _start_next_recording_segment(self):
        return True

    def _stop_recording(self):
        return True

    def configure_module_special(self, updated_keys):
        pass


class _DerivedDummyModule(_DummyModule):
    """Overrides do_thing — used to confirm the most-derived override wins."""

    @command()
    def do_thing(self):
        return {"result": "overridden"}


def _bare_instance(cls):
    """Construct without running Module.__init__ (which builds a full
    config/network/zmq stack). Auto-registration only needs `type(self)` for
    the class-hierarchy scan and bound-method lookup via getattr(self, name),
    neither of which requires any instance state to already exist."""
    return object.__new__(cls)


def test_auto_registers_decorated_command():
    instance = _bare_instance(_DummyModule)
    commands, _ = instance._auto_register_decorated_methods()
    assert "do_thing" in commands
    assert commands["do_thing"]() == {"result": "success"}


def test_command_name_override_used_as_dict_key():
    instance = _bare_instance(_DummyModule)
    commands, _ = instance._auto_register_decorated_methods()
    assert "custom_name" in commands
    assert "_renamed_thing" not in commands


def test_auto_registers_decorated_check_excluding_base_checks():
    instance = _bare_instance(_DummyModule)
    _, module_checks = instance._auto_register_decorated_methods()
    names = {c.__name__ for c in module_checks}
    assert "_check_widget" in names
    assert not names & Module._BASE_CHECK_NAMES


def test_most_derived_override_wins_on_name_collision():
    instance = _bare_instance(_DerivedDummyModule)
    commands, _ = instance._auto_register_decorated_methods()
    assert commands["do_thing"]() == {"result": "overridden"}


def test_manual_and_auto_registered_commands_merge():
    from src.modules.command import Command

    cmd = Command()
    instance = _bare_instance(_DummyModule)
    auto_commands, _ = instance._auto_register_decorated_methods()

    cmd.set_commands({"manual_one": lambda: {"result": "success"}})
    cmd.set_commands(auto_commands)

    assert "manual_one" in cmd.commands
    assert "do_thing" in cmd.commands
