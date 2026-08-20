import os
import sys
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from src.controller.facade import ControllerFacade
from src.controller.models import Module
from src.controller.modules import ModuleConfigState, Modules

MockController = {}


def test_facade():
    facade = ControllerFacade(MockController)
    assert facade


# ---------------------------------------------------------------------------
# enqueue_export
# ---------------------------------------------------------------------------

class TestEnqueueExport:
    def test_pending_only_counted_when_queue_actually_accepts(self):
        """export_queue.enqueue() drops duplicate export_ready signals for a
        module already queued/active. Counting pending on every signal
        regardless of that meant pending_exports drifted upward forever for
        the dropped ones -- no complete/failed ever arrives to bring it back
        down. A module producing several export_ready signals close
        together (e.g. habitat_camera closing multiple clips) hit this on
        real hardware."""
        controller = MagicMock()
        facade = ControllerFacade(controller)
        controller.export_queue.enqueue.return_value = True

        facade.enqueue_export("mod_a", "session/2026/mod_a")

        controller.recording.module_export_update.assert_called_once_with(
            "mod_a", "session/2026/mod_a", "pending"
        )

    def test_pending_not_counted_when_queue_drops_as_duplicate(self):
        controller = MagicMock()
        facade = ControllerFacade(controller)
        controller.export_queue.enqueue.return_value = False

        facade.enqueue_export("mod_a", "session/2026/mod_a")

        controller.recording.module_export_update.assert_not_called()


# ---------------------------------------------------------------------------
# reconcile_framesync
# ---------------------------------------------------------------------------

def _make_facade_with_modules():
    modules = Modules()
    modules.facade = None
    controller = MagicMock()
    controller.modules = modules
    facade = ControllerFacade(controller)
    return facade, modules, controller


def _add_camera(
    modules, module_id, *,
    online=True, framesync_enabled=False, sync_mode="none",
    fps=30, sensor_mode_index=0,
):
    modules.add_module(Module(
        id=module_id, name=module_id, type="camera", version="1.0",
        ip="10.0.0.2", online=online,
    ))
    modules._config_states[module_id] = ModuleConfigState(true_config={
        "camera": {
            "framesync_enabled": framesync_enabled,
            "sync_mode": sync_mode,
            "fps": fps,
            "sensor_mode_index": sensor_mode_index,
        }
    })


