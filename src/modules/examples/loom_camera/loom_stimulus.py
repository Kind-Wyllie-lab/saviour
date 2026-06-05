import os
from dataclasses import dataclass
from typing import Literal, Optional
import multiprocessing as mp
import ctypes
import time
import queue
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import glfw
import OpenGL.GL
import OpenGL.GL.shaders
from OpenGL.GL import glGenBuffers, glGenVertexArrays

from OpenGL.raw.GL.VERSION.GL_1_0 import (
    GL_BLEND,
    GL_COLOR_BUFFER_BIT,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_SCISSOR_TEST,
    GL_SRC_ALPHA,
    GL_TEXTURE_2D,
    GL_TRIANGLES,
    glBlendFunc,
    glClear,
    glClearColor,
    glDisable,
    glEnable,
    glScissor,
    glViewport,
)
from OpenGL.raw.GL.VERSION.GL_1_1 import glDrawElements
from OpenGL.raw.GL.VERSION.GL_1_3 import GL_TEXTURE0, glActiveTexture
from OpenGL.raw.GL.VERSION.GL_1_5 import (
    GL_ARRAY_BUFFER,
    GL_ELEMENT_ARRAY_BUFFER,
    GL_STATIC_DRAW,
    glBindBuffer,
    glBufferData,
    glDeleteBuffers,
)
from OpenGL.raw.GL.VERSION.GL_2_0 import (
    GL_FRAGMENT_SHADER,
    GL_VERTEX_SHADER,
    glDeleteProgram,
    glEnableVertexAttribArray,
    glGetUniformLocation,
    glUniform1i,
    glUniformMatrix4fv,
    glUseProgram,
    glVertexAttribPointer,
)
from OpenGL.raw.GL.VERSION.GL_3_0 import glBindVertexArray, glDeleteVertexArrays
from OpenGL.raw.GL._types import GL_FALSE, GL_FLOAT, GL_UNSIGNED_INT

from modules.examples.loom_camera.utils import framebuffer_size_callback, load_texture, vcalc, translate_matrix, \
    scale_matrix


LoomStimulusCommand = Literal["start", "stop", "shutdown", "ping"]


@dataclass(frozen=True)
class LoomStimulusConfig:
    """
    Configuration for the looming stimulus renderer.

    Parameters
    ----------
    texture_path : str
        Path to the stimulus texture PNG.
    initial_size_cm : float
        Initial stimulus size (cm).
    final_size_cm : float
        Final stimulus size (cm).
    initial_pos_ndc : tuple of float
        Start position in NDC.
    final_pos_ndc : tuple of float
        End position in NDC.
    travel_time_s : float
        Outward travel time.
    loom_wait_time_s : float
        Return time / inter-loom timing in your original code.
    round_size : int
        Looms per round before checking stop_after_round.
    image_angle_deg : float
        Texture rotation.
    background_rgba : tuple of float
        Clear color.
    """
    texture_path: str
    initial_size_cm: float
    final_size_cm: float
    initial_pos_ndc: tuple[float, float]
    final_pos_ndc: tuple[float, float]
    travel_time_s: float
    loom_wait_time_s: float
    round_size: int
    image_angle_deg: float
    background_rgba: tuple[float, float, float, float]


@dataclass
class LoomBatchRunState:
    round_size: int = 5
    active: bool = False
    stop_after_current_round: bool = False
    round_trip_counter_in_round: int = 0

    def on_enter(self) -> None:
        """
        Enter means: keep running (or start running), and DO NOT stop after round.
        Does not reset current round progress.
        """
        self.active = True
        self.stop_after_current_round = False

    def on_leave(self) -> None:
        """Leave means: finish current round then stop."""
        if self.active:
            self.stop_after_current_round = True

    def reset_round(self) -> None:
        self.round_trip_counter_in_round = 0

    def on_completed_round_trip(self) -> None:
        self.round_trip_counter_in_round += 1

    def current_round_completed(self) -> bool:
        return self.round_trip_counter_in_round >= self.round_size

    def should_start_next_round(self) -> bool:
        return self.active and (not self.stop_after_current_round)

    def stop_now(self) -> None:
        self.active = False
        self.stop_after_current_round = False
        self.round_trip_counter_in_round = 0


