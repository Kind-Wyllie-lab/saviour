"""
SAVIOUR Controller — alert notifier (Microsoft Teams and/or ntfy)

`send_alert` is the one entry point: it applies the per-key cooldown, then
sends to every configured channel, each on its own background thread so a
slow or broken one can't hold up the other.

Teams — a Power Automate HTTP webhook (`teams.webhook_url`), setup below.

ntfy — `ntfy.url` is a topic URL, e.g. https://ntfy.sh/saviour-lab-x7q2 or a
self-hosted https://ntfy.example.org/saviour. Published as JSON to the
server root (UTF-8 safe, unlike ntfy's header form). `ntfy.token` (optional)
is sent as a bearer token for protected topics. Anyone who knows a public
ntfy.sh topic name can read it, so use a long random one (the Alerts tab
suggests one) or a token. ntfy skips the internet check: a self-hosted
server on the LAN must still work offline, so a failed request is just
logged.

The cooldown and the notify_* event switches stay under `teams.*` (where
they predate ntfy) but apply to both channels.

Teams setup:

Setup:
  1. In Power Automate, create a flow with trigger "When a HTTP request is received".
  2. Enable "Anyone can trigger this flow" (or restrict to your IP).
  3. Use "Use sample payload to generate schema" and paste:
       {"title": "", "message": "", "severity": "", "controller": "", "timestamp": ""}
  4. Add a "Post message in a chat or channel" action wired to those fields.
  5. Copy the HTTP POST URL into the controller config key: teams.webhook_url

Alerts are silently skipped if:
  - neither teams.webhook_url nor ntfy.url is set
  - The same alert key fired within the cooldown window (default 10 min)
  - (Teams only) the controller has no internet access (checked before each send)
"""

# To send a test alert from the command line:
#   python3 notify.py <webhook_url>
#   python3 notify.py <webhook_url> "Custom title" "Custom message"

import json
import logging
import socket
import threading
import time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

_INTERNET_CHECK_HOST = "8.8.8.8"
_INTERNET_CHECK_PORT = 53
_INTERNET_CHECK_TIMEOUT = 3.0

# severity -> (ntfy priority 1-5, ntfy tag; tags named like emoji render as one)
_NTFY_STYLE = {
    "error": (4, "rotating_light"),
    "warning": (3, "warning"),
    "info": (2, "information_source"),
}


