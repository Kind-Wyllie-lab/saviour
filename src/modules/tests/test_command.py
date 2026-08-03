"""
Tests for src/modules/command.py.

Command.__init__ is side-effect-free, so it's constructed directly with
facade assigned afterward. Covers the legacy string command protocol's
parser (JSON-embedded and key=value forms) and handle_command's dispatch,
result-shape normalisation, and error paths.
"""

from unittest.mock import MagicMock

from src.modules.command import Command


def _make_command() -> Command:
    cmd = Command()
    cmd.facade = MagicMock()
    return cmd


# ---------------------------------------------------------------------------
# _parse_command
# ---------------------------------------------------------------------------

class TestParseCommand:
    def test_key_value_params(self):
        cmd = _make_command()
        name, params = cmd._parse_command(
            "start_streaming client_ip=192.168.0.55 port=8080"
        )
        assert name == "start_streaming"
        assert params == {"client_ip": "192.168.0.55", "port": "8080"}

    def test_no_params(self):
        cmd = _make_command()
        name, params = cmd._parse_command("get_health")
        assert name == "get_health"
        assert params == {}

    def test_embedded_json_params(self):
        cmd = _make_command()
        name, params = cmd._parse_command('start_recording {"duration": 60}')
        assert name == "start_recording"
        assert params == {"duration": 60}

    def test_malformed_json_is_caught(self):
        cmd = _make_command()
        name, params = cmd._parse_command('start_recording {not valid json}')
        assert name == ""
        assert params == {}


# ---------------------------------------------------------------------------
# set_commands / set_callbacks alias
# ---------------------------------------------------------------------------

class TestSetCommands:
    def test_updates_command_registry(self):
        cmd = _make_command()
        handler = MagicMock()
        cmd.set_commands({"ping": handler})
        assert cmd.commands["ping"] is handler

    def test_set_callbacks_is_the_same_method(self):
        assert Command.set_callbacks is Command.set_commands


# ---------------------------------------------------------------------------
# handle_command
# ---------------------------------------------------------------------------

class TestHandleCommand:
    def test_unknown_command_sends_error_status(self):
        cmd = _make_command()
        cmd.handle_command("ghost_command")
        sent = cmd.facade.send_status.call_args[0][0]
        assert sent["type"] == "error"
        assert "ghost_command" in sent["error"]

    def test_dispatches_with_no_params(self):
        cmd = _make_command()
        handler = MagicMock(return_value={"result": "success", "data": 1})
        cmd.set_commands({"get_health": handler})
        cmd.handle_command("get_health")
        handler.assert_called_once_with()

    def test_dispatches_with_unpacked_params(self):
        cmd = _make_command()
        handler = MagicMock(return_value=True)
        cmd.set_commands({"start_streaming": handler})
        cmd.handle_command("start_streaming client_ip=10.0.0.5 port=8080")
        handler.assert_called_once_with(client_ip="10.0.0.5", port="8080")

    def test_true_result_becomes_success_dict(self):
        cmd = _make_command()
        cmd.set_commands({"stop_recording": MagicMock(return_value=True)})
        cmd.handle_command("stop_recording")
        sent = cmd.facade.send_status.call_args[0][0]
        assert sent["result"] == "success"
        assert sent["type"] == "cmd_ack"
        assert sent["command"] == "stop_recording"

    def test_false_result_becomes_error_dict(self):
        cmd = _make_command()
        cmd.set_commands({"stop_recording": MagicMock(return_value=False)})
        cmd.handle_command("stop_recording")
        sent = cmd.facade.send_status.call_args[0][0]
        assert sent["result"] == "error"

    def test_none_result_becomes_error_dict_with_warning(self):
        cmd = _make_command()
        cmd.set_commands({"broken_handler": MagicMock(return_value=None)})
        cmd.handle_command("broken_handler")
        sent = cmd.facade.send_status.call_args[0][0]
        assert sent["result"].startswith("error:")

    def test_dict_result_passes_through_and_gets_cmd_ack_fields(self):
        cmd = _make_command()
        cmd.set_commands({
            "get_sensor_modes": MagicMock(return_value={"sensor_modes": []})
        })
        cmd.handle_command("get_sensor_modes")
        sent = cmd.facade.send_status.call_args[0][0]
        assert sent == {
            "type": "cmd_ack", "command": "get_sensor_modes", "sensor_modes": []
        }

    def test_handler_exception_reports_error_with_timestamp(self):
        cmd = _make_command()
        cmd.set_commands({
            "boom": MagicMock(side_effect=RuntimeError("camera wedged"))
        })
        cmd.handle_command("boom")
        sent = cmd.facade.send_status.call_args[0][0]
        assert sent["type"] == "error"
        assert "camera wedged" in sent["error"]
        assert "timestamp" in sent
