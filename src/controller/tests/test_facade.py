import os
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from src.controller.facade import ControllerFacade
from src.controller.models import Module
from src.controller.modules import ConfigSyncStatus, ModuleConfigState, Modules

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


def _pushed(controller) -> dict:
    """{module_id: pushed sync_mode} from every set_config sent."""
    return {
        call.args[0]: call.args[2]["camera"]["sync_mode"]
        for call in controller.communication.send_command.call_args_list
    }


def _confirm(modules, module_id, **camera):
    """Simulate the module echoing an applied camera config."""
    state = modules._config_states[module_id]
    state.true_config["camera"].update(camera)
    state.status = ConfigSyncStatus.SYNCED


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
        # Server first; the clients wait for its echo.
        assert _pushed(controller) == {"camera_a": "server"}

        _confirm(modules, "camera_a", sync_mode="server")
        controller.communication.send_command.reset_mock()
        facade.reconcile_framesync()

        assert _pushed(controller) == {"camera_b": "client", "camera_c": "client"}

    def test_clients_pinned_to_transmitter_fps_and_sensor_mode(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="server",
                    fps=60, sensor_mode_index=3)
        _add_camera(modules, "camera_b", framesync_enabled=True, fps=25, sensor_mode_index=0)

        facade.reconcile_framesync()

        pushed = controller.communication.send_command.call_args.args
        assert pushed[0] == "camera_b"
        assert pushed[2]["camera"]["fps"] == 60
        assert pushed[2]["camera"]["sensor_mode_index"] == 3

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
        """Never two servers: when two cameras both report 'server' (one
        stale from before it dropped) and there's no remembered transmitter,
        the lowest keeps the role and the other is corrected to 'client'."""
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", online=True, framesync_enabled=True, sync_mode="server")
        _add_camera(modules, "camera_z", online=True, framesync_enabled=True, sync_mode="server")

        roles = facade.reconcile_framesync()

        assert roles == {"camera_a": "server", "camera_z": "client"}
        assert _pushed(controller) == {"camera_z": "client"}

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


class TestReconcileSkipsRecordingCameras:
    """Test D (2026-10-04): a role change restarts the camera; doing that
    mid-recording orphaned the hailo camera's encoder."""

    def test_recording_camera_is_not_reroled(self):
        from src.controller.modules import ModuleStatus
        facade, modules, controller = _make_facade_with_modules()
        facade._started_mono -= facade._FRAMESYNC_COLD_START_S + 1  # mid-run
        _add_camera(modules, "camera_b", framesync_enabled=True, sync_mode="client")
        modules._modules["camera_b"].status = ModuleStatus.RECORDING
        # camera_a (would-be server) is offline -> camera_b should become server
        _add_camera(modules, "camera_a", framesync_enabled=True, online=False,
                    sync_mode="server")
        roles = facade.reconcile_framesync()
        assert roles["camera_b"] == "server"
        controller.communication.send_command.assert_not_called()

    def test_idle_camera_is_reroled(self):
        facade, modules, controller = _make_facade_with_modules()
        facade._started_mono -= facade._FRAMESYNC_COLD_START_S + 1  # mid-run
        _add_camera(modules, "camera_b", framesync_enabled=True, sync_mode="client")
        _add_camera(modules, "camera_a", framesync_enabled=True, online=False,
                    sync_mode="server")
        facade.reconcile_framesync()
        controller.communication.send_command.assert_called_once()