def run_loom_stimulus_with_ipc(
    *,
    command_queue,
    status_queue,
    texture_path: str,
    initial_size_cm: float,
    final_size_cm: float,
    initial_pos_ndc: Tuple[float, float],
    final_pos_ndc: Tuple[float, float],
    travel_time_s: float,
    loom_wait_time_s: float,
    repetitions_per_round: int,
    image_angle_deg: float,
    background_rgba: Tuple[float, float, float, float],
    photodiode_box_px: int = 80,
    photodiode_y_ndc: float = 0.0,
    monitor_index: Optional[int] = None,
    fullscreen: bool = True,
    window_size_px: Tuple[int, int] = (1920, 1080),
    vsync: bool = True,
) -> None:
    """
    Run the looming stimulus controlled by IPC commands instead of GPIO TTL.

    Parameters
    ----------
    command_queue : multiprocessing.Queue-like
        Receives tuples (cmd, payload) where cmd in {'start','stop','shutdown','ping'}.
    status_queue : multiprocessing.Queue-like
        Emits dict status messages (best-effort).
    texture_path : str
        Texture image path.
    initial_size_cm, final_size_cm : float
        Stimulus size range.
    initial_pos_ndc, final_pos_ndc : tuple of float
        Stimulus start/end in NDC.
    travel_time_s : float
        Outward travel time.
    loom_wait_time_s : float
        Return leg duration.
    repetitions_per_round : int
        Number of round-trips per round (your old round_size=5).
    image_angle_deg : float
        Texture rotation angle.
    background_rgba : tuple
        Background clear color.
    photodiode_box_px : int
        Photodiode marker square size in pixels.
    photodiode_y_ndc : float
        Y position for photodiode marker in NDC.
    monitor_index : int or None
        Which monitor to use for fullscreen (None -> primary).
    fullscreen : bool
        Whether to open fullscreen on a monitor.
    window_size_px : tuple
        Windowed mode size.
    vsync : bool
        Enable vsync.

    Notes
    -----
    - 'start' sets "run requested" (enter).
    - 'stop' sets "stop after current round" (leave).
    - Does not abort mid-round.
    """
    def _status(msg: dict) -> None:
        try:
            if status_queue is not None:
                status_queue.put_nowait(msg)
        except Exception:
            pass

    if not glfw.init():
        raise RuntimeError("Failed to initialize GLFW")

    window = None
    shader_program = None
    vao = None
    vbo = None
    ebo = None
    texture = None

    try:
        # -----------------------------
        # Window / monitor selection
        # -----------------------------
        glfw.default_window_hints()

        # Request OpenGL ES 3.0 context (Raspberry Pi friendly)
        glfw.window_hint(glfw.CLIENT_API, glfw.OPENGL_ES_API)
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 0)
        # Borderless window (no decorations)
        glfw.window_hint(glfw.DECORATED, glfw.FALSE)

        # Keep window focused on creation (optional)
        glfw.window_hint(glfw.FOCUSED, glfw.TRUE)
        mon = None
        monitors = glfw.get_monitors() or []

        if fullscreen:
            if not monitors:
                raise RuntimeError("No monitors detected by GLFW.")
            if monitor_index is None:
                mon = glfw.get_primary_monitor()
            else:
                mon = monitors[int(np.clip(monitor_index, 0, len(monitors) - 1))]

            mode = glfw.get_video_mode(mon)
            w, h = mode.size.width, mode.size.height
            window = glfw.create_window(w, h, "Loom Stimulus", mon, None)
        else:
            w, h = int(window_size_px[0]), int(window_size_px[1])
            window = glfw.create_window(w, h, "Loom Stimulus", None, None)

        if window is None:
            raise RuntimeError("Failed to create GLFW window (window=None). Check GLX/Mesa driver stack.")

        glfw.make_context_current(window)
        glfw.set_framebuffer_size_callback(window, framebuffer_size_callback)
        glfw.swap_interval(1 if vsync else 0)

        glViewport(0, 0, w, h)
        current_window_width = w
        current_window_height = h

        # -----------------------------
        # Shaders + geometry + texture
        # -----------------------------

        VERTEX_SHADER_GLES300 = r"""#version 300 es
        precision highp float;

        layout(location = 0) in vec3 position;
        layout(location = 1) in vec2 texCoords;

        uniform mat4 transform;

        out vec2 vTexCoords;

        void main()
        {
            gl_Position = transform * vec4(position, 1.0);
            vTexCoords = texCoords;
        }
        """

        FRAGMENT_SHADER_GLES300 = r"""#version 300 es
        precision highp float;

        in vec2 vTexCoords;
        uniform sampler2D texture1;

        out vec4 FragColor;

        void main()
        {
            FragColor = texture(texture1, vTexCoords);
        }
        """

        shader_program = OpenGL.GL.shaders.compileProgram(
            OpenGL.GL.shaders.compileShader(VERTEX_SHADER_GLES300, GL_VERTEX_SHADER),
            OpenGL.GL.shaders.compileShader(FRAGMENT_SHADER_GLES300, GL_FRAGMENT_SHADER),
        )

        vertices = np.array(
            [
                1.0,  1.0, 0.0, 1.0, 1.0,
                1.0, -1.0, 0.0, 1.0, 0.0,
               -1.0, -1.0, 0.0, 0.0, 0.0,
               -1.0,  1.0, 0.0, 0.0, 1.0,
            ],
            dtype=np.float32,
        )
        indices = np.array([0, 1, 3, 1, 2, 3], dtype=np.uint32)

        vao = glGenVertexArrays(1)
        glBindVertexArray(vao)

        vbo = glGenBuffers(1)
        glBindBuffer(GL_ARRAY_BUFFER, vbo)
        glBufferData(GL_ARRAY_BUFFER, vertices.nbytes, vertices, GL_STATIC_DRAW)

        ebo = glGenBuffers(1)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, ebo)
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, indices.nbytes, indices, GL_STATIC_DRAW)

        glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 5 * vertices.itemsize, ctypes.c_void_p(0))
        glEnableVertexAttribArray(0)

        glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 5 * vertices.itemsize, ctypes.c_void_p(12))
        glEnableVertexAttribArray(1)

        texture = load_texture(texture_path, image_angle_deg)

        glUseProgram(shader_program)
        glUniform1i(glGetUniformLocation(shader_program, "texture1"), 0)

        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        # -----------------------------
        # Motion params (your original)
        # -----------------------------
        outward_vx = vcalc(initial_pos_ndc, final_pos_ndc, travel_time_s, 0)
        outward_vy = vcalc(initial_pos_ndc, final_pos_ndc, travel_time_s, 1)
        return_vx = vcalc(initial_pos_ndc, final_pos_ndc, loom_wait_time_s, 0)
        return_vy = vcalc(initial_pos_ndc, final_pos_ndc, loom_wait_time_s, 1)

        # Convert cm sizes to scale factors
        # Keep your original calibration here if you had correction/screen_x.
        # For now we interpret "cm" as arbitrary scale units; replace if needed.
        # initial_scale = float(initial_size_cm)
        # final_scale = float(final_size_cm)
        screen_x = 105.41 * 2
        screen_y = 59.29
        correction = 1.125
        initial_scale = initial_size_cm / screen_x * correction
        final_scale = final_size_cm / screen_x * correction

        outward_scale_rate = (final_scale - initial_scale) / float(travel_time_s)
        return_scale_rate = (final_scale - initial_scale) / float(loom_wait_time_s)

        def _reset_motion():
            return {
                "x": float(initial_pos_ndc[0]),
                "y": float(initial_pos_ndc[1]),
                "scale": float(initial_scale),
                "vx": float(outward_vx),
                "vy": float(outward_vy),
                "scale_rate": float(outward_scale_rate),
                "destination": tuple(final_pos_ndc),
                "travel_state_outward": True,
            }

        motion = _reset_motion()
        batch = LoomBatchRunState(round_size=int(repetitions_per_round))

        start_requested = False
        last_start_requested = False
        shutdown_requested = False

        animation_t0 = time.time()
        prev_elapsed = 0.0

        _status({"type": "stimulus_ready"})

        # -----------------------------
        # Render loop
        # -----------------------------
        while not glfw.window_should_close(window):
            # ---- drain IPC commands ----
            try:
                while True:
                    cmd, payload = command_queue.get_nowait()
                    cmd = str(cmd).lower()
                    if cmd == "start":
                        start_requested = True
                        _status({"type": "start_received"})
                    elif cmd == "stop":
                        start_requested = False
                        _status({"type": "stop_received"})
                    elif cmd == "shutdown":
                        shutdown_requested = True
                        _status({"type": "shutdown_received"})
                    elif cmd == "ping":
                        _status({"type": "pong"})
            except Exception:
                pass

            if shutdown_requested:
                break

            # Edge detect start/stop like TTL
            if start_requested and not last_start_requested:
                # ENTER edge: resume/continue. Do not restart if already active.
                was_active = batch.active
                batch.on_enter()

                # Only start a new round if we were previously inactive
                if not was_active:
                    motion = _reset_motion()
                    batch.reset_round()
                    animation_t0 = time.time()
                    prev_elapsed = 0.0
                    _status({"type": "round_started"})
                else:
                    # We were mid-round: just clear stop flag and continue
                    _status({"type": "continue_round"})

            if (not start_requested) and last_start_requested:
                batch.on_leave()
                _status({"type": "stop_after_round_set"})

            last_start_requested = start_requested

            # framebuffer size
            current_window_width, current_window_height = glfw.get_framebuffer_size(window)

            # Clear
            glUseProgram(shader_program)
            glClearColor(*background_rgba)
            glClear(GL_COLOR_BUFFER_BIT)

            image_visible = False

            if batch.active:
                elapsed = time.time() - animation_t0
                dt = elapsed - prev_elapsed
                if dt < 0.0:
                    dt = 0.0
                elif dt > 0.1:
                    dt = 0.1

                motion["x"] += motion["vx"] * dt
                motion["y"] += motion["vy"] * dt
                motion["scale"] += motion["scale_rate"] * dt

                if motion["travel_state_outward"]:
                    # Draw stimulus only outward like your original
                    transform = np.identity(4, dtype=np.float32)
                    transform = np.matmul(translate_matrix(motion["x"], motion["y"], 0.0), transform)
                    transform = np.matmul(scale_matrix(motion["scale"]), transform)

                    loc = glGetUniformLocation(shader_program, "transform")
                    glUniformMatrix4fv(loc, 1, GL_FALSE, transform)

                    glActiveTexture(GL_TEXTURE0)
                    OpenGL.GL.glBindTexture(GL_TEXTURE_2D, texture)
                    glBindVertexArray(vao)
                    glDrawElements(GL_TRIANGLES, 6, GL_UNSIGNED_INT, None)
                    image_visible = True

                # destination reached logic (mirrors your old _destination_reached)
                def _dest_reached(is_outward: bool) -> bool:
                    x_ok = (motion["vx"] < 0 and motion["x"] < motion["destination"][0]) or \
                           (motion["vx"] > 0 and motion["x"] > motion["destination"][0]) or \
                           (motion["vx"] == 0)
                    y_ok = (motion["vy"] < 0 and motion["y"] < motion["destination"][1]) or \
                           (motion["vy"] > 0 and motion["y"] > motion["destination"][1]) or \
                           (motion["vy"] == 0)
                    s_ok = (motion["scale"] >= final_scale) if is_outward else (motion["scale"] <= initial_scale)
                    return x_ok and y_ok and s_ok

                if _dest_reached(motion["travel_state_outward"]):
                    if motion["travel_state_outward"]:
                        # outward -> return leg
                        motion["x"] = float(final_pos_ndc[0])
                        motion["y"] = float(final_pos_ndc[1])
                        motion["scale"] = float(final_scale)
                        motion["destination"] = tuple(initial_pos_ndc)
                        motion["travel_state_outward"] = False
                        motion["vx"] = -float(return_vx)
                        motion["vy"] = -float(return_vy)
                        motion["scale_rate"] = -float(return_scale_rate)
                    else:
                        # return -> outward completes one round-trip
                        motion["x"] = float(initial_pos_ndc[0])
                        motion["y"] = float(initial_pos_ndc[1])
                        motion["scale"] = float(initial_scale)
                        motion["destination"] = tuple(final_pos_ndc)
                        motion["travel_state_outward"] = True
                        motion["vx"] = float(outward_vx)
                        motion["vy"] = float(outward_vy)
                        motion["scale_rate"] = float(outward_scale_rate)

                        batch.on_completed_round_trip()
                        _status({"type": "round_trip_completed", "count_in_round": batch.round_trip_counter_in_round})

                        if batch.current_round_completed():
                            if batch.should_start_next_round():
                                motion = _reset_motion()
                                batch.reset_round()
                                animation_t0 = time.time()
                                prev_elapsed = 0.0
                                _status({"type": "round_restarted"})
                            else:
                                batch.stop_now()
                                start_requested = False
                                _status({"type": "stopped_after_round"})

                prev_elapsed = elapsed

            # Photodiode marker
            # black when visible, white when not (same semantics)
            marker_rgba = (0.0, 0.0, 0.0, 1.0) if image_visible else (1.0, 1.0, 1.0, 1.0)
            y_center_px = int((photodiode_y_ndc * 0.5 + 0.5) * current_window_height)
            half_box = int(photodiode_box_px) // 2
            y_start = max(0, y_center_px - half_box)
            box_h = min(int(photodiode_box_px), current_window_height - y_start)
            box_w = int(photodiode_box_px)
            if box_h > 0 and box_w > 0:
                glEnable(GL_SCISSOR_TEST)
                glClearColor(*marker_rgba)
                glScissor(current_window_width - box_w, y_start, box_w, box_h)
                glClear(GL_COLOR_BUFFER_BIT)
                glDisable(GL_SCISSOR_TEST)
                glClearColor(*background_rgba)

            glfw.swap_buffers(window)
            glfw.poll_events()

        _status({"type": "stimulus_exited"})

    finally:
        # Cleanup GL resources
        try:
            if vao is not None:
                glDeleteVertexArrays(1, [vao])
        except Exception:
            pass
        try:
            if vbo is not None:
                glDeleteBuffers(1, [vbo])
        except Exception:
            pass
        try:
            if ebo is not None:
                glDeleteBuffers(1, [ebo])
        except Exception:
            pass
        try:
            if shader_program is not None:
                glDeleteProgram(shader_program)
        except Exception:
            pass
        try:
            if window is not None:
                glfw.destroy_window(window)
        except Exception:
            pass
        glfw.terminate()


