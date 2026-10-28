"""
Tests for src/controller/notify.py.

Notifier.__init__ has no real side effects (just config + a lock + a dedup
dict), so it's constructed directly rather than via __new__. Covers the
cooldown/dedup logic in send_alert, check_internet and the webhook POST
flow in send_test/_send with socket/urlopen mocked -- nothing here ever
makes a real network call.
"""

import json
from unittest.mock import MagicMock, mock_open, patch
from urllib.error import URLError

from src.controller.notify import Notifier


def _make_config(**overrides) -> MagicMock:
    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: overrides.get(key, default)
    return cfg


def _make_notifier(**config_overrides) -> Notifier:
    return Notifier(_make_config(**config_overrides))


def _card_body(mock_urlopen) -> list:
    """The Adaptive Card body (list of TextBlock/FactSet dicts) from the
    JSON that was POSTed via a mocked urlopen."""
    request = mock_urlopen.call_args[0][0]
    payload = json.loads(request.data.decode())
    return payload["attachments"][0]["content"]["body"]


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

    def test_first_alert_is_not_suppressed_when_monotonic_clock_is_small(self):
        # time.monotonic()'s reference point is undefined -- on a freshly
        # booted CI runner it can be well under the cooldown window. A
        # never-seen key must not be suppressed just because `now` is small.
        notifier = _make_notifier(**{
            "teams.webhook_url": "https://example.invalid/webhook",
            "teams.alert_cooldown_secs": 600,
        })
        with patch("src.controller.notify.threading.Thread") as mock_thread, \
             patch("src.controller.notify.time.monotonic", return_value=5.0):
            notifier.send_alert("key1", "title", "message")

        mock_thread.assert_called_once()

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
# _variant / _saviour_version / _footer -- card context helpers
# ---------------------------------------------------------------------------

class TestContextHelpers:
    def test_variant_reads_type_from_saviour_config(self):
        notifier = _make_notifier()
        with patch("builtins.open", mock_open(read_data='ROLE=controller\nTYPE="habitat"\n')):
            assert notifier._variant() == "habitat"

    def test_variant_strips_unquoted_value(self):
        notifier = _make_notifier()
        with patch("builtins.open", mock_open(read_data="ROLE=controller\nTYPE=apa\n")):
            assert notifier._variant() == "apa"

    def test_variant_unknown_when_config_missing(self):
        notifier = _make_notifier()
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert notifier._variant() == "unknown"

    def test_footer_joins_name_variant_version_timestamp(self):
        notifier = _make_notifier(**{"controller.name": "Habitat1"})
        with patch.object(notifier, "_variant", return_value="habitat"), \
             patch.object(notifier, "_saviour_version", return_value="v0.9-1-gabc"):
            assert notifier._footer("2026-08-28 14:30 UTC") == (
                "Habitat1 · habitat · v0.9-1-gabc · 2026-08-28 14:30 UTC"
            )


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

    def test_card_carries_controller_variant_version_factset(self):
        notifier = _make_notifier(**{
            "teams.webhook_url": "https://example.invalid/hook",
            "controller.name": "Habitat1",
        })
        resp = MagicMock()
        resp.getcode.return_value = 200
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch.object(notifier, "check_internet", return_value=True), \
             patch.object(notifier, "_variant", return_value="habitat"), \
             patch.object(notifier, "_saviour_version", return_value="v0.9-1-gabc"), \
             patch("src.controller.notify.urlopen", return_value=resp) as urlopen:
            notifier.send_test()

        facts = next(b for b in _card_body(urlopen) if b["type"] == "FactSet")["facts"]
        assert {f["title"]: f["value"] for f in facts} == {
            "Controller": "Habitat1", "Variant": "habitat", "Version": "v0.9-1-gabc",
        }

    def test_webhook_url_override_is_used_over_saved_config(self):
        # saved config has no webhook; the operator is testing a typed-but-unsaved URL
        notifier = _make_notifier(**{"teams.webhook_url": ""})
        resp = MagicMock()
        resp.getcode.return_value = 200
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch.object(notifier, "check_internet", return_value=True), \
             patch("src.controller.notify.urlopen", return_value=resp) as urlopen:
            success, _ = notifier.send_test(webhook_url="  https://typed.invalid/hook  ")
        assert success is True
        assert urlopen.call_args[0][0].full_url == "https://typed.invalid/hook"

    def test_blank_webhook_url_override_falls_back_to_saved_config(self):
        notifier = _make_notifier(**{"teams.webhook_url": ""})
        success, detail = notifier.send_test(webhook_url="   ")
        assert success is False
        assert "webhook" in detail.lower()

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

    def test_footer_carries_variant_and_version(self):
        notifier = _make_notifier(**{"controller.name": "Habitat1"})
        resp = MagicMock()
        resp.getcode.return_value = 200
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        with patch.object(notifier, "check_internet", return_value=True), \
             patch.object(notifier, "_variant", return_value="habitat"), \
             patch.object(notifier, "_saviour_version", return_value="v0.9-1-gabc"), \
             patch("src.controller.notify.urlopen", return_value=resp) as mock_urlopen:
            notifier._send("https://example.invalid/hook", "Title", "Body", "warning")

        footer = _card_body(mock_urlopen)[-1]["text"]
        assert footer.startswith("Habitat1 · habitat · v0.9-1-gabc · ")

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