class TestFramesyncStickyElection:
    """2026-10-23: a re-provisioned camera's new ID sorted first, the role
    moved, both cameras restarted at once and the client wedged."""

    def test_new_lower_id_camera_does_not_take_the_role(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_0fcc", framesync_enabled=True, sync_mode="server")
        facade.reconcile_framesync()
        _add_camera(modules, "apa_camera_3606", framesync_enabled=True, sync_mode="none")

        roles = facade.reconcile_framesync()

        assert roles == {"camera_0fcc": "server", "apa_camera_3606": "client"}
        assert _pushed(controller) == {"apa_camera_3606": "client"}

    def test_remembered_transmitter_kept_over_lowest_id(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_b", framesync_enabled=True)
        facade.reconcile_framesync()           # camera_b elected (only one)
        _add_camera(modules, "camera_a", framesync_enabled=True)

        roles = facade.reconcile_framesync()

        assert roles["camera_b"] == "server"
        assert roles["camera_a"] == "client"

    def test_transmitter_offline_reelects(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="server")
        _add_camera(modules, "camera_b", framesync_enabled=True, sync_mode="client")
        _add_camera(modules, "camera_c", framesync_enabled=True, sync_mode="client")
        facade.reconcile_framesync()
        modules._modules["camera_a"].online = False

        roles = facade.reconcile_framesync()

        assert roles == {"camera_b": "server", "camera_c": "client"}
        assert _pushed(controller) == {"camera_b": "server"}

    def test_transmitter_disabled_reelects(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="server")
        _add_camera(modules, "camera_b", framesync_enabled=True, sync_mode="client")
        facade.reconcile_framesync()
        modules._config_states["camera_a"].true_config["camera"]["framesync_enabled"] = False

        roles = facade.reconcile_framesync()

        assert roles == {"camera_a": "none", "camera_b": "server"}

    def test_cold_start_keeps_single_claim_over_lowest_id(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="client")
        _add_camera(modules, "camera_z", framesync_enabled=True, sync_mode="server")

        roles = facade.reconcile_framesync()

        assert roles == {"camera_a": "client", "camera_z": "server"}
        controller.communication.send_command.assert_not_called()

    def test_cold_start_waits_for_every_online_camera_config(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="client")
        modules.add_module(Module(id="camera_z", name="camera_z", type="camera",
                                  version="1.0", ip="10.0.0.3", online=True))

        assert facade.reconcile_framesync() == {}
        controller.communication.send_command.assert_not_called()

    def test_after_cold_start_window_a_silent_camera_no_longer_blocks(self):
        facade, modules, controller = _make_facade_with_modules()
        facade._started_mono -= facade._FRAMESYNC_COLD_START_S + 1
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="none")
        modules.add_module(Module(id="camera_z", name="camera_z", type="camera",
                                  version="1.0", ip="10.0.0.3", online=True))

        assert facade.reconcile_framesync() == {"camera_a": "server"}


    def test_cold_start_holds_election_while_server_missing(self):
        """Fleet update, APA rig 2026-10-23: the old client re-reported before
        the real server re-registered and was elected over it."""
        facade, modules, controller = _make_facade_with_modules()
        facade._schedule_framesync_retry = MagicMock()
        _add_camera(modules, "camera_z", framesync_enabled=True, sync_mode="client")

        assert facade.reconcile_framesync() == {}
        controller.communication.send_command.assert_not_called()
        facade._schedule_framesync_retry.assert_called_once()

        _add_camera(modules, "apa_camera_a", framesync_enabled=True, sync_mode="server")
        roles = facade.reconcile_framesync()

        assert roles == {"apa_camera_a": "server", "camera_z": "client"}
        controller.communication.send_command.assert_not_called()

    def test_after_cold_start_window_lone_client_is_elected(self):
        facade, modules, controller = _make_facade_with_modules()
        facade._started_mono -= facade._FRAMESYNC_COLD_START_S + 1
        _add_camera(modules, "camera_z", framesync_enabled=True, sync_mode="client")

        assert facade.reconcile_framesync() == {"camera_z": "server"}

    def test_cold_start_retry_reruns_reconcile(self):
        facade, _, _ = _make_facade_with_modules()
        facade.reconcile_framesync = MagicMock()
        with patch("src.controller.facade.threading.Timer") as timer:
            facade._schedule_framesync_retry(30.0)
            facade._schedule_framesync_retry(30.0)  # one timer at a time
        timer.assert_called_once()
        timer.call_args.args[1]()
        facade.reconcile_framesync.assert_called_once()
        assert facade._framesync_retry is None


class TestFramesyncOrderedRestarts:
    def test_clients_wait_while_server_pending(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="server")
        _add_camera(modules, "camera_b", framesync_enabled=True, sync_mode="none")
        modules._config_states["camera_a"].status = ConfigSyncStatus.PENDING

        facade.reconcile_framesync()

        controller.communication.send_command.assert_not_called()

    def test_failed_server_still_releases_clients(self):
        """FAILED can be an unrelated diff (export.share_password on the
        APA rig): what matters is the echoed sync_mode."""
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="server")
        _add_camera(modules, "camera_b", framesync_enabled=True, sync_mode="none")
        modules._config_states["camera_a"].status = ConfigSyncStatus.FAILED

        facade.reconcile_framesync()

        assert _pushed(controller) == {"camera_b": "client"}

    def test_server_fps_change_repins_client_after_echo(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="server", fps=30)
        _add_camera(modules, "camera_b", framesync_enabled=True, sync_mode="client", fps=30)
        facade.reconcile_framesync()
        controller.communication.send_command.assert_not_called()

        _confirm(modules, "camera_a", fps=60, sensor_mode_index=2)
        facade.reconcile_framesync()

        args = controller.communication.send_command.call_args.args
        assert args[0] == "camera_b"
        assert args[2]["camera"]["fps"] == 60
        assert args[2]["camera"]["sensor_mode_index"] == 2
        assert args[2]["camera"]["sync_mode"] == "client"

    def test_matching_client_not_pushed(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="server",
                    fps=30, sensor_mode_index=1)
        _add_camera(modules, "camera_b", framesync_enabled=True, sync_mode="client",
                    fps=30, sensor_mode_index=1)

        facade.reconcile_framesync()

        controller.communication.send_command.assert_not_called()

    def test_pending_push_not_resent(self):
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="none")
        facade.reconcile_framesync()
        facade.reconcile_framesync()   # another echo arrives before camera_a's

        assert controller.communication.send_command.call_count == 1

    def test_recording_client_deferred(self):
        from src.controller.modules import ModuleStatus
        facade, modules, controller = _make_facade_with_modules()
        _add_camera(modules, "camera_a", framesync_enabled=True, sync_mode="server", fps=60)
        _add_camera(modules, "camera_b", framesync_enabled=True, sync_mode="client", fps=30)
        modules._modules["camera_b"].status = ModuleStatus.RECORDING

        facade.reconcile_framesync()

        controller.communication.send_command.assert_not_called()