class Notifier:
    def __init__(self, config):
        self.logger = logging.getLogger(__name__)
        self.config = config
        self._lock = threading.Lock()
        self._last_sent: dict[str, float] = {}  # key → epoch of last successful send

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def send_alert(self, key: str, title: str, message: str, severity: str = "error") -> None:
        """Send an alert to Teams and/or ntfy, each in a background thread.

        key      — deduplication key (e.g. "module_offline_camera_a3df").
                   The same key will not fire again within the cooldown window.
        title    — short heading.
        message  — body text.
        severity — "error" | "warning" | "info" (Teams card colour, ntfy priority).
        """
        webhook_url = self.config.get("teams.webhook_url", "")
        ntfy_url = (self.config.get("ntfy.url", "") or "").strip()
        if not webhook_url and not ntfy_url:
            return

        cooldown = self.config.get("teams.alert_cooldown_secs", 600)
        now = time.monotonic()
        with self._lock:
            last = self._last_sent.get(key)
            if last is not None and now - last < cooldown:
                return
            self._last_sent[key] = now

        if webhook_url:
            threading.Thread(
                target=self._send,
                args=(webhook_url, title, message, severity),
                daemon=True,
                name=f"teams-alert-{key}",
            ).start()
        if ntfy_url:
            threading.Thread(
                target=self._send_ntfy,
                args=(ntfy_url, self.config.get("ntfy.token", ""), title, message, severity),
                daemon=True,
                name=f"ntfy-alert-{key}",
            ).start()

    def send_test(self, title: str = "SAVIOUR Test Alert", message: str = "This is a test message from the SAVIOUR controller.", webhook_url: str | None = None) -> tuple:
        """Send a one-off test alert synchronously, bypassing cooldown.

        webhook_url — test this URL instead of the saved teams.webhook_url, so
        an operator can verify a webhook they've typed into the Alerts tab
        before saving it. Falls back to the saved config value when None/empty.

        Returns (success: bool, detail: str).
        """
        webhook_url = (webhook_url or "").strip() or self.config.get("teams.webhook_url", "")
        if not webhook_url:
            return False, "No webhook URL configured (teams.webhook_url is empty)"
        if not self.check_internet():
            return False, "No internet access — controller cannot reach 8.8.8.8:53"
        colour = "default"
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.2",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": title,
                                "weight": "Bolder",
                                "size": "Medium",
                                "color": colour,
                                "wrap": True,
                            },
                            {"type": "TextBlock", "text": message, "wrap": True},
                            {
                                "type": "FactSet",
                                "facts": [
                                    {"title": "Controller", "value": self._controller_name()},
                                    {"title": "Variant", "value": self._variant()},
                                    {"title": "Version", "value": self._saviour_version()},
                                ],
                            },
                            {
                                "type": "TextBlock",
                                "text": timestamp,
                                "size": "Small",
                                "isSubtle": True,
                            },
                        ],
                    },
                }
            ],
        }
        try:
            body = json.dumps(payload).encode()
            req = Request(
                webhook_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                status = resp.getcode()
                if status in (200, 202):
                    return True, f"Message delivered (HTTP {status})"
                return False, f"Webhook returned HTTP {status}"
        except URLError as e:
            return False, f"Network error: {e}"
        except Exception as e:
            return False, f"Unexpected error: {e}"

    def send_ntfy_test(self, url: str | None = None, token: str | None = None) -> tuple:
        """Send a one-off ntfy test synchronously, bypassing cooldown.

        url / token — test what the operator has typed into the Alerts tab
        (saved or not); each falls back to the saved config value when
        None/empty. Returns (success: bool, detail: str)."""
        url = (url or "").strip() or (self.config.get("ntfy.url", "") or "").strip()
        if token is None:
            token = self.config.get("ntfy.token", "")
        if not url:
            return False, "No ntfy URL configured (ntfy.url is empty)"
        try:
            status = self._ntfy_publish(
                url, token, "SAVIOUR Test Alert",
                "This is a test message from the SAVIOUR controller.", "info")
        except ValueError as e:
            return False, str(e)
        except HTTPError as e:
            hint = " (wrong or missing token?)" if e.code in (401, 403) else ""
            return False, f"ntfy returned HTTP {e.code}{hint}"
        except URLError as e:
            return False, f"Network error: {e.reason}"
        except Exception as e:
            return False, f"Unexpected error: {e}"
        if status == 200:
            return True, "Message delivered (HTTP 200)"
        return False, f"ntfy returned HTTP {status}"

    def check_internet(self) -> bool:
        """Return True if the controller can reach the public internet."""
        try:
            with socket.create_connection(
                (_INTERNET_CHECK_HOST, _INTERNET_CHECK_PORT),
                timeout=_INTERNET_CHECK_TIMEOUT,
            ):
                return True
        except OSError:
            return False

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _controller_name(self) -> str:
        name = self.config.get("controller.name", "")
        if name:
            return name
        try:
            return socket.gethostname()
        except Exception:
            return "unknown"

    def _saviour_version(self) -> str:
        """Running version from src/__version__.py (pre-commit-hook-written,
        travels inside ZIP deploys) — same source web.py's version read uses."""
        try:
            from src.__version__ import __version__
            return __version__ or "unknown"
        except Exception:
            return "unknown"

    def _variant(self) -> str:
        """Controller variant/type from the shell-style /etc/saviour/config
        (TYPE=habitat, etc.). "unknown" off-device or if unreadable."""
        try:
            with open("/etc/saviour/config") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TYPE="):
                        return line.split("=", 1)[1].strip().strip('"\'') or "unknown"
        except Exception:
            pass
        return "unknown"

    def _footer(self, timestamp: str) -> str:
        """Compact one-line context footer for alert cards."""
        return " · ".join([
            self._controller_name(), self._variant(),
            self._saviour_version(), timestamp,
        ])

    @staticmethod
    def _split_ntfy_url(url: str) -> tuple[str, str]:
        """https://ntfy.sh/topic -> ("https://ntfy.sh", "topic"). The server
        may sit under a path prefix; the topic is always the last segment."""
        parts = urlsplit((url or "").strip())
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("ntfy URL must look like https://ntfy.sh/my-topic")
        prefix, _, topic = parts.path.rstrip("/").rpartition("/")
        if not topic:
            raise ValueError("ntfy URL needs a topic, e.g. https://ntfy.sh/my-topic")
        return f"{parts.scheme}://{parts.netloc}{prefix}", topic

    def _ntfy_publish(self, url: str, token: str, title: str, message: str,
                      severity: str) -> int:
        """POST one message; returns the HTTP status. Raises ValueError on a
        malformed URL and urllib errors on failure."""
        server, topic = self._split_ntfy_url(url)
        priority, tag = _NTFY_STYLE.get(severity, _NTFY_STYLE["info"])
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        payload = {
            "topic": topic,
            "title": title,
            "message": f"{message}\n\n{self._footer(timestamp)}",
            "priority": priority,
            "tags": [tag],
        }
        headers = {"Content-Type": "application/json"}
        if (token or "").strip():
            headers["Authorization"] = f"Bearer {token.strip()}"
        req = Request(server, data=json.dumps(payload).encode(), headers=headers,
                      method="POST")
        with urlopen(req, timeout=10) as resp:
            return resp.getcode()

    def _send_ntfy(self, url: str, token: str, title: str, message: str,
                   severity: str) -> None:
        try:
            status = self._ntfy_publish(url, token, title, message, severity)
            if status == 200:
                self.logger.info(f"ntfy alert sent: '{title}'")
            else:
                self.logger.warning(f"ntfy alert '{title}' returned HTTP {status}")
        except (ValueError, URLError) as e:
            self.logger.warning(f"ntfy alert '{title}' failed: {e}")
        except Exception as e:
            self.logger.error(f"ntfy alert '{title}' unexpected error: {e}")

    def _send(self, webhook_url: str, title: str, message: str, severity: str) -> None:
        if not self.check_internet():
            self.logger.warning(f"Teams alert '{title}' skipped — no internet access")
            return

        colour = {"error": "attention", "warning": "warning"}.get(severity, "default")
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

        # Teams Workflows webhook requires an Adaptive Card envelope
        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.2",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": title,
                                "weight": "Bolder",
                                "size": "Medium",
                                "color": colour,
                                "wrap": True,
                            },
                            {
                                "type": "TextBlock",
                                "text": message,
                                "wrap": True,
                            },
                            {
                                "type": "TextBlock",
                                "text": self._footer(timestamp),
                                "size": "Small",
                                "isSubtle": True,
                                "wrap": True,
                            },
                        ],
                    },
                }
            ],
        }

        try:
            body = json.dumps(payload).encode()
            req = Request(
                webhook_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:
                status = resp.getcode()
                if status == 200 or status == 202:
                    self.logger.info(f"Teams alert sent: '{title}'")
                else:
                    self.logger.warning(f"Teams alert '{title}' returned HTTP {status}")
        except URLError as e:
            self.logger.warning(f"Teams alert '{title}' failed: {e}")
        except Exception as e:
            self.logger.error(f"Teams alert '{title}' unexpected error: {e}")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python3 notify.py <webhook_url> [title] [message]")
        sys.exit(1)

    url   = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) > 2 else "SAVIOUR Test Alert"
    msg   = sys.argv[3] if len(sys.argv) > 3 else "This is a test message from the SAVIOUR controller."

    class _FakeConfig:
        def get(self, key, default=None):
            if key == "teams.webhook_url":     return url
            if key == "teams.alert_cooldown_secs": return 0
            return default

    n = Notifier(_FakeConfig())
    print(f"Internet: {n.check_internet()}")
    n._send(url, title, msg, "info")
