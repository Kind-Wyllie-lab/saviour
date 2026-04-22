"""
SAVIOUR Controller — Teams Notifier

Sends alerts to a Microsoft Teams channel via a Power Automate HTTP webhook.

Setup:
  1. In Power Automate, create a flow with trigger "When a HTTP request is received".
  2. Enable "Anyone can trigger this flow" (or restrict to your IP).
  3. Use "Use sample payload to generate schema" and paste:
       {"title": "", "message": "", "severity": "", "controller": "", "timestamp": ""}
  4. Add a "Post message in a chat or channel" action wired to those fields.
  5. Copy the HTTP POST URL into the controller config key: teams.webhook_url

Alerts are silently skipped if:
  - teams.webhook_url is empty
  - The controller has no internet access (checked before each send)
  - The same alert key fired within the cooldown window (default 10 min)
"""

# To send a test alert from the command line:
#   python3 notify.py <webhook_url>
#   python3 notify.py <webhook_url> "Custom title" "Custom message"

import json
import logging
import socket
import threading
import time
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError


_INTERNET_CHECK_HOST = "8.8.8.8"
_INTERNET_CHECK_PORT = 53
_INTERNET_CHECK_TIMEOUT = 3.0


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
        """Fire a Teams alert in a background thread.

        key      — deduplication key (e.g. "module_offline_camera_a3df").
                   The same key will not fire again within the cooldown window.
        title    — short heading for the Teams message.
        message  — body text.
        severity — "error" | "warning" | "info" (informational only).
        """
        webhook_url = self.config.get("teams.webhook_url", "")
        if not webhook_url:
            return

        cooldown = self.config.get("teams.alert_cooldown_secs", 600)
        now = time.monotonic()
        with self._lock:
            last = self._last_sent.get(key, 0)
            if now - last < cooldown:
                return
            self._last_sent[key] = now

        threading.Thread(
            target=self._send,
            args=(webhook_url, title, message, severity),
            daemon=True,
            name=f"teams-alert-{key}",
        ).start()

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

    def _send(self, webhook_url: str, title: str, message: str, severity: str) -> None:
        if not self.check_internet():
            self.logger.warning(f"Teams alert '{title}' skipped — no internet access")
            return

        colour = {"error": "attention", "warning": "warning"}.get(severity, "default")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

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
                                "text": f"{self._controller_name()} · {timestamp}",
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
