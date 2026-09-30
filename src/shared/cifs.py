"""
CIFS mount credentials without putting the password on a command line.

Every Samba mount used to pass ``-o username=...,password=...`` to
``sudo mount -t cifs``. sudo logs the full command line to the journal, so
the share password was written there on every mount -- every 5 minutes on
the controller from the NAS health probe (found in the 2026-09-30 desk
soak), and the journal rides the diagnostics bundle and the per-session
journal export. mount.cifs reads ``credentials=<file>`` instead; this
writes that file (0600) under /run (tmpfs, never on disk) and returns the
option to pass.
"""

import os
import tempfile

DEFAULT_DIR = "/run/saviour"


def cifs_auth_option(username: str, password: str, name: str = "cifs",
                     directory: str = DEFAULT_DIR) -> str:
    """The auth part of a cifs ``-o`` option string: ``guest`` when there is
    no username, else ``credentials=<path>`` for a freshly written 0600 file.
    ``name`` keeps concurrent mounts (probe vs export) on separate files."""
    if not username:
        return "guest"
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        path = os.path.join(directory, f"{name}-credentials")
    except OSError:
        # Not root / no /run (tests, dev machines): a private temp file.
        fd, path = tempfile.mkstemp(prefix=f"{name}-credentials-")
        os.close(fd)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"username={username}\npassword={password}\n")
    os.chmod(path, 0o600)
    return f"credentials={path}"


def redact_secrets(text: str) -> str:
    """Mask ``"...password...": "value"`` pairs in a JSON-ish log string."""
    import re
    return re.sub(r'("[^"]*password[^"]*"\s*:\s*)"[^"]*"', r'\1"***"', text,
                  flags=re.IGNORECASE)
