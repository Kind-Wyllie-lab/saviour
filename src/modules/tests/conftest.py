"""Allow camera-adjacent module tests to be collected on non-Pi runners.

picamera2 depends on the Pi's system libcamera bindings and cannot be pip-
installed on generic CI hardware (e.g. GitHub Actions' ubuntu-latest). Tests
in this directory that exercise pure-Python logic in camera_base.py /
camera_module.py / apa_camera_module.py / loom_camera_module.py still need
those modules to *import* cleanly, so stub picamera2 out when the real
package isn't present. On real Pi hardware this is a no-op — the genuine
package is used.
"""

import sys
from unittest.mock import MagicMock

try:
    import picamera2  # noqa: F401
except ImportError:
    for name in (
        "picamera2",
        "picamera2.encoders",
        "picamera2.outputs",
        "picamera2.devices",
        "picamera2.devices.hailo",
    ):
        sys.modules[name] = MagicMock()