def loom_stimulus_process_main(
    command_queue: "mp.Queue[tuple[str, dict]]",
    status_queue: "mp.Queue[dict]",
    stim_cfg: LoomStimulusConfig,
) -> None:
    """
    Process entrypoint for the looming stimulus.

    Notes
    -----
    This should wrap your existing GLFW/OpenGL code, but replace GPIO TTL inputs with
    queue commands:
      - "start" => equivalent to enter TTL (request_run_start)
      - "stop"  => equivalent to leave TTL (request_run_stop; finish round)
      - "shutdown" => graceful close

    Parameters
    ----------
    command_queue : multiprocessing.Queue
        Receives (cmd, payload) tuples.
    status_queue : multiprocessing.Queue
        Emits status dictionaries.
    cfg : LoomStimulusConfig
        Renderer configuration.
    """
    # Import inside process to avoid GL context issues in parent.
    import time as _time

    # --- Replace this block with your actual LoomStimulusRenderer integration ---
    run_requested = False
    shutdown = False

    status_queue.put({"type": "loom_stimulus_started"})

    # Skeleton loop: you will integrate this with your renderer loop
    while not shutdown:
        # Non-blocking drain commands
        try:
            while True:
                cmd, payload = command_queue.get_nowait()
                if cmd == "start":
                    run_requested = True
                    status_queue.put({"type": "loom_stimulus_run_requested", "value": True})
                elif cmd == "stop":
                    run_requested = False
                    status_queue.put({"type": "loom_stimulus_run_requested", "value": False})
                elif cmd == "shutdown":
                    shutdown = True
                    status_queue.put({"type": "loom_stimulus_shutdown"})
                elif cmd == "ping":
                    status_queue.put({"type": "loom_stimulus_pong"})
        except Exception:
            pass

        run_loom_stimulus_with_ipc(
            command_queue=command_queue,
            status_queue=status_queue,
            texture_path=stim_cfg.texture_path,
            initial_size_cm=stim_cfg.initial_size_cm,
            final_size_cm=stim_cfg.final_size_cm,
            initial_pos_ndc=stim_cfg.initial_pos_ndc,
            final_pos_ndc=stim_cfg.final_pos_ndc,
            travel_time_s=stim_cfg.travel_time_s,
            loom_wait_time_s=stim_cfg.loom_wait_time_s,
            repetitions_per_round=stim_cfg.round_size,
            image_angle_deg=stim_cfg.image_angle_deg,
            background_rgba=stim_cfg.background_rgba,
            # photodiode_box_px=stim_cfg.get("photodiode_box_px", 80),  # optional if you add
            # photodiode_y_ndc=stim_cfg.get("photodiode_y_ndc", 0.0),
            # monitor_index=stim_cfg.get("monitor_index", None),
            # fullscreen=stim_cfg.get("fullscreen", True),
            # window_size_px=tuple(stim_cfg.get("window_size_px", [1920, 1080])),
            # vsync=stim_cfg.get("vsync", True),
        )

        _time.sleep(0.01)

    # Cleanup should happen here (terminate GLFW etc.)