class TestReconcileFramesync:
    def test_no_enabled_cameras_is_a_no_op(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a")
        _add_camera(modules, "camera_b")

        roles = facade.reconcile_framesync()

        assert roles == {"camera_a": "none", "camera_b": "none"}
        controller.communication.send_command.assert_not_called()

    def test_lowest_module_id_elected_transmitter(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_b", framesync_enabled=True, fps=30, sensor_mode_index=2)
        _add_camera(modules, "camera_a", framesync_enabled=True, fps=30, sensor_mode_index=2)
        _add_camera(modules, "camera_c", framesync_enabled=True, fps=30, sensor_mode_index=2)

        roles = facade.reconcile_framesync()

        assert roles == {"camera_a": "server", "camera_b": "client", "camera_c": "client"}
        assert controller.communication.send_command.call_count == 3
        pushed = {
            call.args[0]: call.args[2]
            for call in controller.communication.send_command.call_args_list
        }
        assert pushed["camera_a"]["camera"]["sync_mode"] == "server"
        assert pushed["camera_b"]["camera"]["sync_mode"] == "client"
        assert pushed["camera_c"]["camera"]["sync_mode"] == "client"

    def test_clients_pinned_to_transmitter_fps_and_sensor_mode(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, fps=60, sensor_mode_index=3)
        _add_camera(modules, "camera_b", framesync_enabled=True, fps=25, sensor_mode_index=0)

        facade.reconcile_framesync()

        pushed = {
            call.args[0]: call.args[2]
            for call in controller.communication.send_command.call_args_list
        }
        assert pushed["camera_b"]["camera"]["fps"] == 60
        assert pushed["camera_b"]["camera"]["sensor_mode_index"] == 3

    def test_disabled_camera_with_stale_server_role_is_corrected_to_none(self):
        """A camera that was previously the transmitter but has since been
        unticked must be pushed back to sync_mode='none', not left as 'server'."""
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=False, sync_mode="server")

        roles = facade.reconcile_framesync()

        assert roles == {"camera_a": "none"}
        controller.communication.send_command.assert_called_once()
        pushed_config = controller.communication.send_command.call_args.args[2]
        assert pushed_config["camera"]["sync_mode"] == "none"

    def test_offline_enabled_camera_is_skipped_not_pushed_to(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", online=False, framesync_enabled=True)

        roles = facade.reconcile_framesync()

        assert "camera_a" not in roles
        controller.communication.send_command.assert_not_called()

    def test_reconnecting_module_with_stale_self_reported_server_is_demoted(self):
        """The core dual-transmitter-avoidance property: a camera that still
        locally believes it's the transmitter (stale sync_mode='server' from
        before it dropped) must never be trusted as a vote — a legitimately
        lower-ID candidate is elected instead, and the stale one gets
        corrected to 'client', not left as a second 'server'."""
        facade, modules, controller = _make_facade_with_modules()
        # camera_a is back online and *should* be elected (lowest ID), but
        # hasn't been told yet -- its own config still says "none".
        _add_camera(modules, "camera_a", online=True, framesync_enabled=True, sync_mode="none")
        # camera_z never went through reconciliation and still self-reports
        # as the transmitter from a previous election.
        _add_camera(modules, "camera_z", online=True, framesync_enabled=True, sync_mode="server")

        roles = facade.reconcile_framesync()

        assert roles["camera_a"] == "server"
        assert roles["camera_z"] == "client"
        pushed = {
            call.args[0]: call.args[2]
            for call in controller.communication.send_command.call_args_list
        }
        assert pushed["camera_z"]["camera"]["sync_mode"] == "client"
        assert "camera_a" not in pushed or pushed["camera_a"]["camera"]["sync_mode"] == "server"
        # Never both "server" at once.
        pushed_roles = [cfg["camera"]["sync_mode"] for cfg in pushed.values()]
        assert pushed_roles.count("server") <= 1

    def test_pushed_config_strips_private_keys(self):
        """A module's true_config carries its internal/private (_-prefixed)
        keys verbatim (e.g. _codec, _heartbeat_interval). Every other caller
        of set_target_module_config sends only the filtered public view, so
        reconcile pushing an unfiltered target_config would make the
        module's next echo look like a mismatch on every single field the
        module itself didn't report back (e.g. a whole section that's
        entirely private keys, like 'communication', disappears from the
        echo and gets misread as FAILED sync) -- a real bug caught on
        hardware, not hypothetical."""
        facade, modules, controller = _make_facade_with_modules()
        modules.add_module(Module(
            id="camera_a", name="camera_a", type="camera", version="1.0",
            ip="10.0.0.2", online=True,
        ))
        modules._config_states["camera_a"] = ModuleConfigState(true_config={
            "communication": {"_command_socket_port": 5555, "_status_socket_port": 5556},
            "camera": {
                "framesync_enabled": True,
                "sync_mode": "none",
                "fps": 30,
                "sensor_mode_index": 0,
                "_codec": "h264",
            },
        })

        facade.reconcile_framesync()

        pushed_config = controller.communication.send_command.call_args.args[2]
        assert "communication" not in pushed_config
        assert "_codec" not in pushed_config["camera"]
        assert pushed_config["camera"]["sync_mode"] == "server"
        assert pushed_config["camera"]["framesync_enabled"] is True

    def test_idempotent_once_confirmed(self):
        """Once every camera's true_config already matches its target role
        (simulating the module having echoed the applied config back), a
        second call must not push anything further."""
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="none")
        _add_camera(modules, "camera_b", framesync_enabled=True, sync_mode="none")
        facade.reconcile_framesync()
        # Simulate the modules confirming the pushed config.
        modules._config_states["camera_a"].true_config["camera"]["sync_mode"] = "server"
        modules._config_states["camera_b"].true_config["camera"]["sync_mode"] = "client"
        controller.communication.send_command.reset_mock()

        facade.reconcile_framesync()

        controller.communication.send_command.assert_not_called()

    def test_non_camera_modules_ignored(self):
        facade, modules, controller = _make_facade_with_modules()
        modules.add_module(Module(id="ttl_1", name="ttl_1", type="ttl", version="1.0", ip="10.0.0.3"))
        modules._config_states["ttl_1"] = ModuleConfigState(true_config={})

        roles = facade.reconcile_framesync()

        assert roles == {}
        controller.communication.send_command.assert_not_called()
