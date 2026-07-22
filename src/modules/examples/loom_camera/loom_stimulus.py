import os
import signal
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
    scale_matrix, scale_matrix_seperate


LoomStimulusCommand = Literal["start", "stop", "abort", "shutdown", "ping"]


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
    start_monitor_index : int
        Controls how many monitors the window spans, using monitors sorted
        by physical X position (left to right). The window covers monitors
        0 through start_monitor_index inclusive:
          0 → left monitor only
          1 → both monitors (full dual-monitor span)
        Defaults to 0.
    flip_horizontal : bool
        When True, shifts all NDC x-coordinates left by one monitor-width
        so the stimulus moves from the right-side monitor to the left-side
        monitor in screen space. Use this when the physical monitors are
        cabled in the opposite order from their xrandr screen-space positions.
        Also moves the photodiode marker from the right edge to the left edge.
        Defaults to False.
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
    start_monitor_index: int = 0
    flip_horizontal: bool = False
    screen_width_cm: float = 105.41
    screen_height_cm: float = 59.29
    size_correction: float = 1.125
    photodiode_box_px: int = 80
    photodiode_y_ndc: float = 0.0
    keepalive_interval_s: float = 30.0


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
    screen_width_cm: float = 105.41,
    screen_height_cm: float = 59.29,
    size_correction: float = 1.125,
    keepalive_interval_s: float = 30.0,
    monitor_index: Optional[int] = None,
    fullscreen: bool = True,
    window_size_px: Tuple[int, int] = (1920, 1080),
    vsync: bool = True,
    flip_horizontal: bool = False,
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

    # -------------------------------------------------------------------
    # Force GLFW onto X11 / XWayland BEFORE glfw.init().
    #
    # Wayland forbids client-side window positioning (set_window_pos is a
    # no-op) and will not let a single surface span two outputs, so the
    # extended-desktop loom canvas collapses onto one monitor. X11/Xinerama
    # allows both, so we drop any inherited WAYLAND_DISPLAY here.
    # -------------------------------------------------------------------
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ.setdefault("DISPLAY", ":0")
    try:
        if hasattr(glfw, "PLATFORM") and hasattr(glfw, "PLATFORM_X11"):
            glfw.init_hint(glfw.PLATFORM, glfw.PLATFORM_X11)  # GLFW 3.4+
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

    # Defined here so the span_debug status can reference them regardless of
    # which branch (fullscreen / windowed) ran.
    x0 = y0 = 0

    try:
        # -----------------------------
        # Window / monitor selection
        # -----------------------------
        glfw.default_window_hints()

        # Request OpenGL ES 3.0 context (Raspberry Pi friendly)
        glfw.window_hint(glfw.CLIENT_API, glfw.OPENGL_ES_API)
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 0)
        # Borderless, always-visible window — prevent compositor auto-minimise
        glfw.window_hint(glfw.DECORATED, glfw.FALSE)
        glfw.window_hint(glfw.AUTO_ICONIFY, glfw.FALSE)
        glfw.window_hint(glfw.FOCUSED, glfw.TRUE)
        monitors = glfw.get_monitors() or []

        if fullscreen:
            if not monitors:
                raise RuntimeError("No monitors detected by GLFW.")

            # Collect monitor rects and sort by X position (left to right).
            rects = []
            for m in monitors:
                mx, my = glfw.get_monitor_pos(m)
                vm = glfw.get_video_mode(m)
                rects.append((mx, my, vm.size.width, vm.size.height))
            rects.sort(key=lambda r: (r[0], r[1]))

            # Include monitors 0 through start_monitor_index (inclusive).
            # index=0 → left monitor only; index=1 → both monitors (span left to right).
            start_idx = max(0, min(int(monitor_index or 0), len(rects) - 1))
            selected = rects[0:start_idx + 1]

            x0 = min(r[0] for r in selected)
            y0 = min(r[1] for r in selected)
            x1 = max(r[0] + r[2] for r in selected)
            y1 = max(r[1] + r[3] for r in selected)
            w, h = x1 - x0, y1 - y0

            # One borderless window covering the selected monitors as a single surface.
            glfw.window_hint(glfw.FLOATING, glfw.TRUE)
            # GLFW 3.4+: set initial position as a hint so the WM cannot override
            # placement before the window is first shown (more reliable than
            # set_window_pos() called after creation on most X11 compositors).
            try:
                if hasattr(glfw, "POSITION_X"):
                    glfw.window_hint(glfw.POSITION_X, x0)
                    glfw.window_hint(glfw.POSITION_Y, y0)
            except Exception:
                pass
            window = glfw.create_window(w, h, "Loom Stimulus", None, None)
            if window is not None:
                glfw.set_window_pos(window, x0, y0)
        else:
            w, h = int(window_size_px[0]), int(window_size_px[1])
            window = glfw.create_window(w, h, "Loom Stimulus", None, None)

        if window is None:
            raise RuntimeError("Failed to create GLFW window (window=None). Check GLX/Mesa driver stack.")

        glfw.make_context_current(window)
        glfw.set_framebuffer_size_callback(window, framebuffer_size_callback)
        glfw.swap_interval(1 if vsync else 0)

        # Drive viewport from the FRAMEBUFFER, not the video mode.
        current_window_width, current_window_height = glfw.get_framebuffer_size(window)
        glViewport(0, 0, current_window_width, current_window_height)

        _status({
            "type": "span_debug",
            "fb_wh": glfw.get_framebuffer_size(window),
            "win_wh": glfw.get_window_size(window),
            "n_monitors": len(monitors),
            "selected_rects": selected if fullscreen else [],
            "flip_horizontal": flip_horizontal,
        })

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
        # Convert cm sizes to scale factors.
        # screen_x scales with however many monitors the window spans.
        n_selected = len(selected) if fullscreen else 1

        # In dual-monitor mode, flip_horizontal picks the monitor:
        #   False → GL-right monitor (index n_selected-1)
        #   True  → GL-left monitor  (index 0)
        # The loom is always centred on that monitor; initial_pos_ndc[0] is
        # ignored for X so the controller config value can't misplace it.
        if fullscreen and n_selected >= 2:
            _mon_ndc_w = 2.0 / n_selected
            _stim_mon_idx = 0 if flip_horizontal else (n_selected - 1)
        else:
            _stim_mon_idx = 0

        outward_vx = vcalc(initial_pos_ndc, final_pos_ndc, travel_time_s, 0)
        outward_vy = vcalc(initial_pos_ndc, final_pos_ndc, travel_time_s, 1)
        return_vx = vcalc(initial_pos_ndc, final_pos_ndc, loom_wait_time_s, 0)
        return_vy = vcalc(initial_pos_ndc, final_pos_ndc, loom_wait_time_s, 1)
        screen_x = screen_width_cm * n_selected
        screen_y = screen_height_cm
        correction = size_correction
        # Separate x and y scale factors so the stimulus renders as a circle,
        # not an oval.  The combined canvas (e.g. 3840×1080 for dual 1920×1080
        # monitors) has a much wider NDC x range than y range; applying a single
        # uniform scale squashes the circle into a wide ellipse.
        initial_scale_x = initial_size_cm / screen_x * correction
        initial_scale_y = initial_size_cm / screen_y * correction
        final_scale_x   = final_size_cm   / screen_x * correction
        final_scale_y   = final_size_cm   / screen_y * correction

        outward_scale_x_rate = (final_scale_x - initial_scale_x) / float(travel_time_s)
        outward_scale_y_rate = (final_scale_y - initial_scale_y) / float(travel_time_s)
        return_scale_x_rate  = (final_scale_x - initial_scale_x) / float(loom_wait_time_s)
        return_scale_y_rate  = (final_scale_y - initial_scale_y) / float(loom_wait_time_s)

        def _reset_motion():
            return {
                "x": float(initial_pos_ndc[0]),
                "y": float(initial_pos_ndc[1]),
                "scale_x": float(initial_scale_x),
                "scale_y": float(initial_scale_y),
                "vx": float(outward_vx),
                "vy": float(outward_vy),
                "scale_x_rate": float(outward_scale_x_rate),
                "scale_y_rate": float(outward_scale_y_rate),
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

        # Keep-alive: prevent TV auto-dimming by drawing one imperceptible pixel
        # on the near TV at the configured interval.
        _keepalive_t0    = time.time()
        _keepalive_phase = 0  # toggles between two visually identical grey values

        # Near screen test flash (from "test_near_screen" command).
        _near_test_until = 0.0

        # Pre-compute which X column the near TV starts at (opposite of stimulus).
        _mon_w_px = 0  # set each frame from framebuffer width

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
                        # Soft stop: finish the current round of `round_size`
                        # looms before halting. The enter/leave edge detection
                        # below calls batch.on_leave() to set the flag.
                        start_requested = False
                        _status({"type": "stop_received"})
                    elif cmd == "abort":
                        # Hard stop: immediately halt mid-round.
                        start_requested = False
                        batch.stop_now()
                        motion = _reset_motion()
                        _status({"type": "abort_received"})
                    elif cmd == "shutdown":
                        shutdown_requested = True
                        _status({"type": "shutdown_received"})
                    elif cmd == "ping":
                        _status({"type": "pong"})
                    elif cmd == "test_near_screen":
                        _near_test_until = time.time() + float(payload.get("duration_s", 2.0))
                        _status({"type": "near_screen_test_started"})
                    elif cmd == "reconfigure":
                        # Hot-patch stimulus params without restarting the GL window.
                        # Window-dependent params (start_monitor_index, flip_horizontal)
                        # are intentionally excluded — those require a full restart.
                        p = payload
                        background_rgba      = tuple(p.get("background_rgba", background_rgba))
                        photodiode_box_px    = int(p.get("photodiode_box_px", photodiode_box_px))
                        photodiode_y_ndc     = float(p.get("photodiode_y_ndc", photodiode_y_ndc))
                        initial_size_cm      = float(p.get("initial_size_cm", initial_size_cm))
                        final_size_cm        = float(p.get("final_size_cm", final_size_cm))
                        screen_width_cm      = float(p.get("screen_width_cm", screen_width_cm))
                        screen_height_cm     = float(p.get("screen_height_cm", screen_height_cm))
                        size_correction      = float(p.get("size_correction", size_correction))
                        travel_time_s        = float(p.get("travel_time_s", travel_time_s))
                        loom_wait_time_s     = float(p.get("loom_wait_time_s", loom_wait_time_s))
                        batch.round_size     = int(p.get("round_size", batch.round_size))
                        keepalive_interval_s = float(p.get("keepalive_interval_s", keepalive_interval_s))
                        _ini = p.get("initial_pos_ndc")
                        _fin = p.get("final_pos_ndc")
                        _ini_ndc = tuple(_ini) if _ini is not None else initial_pos_ndc
                        _fin_ndc = tuple(_fin) if _fin is not None else final_pos_ndc
                        initial_pos_ndc = _ini_ndc
                        final_pos_ndc   = _fin_ndc
                        # Recompute all derived motion params (take effect on next _reset_motion()).
                        _sx = screen_width_cm * n_selected
                        initial_scale_x      = initial_size_cm / _sx * size_correction
                        initial_scale_y      = initial_size_cm / screen_height_cm * size_correction
                        final_scale_x        = final_size_cm   / _sx * size_correction
                        final_scale_y        = final_size_cm   / screen_height_cm * size_correction
                        outward_vx           = vcalc(initial_pos_ndc, final_pos_ndc, travel_time_s, 0)
                        outward_vy           = vcalc(initial_pos_ndc, final_pos_ndc, travel_time_s, 1)
                        return_vx            = vcalc(initial_pos_ndc, final_pos_ndc, loom_wait_time_s, 0)
                        return_vy            = vcalc(initial_pos_ndc, final_pos_ndc, loom_wait_time_s, 1)
                        outward_scale_x_rate = (final_scale_x - initial_scale_x) / float(travel_time_s)
                        outward_scale_y_rate = (final_scale_y - initial_scale_y) / float(travel_time_s)
                        return_scale_x_rate  = (final_scale_x - initial_scale_x) / float(loom_wait_time_s)
                        return_scale_y_rate  = (final_scale_y - initial_scale_y) / float(loom_wait_time_s)
                        # Reload texture if path or angle changed.
                        _new_path  = p.get("texture_path")
                        _new_angle = p.get("image_angle_deg")
                        if _new_path is not None or _new_angle is not None:
                            texture_path    = str(_new_path)  if _new_path  is not None else texture_path
                            image_angle_deg = float(_new_angle) if _new_angle is not None else image_angle_deg
                            try:
                                OpenGL.GL.glDeleteTextures(1, [texture])
                            except Exception:
                                pass
                            texture = load_texture(texture_path, image_angle_deg)
                        _status({"type": "reconfigure_applied"})
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

            # framebuffer size (spans both monitors)
            current_window_width, current_window_height = glfw.get_framebuffer_size(window)
            glViewport(0, 0, current_window_width, current_window_height)

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
                motion["scale_x"] += motion["scale_x_rate"] * dt
                motion["scale_y"] += motion["scale_y_rate"] * dt

                if motion["travel_state_outward"]:
                    # Draw stimulus only outward like your original
                    # numpy is row-major; OpenGL reads flat data as column-major (GL_FALSE),
                    # so numpy A@B becomes B@A in OpenGL's column-vector convention.
                    # We want OpenGL to do: translate(centre) @ scale(radius).
                    # Therefore in numpy we must write: scale @ translate (reversed).
                    transform = np.identity(4, dtype=np.float32)
                    transform = np.matmul(translate_matrix(motion["x"], motion["y"], 0.0), transform)
                    transform = np.matmul(scale_matrix_seperate(motion["scale_x"], motion["scale_y"]), transform)

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
                    s_ok = (motion["scale_x"] >= final_scale_x) if is_outward else (motion["scale_x"] <= initial_scale_x)
                    return x_ok and y_ok and s_ok

                if _dest_reached(motion["travel_state_outward"]):
                    if motion["travel_state_outward"]:
                        # outward -> return leg
                        motion["x"] = float(final_pos_ndc[0])
                        motion["y"] = float(final_pos_ndc[1])
                        motion["scale_x"] = float(final_scale_x)
                        motion["scale_y"] = float(final_scale_y)
                        motion["destination"] = tuple(initial_pos_ndc)
                        motion["travel_state_outward"] = False
                        motion["vx"] = -float(return_vx)
                        motion["vy"] = -float(return_vy)
                        motion["scale_x_rate"] = -float(return_scale_x_rate)
                        motion["scale_y_rate"] = -float(return_scale_y_rate)
                    else:
                        # return -> outward completes one round-trip
                        motion["x"] = float(initial_pos_ndc[0])
                        motion["y"] = float(initial_pos_ndc[1])
                        motion["scale_x"] = float(initial_scale_x)
                        motion["scale_y"] = float(initial_scale_y)
                        motion["destination"] = tuple(final_pos_ndc)
                        motion["travel_state_outward"] = True
                        motion["vx"] = float(outward_vx)
                        motion["vy"] = float(outward_vy)
                        motion["scale_x_rate"] = float(outward_scale_x_rate)
                        motion["scale_y_rate"] = float(outward_scale_y_rate)

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
                # Right/outer edge of the monitor the stimulus is on.
                # GL-right of the stimulus monitor = physical outer edge of that TV
                # (far end of arena when the stimulus is on the far TV).
                box_x = (_stim_mon_idx + 1) * (current_window_width // n_selected) - box_w
                glScissor(box_x, y_start, box_w, box_h)
                glClear(GL_COLOR_BUFFER_BIT)
                glDisable(GL_SCISSOR_TEST)
                glClearColor(*background_rgba)

            _mon_w_px = current_window_width // n_selected
            _near_mon_idx = (n_selected - 1 - _stim_mon_idx) if n_selected >= 2 else -1
            _near_x = _near_mon_idx * _mon_w_px

            # Test flash: briefly show a visible grey on the near TV so the user
            # can confirm the GL canvas is reaching that monitor.
            _now = time.time()
            if _near_mon_idx >= 0 and _now < _near_test_until:
                glEnable(GL_SCISSOR_TEST)
                glClearColor(0.85, 0.85, 0.85, 1.0)
                glScissor(_near_x, 0, _mon_w_px, current_window_height)
                glClear(GL_COLOR_BUFFER_BIT)
                glDisable(GL_SCISSOR_TEST)
                glClearColor(*background_rgba)

            # Keep-alive: write one imperceptible pixel in the bottom corner of the
            # near TV at the configured interval to prevent OLED auto-dimming.
            # The pixel alternates between bg±(1/255) — invisible to eye and camera.
            if _near_mon_idx >= 0 and keepalive_interval_s > 0:
                if _now - _keepalive_t0 >= keepalive_interval_s:
                    _keepalive_phase ^= 1
                    _keepalive_t0 = _now
                    _bg = background_rgba[0]
                    _kv = min(1.0, _bg + 1/255) if _keepalive_phase else max(0.0, _bg - 1/255)
                    glEnable(GL_SCISSOR_TEST)
                    glClearColor(_kv, _kv, _kv, 1.0)
                    glScissor(_near_x, 0, 1, 1)
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
    # Die immediately if the parent process is killed (even with SIGKILL).
    # daemon=True only handles clean parent exit; prctl handles the rest.
    try:
        _libc = ctypes.CDLL("libc.so.6", use_errno=True)
        _libc.prctl(1, signal.SIGKILL, 0, 0, 0)  # PR_SET_PDEATHSIG = 1
    except Exception:
        pass

    status_queue.put({"type": "loom_stimulus_started"})

    try:
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
            photodiode_box_px=stim_cfg.photodiode_box_px,
            photodiode_y_ndc=stim_cfg.photodiode_y_ndc,
            screen_width_cm=stim_cfg.screen_width_cm,
            screen_height_cm=stim_cfg.screen_height_cm,
            size_correction=stim_cfg.size_correction,
            monitor_index=stim_cfg.start_monitor_index,
            flip_horizontal=stim_cfg.flip_horizontal,
            keepalive_interval_s=stim_cfg.keepalive_interval_s,
        )
    except Exception as exc:
        status_queue.put({"type": "loom_stimulus_error", "error": str(exc)})


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

        # Force X11: the renderer needs client-side window positioning and a
        # single surface spanning both monitors, neither of which Wayland
        # allows. Drop any inherited WAYLAND_DISPLAY so GLFW uses X11/XWayland.
        os.environ.setdefault("DISPLAY", ":0")
        os.environ.setdefault("XAUTHORITY", "/home/pi/.Xauthority")
        os.environ.pop("WAYLAND_DISPLAY", None)

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

    def reconfigure(self, payload: dict) -> None:
        """Hot-patch stimulus params into the running renderer without restarting the GL window."""
        if self._proc is None or not self._proc.is_alive():
            return
        self._cmd_q.put(("reconfigure", payload))

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
        """Request shutdown and join/terminate/kill best-effort."""
        if self._proc is None:
            return
        try:
            self.send("shutdown")
            self._proc.join(timeout=float(timeout_s))
            if self._proc.is_alive():
                self._proc.terminate()          # SIGTERM
                self._proc.join(timeout=1.0)
            if self._proc.is_alive():
                self._proc.kill()               # SIGKILL — last resort
                self._proc.join(timeout=1.0)
        finally:
            self._proc = None
