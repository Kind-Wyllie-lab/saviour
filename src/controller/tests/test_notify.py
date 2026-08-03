"""
Tests for src/controller/notify.py.

Notifier.__init__ has no real side effects (just config + a lock + a dedup
dict), so it's constructed directly rather than via __new__. Covers the
cooldown/dedup logic in send_alert, check_internet and the webhook POST
flow in send_test/_send with socket/urlopen mocked -- nothing here ever
makes a real network call.
"""

from unittest.mock import MagicMock, patch
from urllib.error import URLError

from src.controller.notify import Notifier


def _make_config(**overrides) -> MagicMock:
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: overrides.get(key, default)
    return cfg


def _make_notifier(**config_overrides) -> Notifier:
    return Notifier(_make_config(**config_overrides))


# ---------------------------------------------------------------------------
# send_alert -- webhook gate, cooldown dedup, background thread dispatch
# ---------------------------------------------------------------------------

class TestSendAlert:
    def test_no_webhook_configured_is_a_no_op(self):
        notifier = _make_notifier(**{"teams.webhook_url": ""})
        with patch("src.controller.notify.threading.Thread") as mock_thread:
            notifier.send_alert("key1", "title", "message")
        mock_thread.assert_not_called()

    def test_dispatches_a_background_thread_when_allowed(self):
        notifier = _make_notifier(**{
            "teams.webhook_url": "https://example.invalid/webhook",
            "teams.alert_cooldown_secs": 600,
        })
        with patch("src.controller.notify.threading.Thread") as mock_thread:
            notifier.send_alert("key1", "title", "message", severity="warning")

        mock_thread.assert_called_once()
        kwargs = mock_thread.call_args.kwargs
        assert kwargs["target"] == notifier._send
        assert kwargs["args"] == (
            "https://example.invalid/webhook", "title", "message", "warning"
        )
        assert kwargs["daemon"] is True
        mock_thread.return_value.start.assert_called_once()

    def test_same_key_within_cooldown_is_suppressed(self):
        notifier = _make_notifier(**{
            "teams.webhook_url": "https://example.invalid/webhook",
            "teams.alert_cooldown_secs": 600,
        })
        with patch("src.controller.notify.threading.Thread") as mock_thread:
            notifier.send_alert("key1", "title", "message")
            notifier.send_alert("key1", "title2", "message2")

        mock_thread.assert_called_once()  # only the first call fired

    def test_different_keys_are_independent(self):
        notifier = _make_notifier(**{
            "teams.webhook_url": "https://example.invalid/webhook",
            "teams.alert_cooldown_secs": 600,
        })
        with patch("src.controller.notify.threading.Thread") as mock_thread:
            notifier.send_alert("key1", "title", "message")
            notifier.send_alert("key2", "title", "message")

        assert mock_thread.call_count == 2

    def test_zero_cooldown_allows_immediate_repeats(self):
        notifier = _make_notifier(**{
            "teams.webhook_url": "https://example.invalid/webhook",
            "teams.alert_cooldown_secs": 0,
        })
        with patch("src.controller.notify.threading.Thread") as mock_thread:
            notifier.send_alert("key1", "title", "message")
            notifier.send_alert("key1", "title", "message")

        assert mock_thread.call_count == 2


# ---------------------------------------------------------------------------
# check_internet
# ---------------------------------------------------------------------------

