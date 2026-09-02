"""
Tests for src/modules/mjpeg_stream.py.

Constructing MJPEGStreamServer is side-effect-free (just a Flask app
object, no server binding until start()/_run() actually runs), so it's
built directly rather than via __new__. start()/_run() are tested with
threading.Thread/werkzeug's make_server mocked so nothing here ever binds
a real socket.
"""

from unittest.mock import MagicMock, patch

from src.modules.mjpeg_stream import MJPEGStreamServer

# ---------------------------------------------------------------------------
# Frame mailbox
# ---------------------------------------------------------------------------

class TestFrameMailbox:
    def test_push_then_get_returns_the_frame(self):
        server = MJPEGStreamServer()
        server.push_frame(b"jpeg-bytes")
        assert server.get_latest_frame() == b"jpeg-bytes"

    def test_no_frame_yet_returns_none(self):
        server = MJPEGStreamServer()
        assert server.get_latest_frame() is None

    def test_clear_frame_discards_current_frame(self):
        server = MJPEGStreamServer()
        server.push_frame(b"jpeg-bytes")
        server.clear_frame()
        assert server.get_latest_frame() is None

    def test_latest_push_wins(self):
        server = MJPEGStreamServer()
        server.push_frame(b"first")
        server.push_frame(b"second")
        assert server.get_latest_frame() == b"second"


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

class TestRoutes:
    def test_index_reports_server_name(self):
        server = MJPEGStreamServer(name="camera1")
        client = server.app.test_client()
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"camera1 Monitoring Server" in resp.data

    def test_video_feed_has_multipart_mimetype(self):
        server = MJPEGStreamServer()
        server.should_stop = True  # generator must not loop forever if consumed
        client = server.app.test_client()
        resp = client.get("/video_feed")
        assert resp.mimetype == "multipart/x-mixed-replace"


class TestSnapshotRoute:
    """`/snapshot.jpg` is served by MJPEGStreamServer itself (not per module
    type), so every module with a monitoring stream — camera preview,
    microphone spectrogram, TTL trace, RFID reads — exposes it. The frontend
    screenshot button and the crop editor both fetch it cross-origin, hence
    the CORS + no-store headers on every response including the 503."""

    def _headers_ok(self, resp):
        assert resp.headers["Content-Type"] == "image/jpeg"
        assert resp.headers["Access-Control-Allow-Origin"] == "*"
        assert resp.headers["Cache-Control"] == "no-store"

    def test_push_mode_returns_last_pushed_frame(self):
        server = MJPEGStreamServer(name="camera")
        server.push_frame(b"jpeg-bytes")
        resp = server.app.test_client().get("/snapshot.jpg")
        assert resp.status_code == 200
        assert resp.data == b"jpeg-bytes"
        self._headers_ok(resp)

    def test_503_with_cors_headers_when_no_frame(self):
        server = MJPEGStreamServer()
        resp = server.app.test_client().get("/snapshot.jpg")
        assert resp.status_code == 503
        self._headers_ok(resp)

    def test_pull_mode_renders_on_demand(self):
        # No client is watching the stream, so the frame mailbox is empty —
        # a pull-mode server must render one fresh instead of 503ing.
        calls = []
        def render():
            calls.append(1)
            return b"spectro-%d" % len(calls)
        server = MJPEGStreamServer(render_fn=render, name="microphone")
        client = server.app.test_client()
        assert client.get("/snapshot.jpg").data == b"spectro-1"
        assert client.get("/snapshot.jpg").data == b"spectro-2"

    def test_pull_render_returning_none_falls_back_to_last_frame(self):
        server = MJPEGStreamServer(render_fn=lambda: None)
        server.push_frame(b"stale-but-real")
        assert server.app.test_client().get("/snapshot.jpg").data == b"stale-but-real"

    def test_pull_render_raising_is_caught_and_503s(self):
        def boom():
            raise RuntimeError("camera busy")
        server = MJPEGStreamServer(render_fn=boom, logger=MagicMock())
        resp = server.app.test_client().get("/snapshot.jpg")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# _mjpeg_chunk
# ---------------------------------------------------------------------------

class TestMjpegChunk:
    def test_wraps_frame_in_multipart_boundary(self):
        chunk = MJPEGStreamServer._mjpeg_chunk(b"DATA")
        assert chunk == (
            b"--frame\r\nContent-Type: image/jpeg\r\n\r\nDATA\r\n"
        )