# ---------------------------------------------------------------------------
# ntfy channel
# ---------------------------------------------------------------------------

def _ntfy_request(mock_urlopen):
    request = mock_urlopen.call_args[0][0]
    return request, json.loads(request.data.decode())


def _ok_response(status=200):
    resp = MagicMock()
    resp.getcode.return_value = status
    resp.__enter__.return_value = resp
    return resp


class TestSendAlertChannels:
    def test_ntfy_only_dispatches_the_ntfy_sender(self):
        notifier = _make_notifier(**{"ntfy.url": "https://ntfy.sh/t", "ntfy.token": "tk"})
        with patch("src.controller.notify.threading.Thread") as mock_thread:
            notifier.send_alert("k", "title", "message", severity="warning")
        mock_thread.assert_called_once()
        kwargs = mock_thread.call_args.kwargs
        assert kwargs["target"] == notifier._send_ntfy
        assert kwargs["args"] == ("https://ntfy.sh/t", "tk", "title", "message", "warning")

    def test_both_channels_each_get_a_thread_under_one_cooldown(self):
        notifier = _make_notifier(**{
            "teams.webhook_url": "https://example.invalid/webhook",
            "ntfy.url": "https://ntfy.sh/t",
            "teams.alert_cooldown_secs": 600,
        })
        with patch("src.controller.notify.threading.Thread") as mock_thread:
            notifier.send_alert("k", "title", "message")
            notifier.send_alert("k", "title", "message")  # within cooldown
        targets = [c.kwargs["target"] for c in mock_thread.call_args_list]
        assert targets == [notifier._send, notifier._send_ntfy]

    def test_blank_ntfy_url_is_ignored(self):
        notifier = _make_notifier(**{"ntfy.url": "   "})
        with patch("src.controller.notify.threading.Thread") as mock_thread:
            notifier.send_alert("k", "title", "message")
        mock_thread.assert_not_called()


class TestSplitNtfyUrl:
    def test_plain_topic(self):
        assert Notifier._split_ntfy_url("https://ntfy.sh/abc") == ("https://ntfy.sh", "abc")

    def test_trailing_slash_and_path_prefix(self):
        assert Notifier._split_ntfy_url("http://10.0.0.5:8080/ntfy/lab/") == (
            "http://10.0.0.5:8080/ntfy", "lab")

    def test_rejects_missing_topic_or_scheme(self):
        import pytest
        for bad in ("https://ntfy.sh", "https://ntfy.sh/", "ntfy.sh/abc", "", "ftp://x/y"):
            with pytest.raises(ValueError):
                Notifier._split_ntfy_url(bad)


