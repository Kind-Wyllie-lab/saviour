#!/usr/bin/env python3
"""
SAVIOUR System - MJPEG monitoring stream helper

Bolt-on Flask/MJPEG server used by module types that expose a live monitoring
view (TTL logic-analyser trace, AudioMoth spectrogram, etc). Composition, not
a Module subclass: the only thing duplicated across those module types was
this HTTP bootstrap, not any recording or hardware-sampling logic, so a
standalone class the module owns is a better fit than another inheritance
layer.
"""

import threading
import time

from flask import Flask, Response
from werkzeug.serving import make_server


class MJPEGStreamServer:
    """Serves frames as MJPEG over HTTP. Supports two ways of supplying frames:

    - Pull mode (pass render_fn): render_fn is called with no arguments on a
      fixed interval and must return JPEG bytes or None (None frames are
      skipped). Use this when computing a frame is cheap and only needs to
      happen when someone's actually watching — e.g. drawing a GPIO trace or
      a spectrogram. Any per-call inputs the caller needs (e.g. reading
      config for plot mode) belong in the render_fn closure/method.

    - Push mode (omit render_fn, call push_frame()): some callback or thread
      you don't control (e.g. a camera encoder) produces frames on its own
      schedule and hands them over via push_frame(). The server polls a
      one-slot mailbox at poll_interval and forwards each new frame exactly
      once (by identity), so send rate is decoupled from the producer's rate
      without re-sending duplicates.

    Only one mode should be used per instance.
    """

    def __init__(self, render_fn=None, interval: float = 0.1, logger=None,
                 name: str = "monitor", poll_interval: float = 0.005):
        self.render_fn = render_fn
        self.interval = interval
        self.poll_interval = poll_interval
        self.logger = logger
        self.name = name

        self.app = Flask(f"{name}_monitor")
        self._register_routes()

        self.server = None
        self.server_thread = None
        self.is_streaming = False
        self.should_stop = False

        self._latest_frame = None
        self._frame_lock = threading.Lock()

    def push_frame(self, frame: bytes) -> None:
        """Push-mode producers call this with each new JPEG frame."""
        with self._frame_lock:
            self._latest_frame = frame

    def get_latest_frame(self):
        """Return the most recently pushed frame (bytes) or None. Also useful
        for a snapshot endpoint that wants the same bytes as the stream."""
        with self._frame_lock:
            return self._latest_frame

    def clear_frame(self) -> None:
        """Discard the current frame, e.g. after reconfiguring the source, so
        reconnecting clients wait for fresh data instead of a stale frame."""
        with self._frame_lock:
            self._latest_frame = None

    def _register_routes(self):
        @self.app.route('/')
        def index():
            return f"{self.name} Monitoring Server"

        @self.app.route('/video_feed')
        def video_feed():
            return Response(
                self._generate_frames(),
                mimetype='multipart/x-mixed-replace; boundary=frame'
            )

    @staticmethod
    def _mjpeg_chunk(frame: bytes) -> bytes:
        return (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" +
            frame +
            b"\r\n"
        )

    def _generate_frames(self):
        if self.render_fn is not None:
            yield from self._generate_frames_pull()
        else:
            yield from self._generate_frames_push()

    def _generate_frames_pull(self):
        while not self.should_stop:
            frame = self.render_fn()
            if frame is not None:
                yield self._mjpeg_chunk(frame)
            time.sleep(self.interval)

    def _generate_frames_push(self):
        last_frame = None
        while not self.should_stop:
            frame = self.get_latest_frame()
            if frame is None or frame is last_frame:
                time.sleep(self.poll_interval)
                continue
            last_frame = frame
            yield self._mjpeg_chunk(frame)

    def _run(self, port: int):
        try:
            self.server = make_server('0.0.0.0', port, self.app, threaded=True)
            if self.logger:
                self.logger.info(f"{self.name} monitoring server listening on port {port}")
            self.server.serve_forever()
        except Exception as e:
            if self.logger:
                self.logger.error(f"{self.name} monitoring server error: {e}")
            self.server = None
            self.is_streaming = False

    def start(self, port: int) -> bool:
        """Start the server thread. Returns False if already running."""
        if self.is_streaming:
            if self.logger:
                self.logger.warning(f"{self.name} monitoring stream already running")
            return False

        self.should_stop = False
        self.server_thread = threading.Thread(
            target=self._run, args=(port,), daemon=True, name=f"{self.name}-mjpeg-server"
        )
        self.server_thread.start()
        self.is_streaming = True
        return True

    def stop(self) -> bool:
        """Stop the server thread. Returns False if not running."""
        if not self.is_streaming:
            return False

        self.should_stop = True
        if self.server:
            self.server.shutdown()
            self.server = None
        if self.server_thread:
            self.server_thread.join(timeout=3)
            self.server_thread = None
        self.is_streaming = False
        return True