# ---------------------------------------------------------------------------
# Frame generators
# ---------------------------------------------------------------------------

class TestGenerateFramesPull:
    def test_yields_chunks_from_render_fn(self):
        frames = iter([b"f1", b"f2"])
        server = MJPEGStreamServer(render_fn=lambda: next(frames), interval=0.0)
        gen = server._generate_frames_pull()
        assert next(gen) == MJPEGStreamServer._mjpeg_chunk(b"f1")
        assert next(gen) == MJPEGStreamServer._mjpeg_chunk(b"f2")
        server.should_stop = True

    def test_none_frames_are_skipped(self):
        frames = iter([None, b"f1"])
        server = MJPEGStreamServer(render_fn=lambda: next(frames), interval=0.0)
        gen = server._generate_frames_pull()
        assert next(gen) == MJPEGStreamServer._mjpeg_chunk(b"f1")


class TestGenerateFramesPush:
    def test_yields_each_new_pushed_frame_once(self):
        server = MJPEGStreamServer(poll_interval=0.0)
        server.push_frame(b"f1")
        gen = server._generate_frames_push()
        assert next(gen) == MJPEGStreamServer._mjpeg_chunk(b"f1")

        # Same frame again (by identity) must not be re-yielded; pushing a
        # new one must be.
        server.push_frame(b"f2")
        assert next(gen) == MJPEGStreamServer._mjpeg_chunk(b"f2")

    def test_dispatches_to_pull_or_push_based_on_render_fn(self):
        pull_server = MJPEGStreamServer(render_fn=lambda: b"x", interval=0.0)
        pull_server.should_stop = True
        assert list(pull_server._generate_frames()) == []

        push_server = MJPEGStreamServer()
        push_server.push_frame(b"f1")
        gen = push_server._generate_frames()
        assert next(gen) == MJPEGStreamServer._mjpeg_chunk(b"f1")


# ---------------------------------------------------------------------------
# start / stop / _run -- threading.Thread and make_server mocked
# ---------------------------------------------------------------------------

class TestStartStop:
    def test_start_spawns_daemon_thread(self):
        server = MJPEGStreamServer(name="cam1")
        with patch("src.modules.mjpeg_stream.threading.Thread") as mock_thread:
            result = server.start(8080)

        assert result is True
        assert server.is_streaming is True
        kwargs = mock_thread.call_args.kwargs
        assert kwargs["target"] == server._run
        assert kwargs["args"] == (8080,)
        assert kwargs["daemon"] is True
        mock_thread.return_value.start.assert_called_once()

    def test_start_twice_is_rejected(self):
        server = MJPEGStreamServer()
        with patch("src.modules.mjpeg_stream.threading.Thread"):
            server.start(8080)
            result = server.start(8080)
        assert result is False

    def test_stop_when_not_running_returns_false(self):
        server = MJPEGStreamServer()
        assert server.stop() is False

    def test_stop_shuts_down_server_and_joins_thread(self):
        server = MJPEGStreamServer()
        server.is_streaming = True
        mock_server = MagicMock()
        mock_thread = MagicMock()
        server.server = mock_server
        server.server_thread = mock_thread

        result = server.stop()

        assert result is True
        assert server.is_streaming is False
        mock_server.shutdown.assert_called_once()
        mock_thread.join.assert_called_once_with(timeout=3)
        assert server.server is None
        assert server.server_thread is None


class TestRun:
    def test_starts_werkzeug_server_and_serves_forever(self):
        server = MJPEGStreamServer(name="cam1")
        with patch("src.modules.mjpeg_stream.make_server") as mock_make_server:
            server._run(8080)

        mock_make_server.assert_called_once_with(
            "0.0.0.0", 8080, server.app, threaded=True
        )
        mock_make_server.return_value.serve_forever.assert_called_once()

    def test_exception_resets_streaming_state(self):
        server = MJPEGStreamServer()
        server.is_streaming = True
        with patch(
            "src.modules.mjpeg_stream.make_server", side_effect=OSError("port in use")
        ):
            server._run(8080)
        assert server.server is None
        assert server.is_streaming is False

    def test_logs_error_when_logger_provided(self):
        logger = MagicMock()
        server = MJPEGStreamServer(logger=logger)
        with patch(
            "src.modules.mjpeg_stream.make_server", side_effect=OSError("port in use")
        ):
            server._run(8080)
        logger.error.assert_called_once()