class LoomStimulusController:
    """
    Manage a looming stimulus renderer running in a separate process.

    Notes
    -----
    This avoids GLFW/OpenGL "must be main thread" issues and isolates crashes.

    Parameters
    ----------
    cfg : LoomStimulusConfig
        Stimulus configuration.
    """
    def __init__(self, cfg: LoomStimulusConfig) -> None:
        self.cfg = cfg
        self._cmd_q: "mp.Queue[tuple[str, dict]]" = mp.Queue()
        self._status_q: "mp.Queue[dict]" = mp.Queue()
        self._proc: Optional[mp.Process] = None

    def start(self) -> None:
        """Start the stimulus process if not running."""
        if self._proc is not None and self._proc.is_alive():
            return

        os.environ.setdefault("DISPLAY", ":0")
        os.environ.setdefault("XAUTHORITY", "/home/pi/.Xauthority")

        self._proc = mp.Process(
            target=loom_stimulus_process_main,
            args=(self._cmd_q, self._status_q, self.cfg),
            name="loom-stimulus",
            daemon=True,
        )
        self._proc.start()

    def send(self, cmd: LoomStimulusCommand, payload: Optional[dict] = None) -> None:
        """
        Send a command to the stimulus process.

        Parameters
        ----------
        cmd : {'start','stop','shutdown','ping'}
            Command type.
        payload : dict or None
            Optional command payload.
        """
        if payload is None:
            payload = {}
        self.start()
        self._cmd_q.put((str(cmd), payload))

    def poll_status(self, max_messages: int = 10) -> list[dict]:
        """
        Drain status messages from the stimulus process.

        Parameters
        ----------
        max_messages : int
            Maximum messages to drain per call.

        Returns
        -------
        messages : list of dict
            Status messages emitted by the stimulus process.
        """
        out = []
        for _ in range(int(max_messages)):
            try:
                out.append(self._status_q.get_nowait())
            except Exception:
                break
        return out

    def shutdown(self, timeout_s: float = 2.0) -> None:
        """Request shutdown and join/terminate best-effort."""
        if self._proc is None:
            return
        try:
            self.send("shutdown")
            self._proc.join(timeout=float(timeout_s))
            if self._proc.is_alive():
                self._proc.terminate()
        finally:
            self._proc = None