class TestCheckInternet:
    def test_reachable_returns_true(self):
        notifier = _make_notifier()
        with patch("src.controller.notify.socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__ = MagicMock()
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            assert notifier.check_internet() is True

    def test_unreachable_returns_false(self):
        notifier = _make_notifier()
        with patch(
            "src.controller.notify.socket.create_connection",
            side_effect=OSError("network unreachable"),
        ):
            assert notifier.check_internet() is False


# ---------------------------------------------------------------------------
# _controller_name
# ---------------------------------------------------------------------------

class TestControllerName:
    def test_uses_configured_name(self):
        notifier = _make_notifier(**{"controller.name": "habitat-1"})
        assert notifier._controller_name() == "habitat-1"

    def test_falls_back_to_hostname(self):
        notifier = _make_notifier(**{"controller.name": ""})
        with patch(
            "src.controller.notify.socket.gethostname", return_value="pi-controller"
        ):
            assert notifier._controller_name() == "pi-controller"

    def test_falls_back_to_unknown_on_hostname_error(self):
        notifier = _make_notifier(**{"controller.name": ""})
        with patch(
            "src.controller.notify.socket.gethostname", side_effect=OSError("boom")
        ):
            assert notifier._controller_name() == "unknown"


# ---------------------------------------------------------------------------
# send_test -- synchronous, bypasses cooldown
# ---------------------------------------------------------------------------

class TestSendTest:
    def test_no_webhook_configured(self):
        notifier = _make_notifier(**{"teams.webhook_url": ""})
        success, detail = notifier.send_test()
        assert success is False
        assert "webhook" in detail.lower()

    def test_no_internet_access(self):
        notifier = _make_notifier(**{"teams.webhook_url": "https://example.invalid/hook"})
        with patch.object(notifier, "check_internet", return_value=False):
            success, detail = notifier.send_test()
        assert success is False
        assert "internet" in detail.lower()

    def test_success_on_http_200(self):
        notifier = _make_notifier(**{"teams.webhook_url": "https://example.invalid/hook"})
        resp = MagicMock()
        resp.getcode.return_value = 200
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch.object(notifier, "check_internet", return_value=True), \
             patch("src.controller.notify.urlopen", return_value=resp):
            success, detail = notifier.send_test(title="T", message="M")
        assert success is True
        assert "200" in detail

    def test_non_2xx_status_is_a_failure(self):
        notifier = _make_notifier(**{"teams.webhook_url": "https://example.invalid/hook"})
        resp = MagicMock()
        resp.getcode.return_value = 400
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch.object(notifier, "check_internet", return_value=True), \
             patch("src.controller.notify.urlopen", return_value=resp):
            success, detail = notifier.send_test()
        assert success is False
        assert "400" in detail

    def test_url_error_is_reported(self):
        notifier = _make_notifier(**{"teams.webhook_url": "https://example.invalid/hook"})
        with patch.object(notifier, "check_internet", return_value=True), \
             patch("src.controller.notify.urlopen", side_effect=URLError("refused")):
            success, detail = notifier.send_test()
        assert success is False
        assert "Network error" in detail

    def test_unexpected_exception_is_reported(self):
        notifier = _make_notifier(**{"teams.webhook_url": "https://example.invalid/hook"})
        with patch.object(notifier, "check_internet", return_value=True), \
             patch("src.controller.notify.urlopen", side_effect=RuntimeError("boom")):
            success, detail = notifier.send_test()
        assert success is False
        assert "Unexpected error" in detail


# ---------------------------------------------------------------------------
# _send -- the background-thread target send_alert dispatches to
# ---------------------------------------------------------------------------

class TestSendInternal:
    def test_skips_when_no_internet(self):
        notifier = _make_notifier()
        with patch.object(notifier, "check_internet", return_value=False), \
             patch("src.controller.notify.urlopen") as mock_urlopen:
            notifier._send("https://example.invalid/hook", "T", "M", "error")
        mock_urlopen.assert_not_called()

    def test_posts_adaptive_card_payload_on_success(self):
        notifier = _make_notifier()
        resp = MagicMock()
        resp.getcode.return_value = 200
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch.object(notifier, "check_internet", return_value=True), \
             patch("src.controller.notify.urlopen", return_value=resp) as mock_urlopen:
            notifier._send("https://example.invalid/hook", "Title", "Body", "warning")

        request = mock_urlopen.call_args[0][0]
        assert request.full_url == "https://example.invalid/hook"
        assert request.get_header("Content-type") == "application/json"

    def test_logs_warning_on_non_2xx(self):
        notifier = _make_notifier()
        notifier.logger = MagicMock()
        resp = MagicMock()
        resp.getcode.return_value = 500
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch.object(notifier, "check_internet", return_value=True), \
             patch("src.controller.notify.urlopen", return_value=resp):
            notifier._send("https://example.invalid/hook", "T", "M", "error")
        notifier.logger.warning.assert_called()

    def test_logs_error_on_unexpected_exception(self):
        notifier = _make_notifier()
        notifier.logger = MagicMock()
        with patch.object(notifier, "check_internet", return_value=True), \
             patch("src.controller.notify.urlopen", side_effect=RuntimeError("boom")):
            notifier._send("https://example.invalid/hook", "T", "M", "error")
        notifier.logger.error.assert_called()