class TestNtfyPublish:
    def test_posts_json_to_server_root_with_priority_tag_and_footer(self):
        notifier = _make_notifier(**{"controller.name": "rig-a"})
        with patch("src.controller.notify.urlopen", return_value=_ok_response()) as uo:
            status = notifier._ntfy_publish(
                "https://ntfy.sh/lab", "", "Module offline — cam µ", "cam gone", "error")
        assert status == 200
        req, body = _ntfy_request(uo)
        assert req.full_url == "https://ntfy.sh"
        assert req.get_header("Authorization") is None
        assert body["topic"] == "lab"
        assert body["title"] == "Module offline — cam µ"   # UTF-8 survives (JSON body)
        assert body["priority"] == 4
        assert body["tags"] == ["rotating_light"]
        assert body["message"].startswith("cam gone\n\n")
        assert "rig-a" in body["message"]

    def test_token_is_sent_as_bearer(self):
        notifier = _make_notifier()
        with patch("src.controller.notify.urlopen", return_value=_ok_response()) as uo:
            notifier._ntfy_publish("https://ntfy.sh/lab", " tk_abc ", "t", "m", "info")
        req, body = _ntfy_request(uo)
        assert req.get_header("Authorization") == "Bearer tk_abc"
        assert body["priority"] == 2

    def test_send_ntfy_never_checks_internet_and_logs_failures(self):
        notifier = _make_notifier()
        notifier.check_internet = MagicMock(return_value=False)
        notifier.logger = MagicMock()
        with patch("src.controller.notify.urlopen",
                   side_effect=URLError("refused")) as uo:
            notifier._send_ntfy("http://10.0.0.5/lab", "", "t", "m", "warning")
        uo.assert_called_once()                # attempted even with "no internet"
        notifier.check_internet.assert_not_called()
        notifier.logger.warning.assert_called_once()


class TestSendNtfyTest:
    def test_no_url_configured(self):
        ok, detail = _make_notifier().send_ntfy_test()
        assert not ok and "ntfy.url" in detail

    def test_typed_url_and_token_override_saved_values(self):
        notifier = _make_notifier(**{"ntfy.url": "https://ntfy.sh/saved", "ntfy.token": "old"})
        with patch("src.controller.notify.urlopen", return_value=_ok_response()) as uo:
            ok, _ = notifier.send_ntfy_test(url="https://ntfy.sh/typed", token="new")
        assert ok
        req, body = _ntfy_request(uo)
        assert body["topic"] == "typed"
        assert req.get_header("Authorization") == "Bearer new"

    def test_falls_back_to_saved_values(self):
        notifier = _make_notifier(**{"ntfy.url": "https://ntfy.sh/saved", "ntfy.token": "old"})
        with patch("src.controller.notify.urlopen", return_value=_ok_response()) as uo:
            ok, _ = notifier.send_ntfy_test(url="  ")
        assert ok
        req, body = _ntfy_request(uo)
        assert body["topic"] == "saved"
        assert req.get_header("Authorization") == "Bearer old"

    def test_auth_failure_hints_at_the_token(self):
        from urllib.error import HTTPError
        notifier = _make_notifier()
        err = HTTPError("https://ntfy.sh", 403, "Forbidden", {}, None)
        with patch("src.controller.notify.urlopen", side_effect=err):
            ok, detail = notifier.send_ntfy_test(url="https://ntfy.sh/x")
        assert not ok and "403" in detail and "token" in detail

    def test_bad_url_is_reported_without_a_request(self):
        notifier = _make_notifier()
        with patch("src.controller.notify.urlopen") as uo:
            ok, detail = notifier.send_ntfy_test(url="https://ntfy.sh/")
        assert not ok and "topic" in detail
        uo.assert_not_called()
