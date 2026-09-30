"""
phc2sys per-sample output, read from a file instead of the journal.

saviour-config runs phc2sys with ``-m -q`` and
``StandardOutput=append:/run/linuxptp/phc2sys.log`` (tmpfs): at ``-R 8`` its
servo lines were ~90% of a module's journal, which let a capped persistent
journal hold only ~2 days. ptp.py (both sides) tails this file for its
offset/frequency telemetry instead of spawning ``journalctl`` every second,
and keeps it bounded -- systemd opens it O_APPEND, so truncating in place is
safe while phc2sys keeps writing.

Devices whose PTP units predate this still log to the journal; callers fall
back to ``journalctl`` when the file doesn't exist.
"""

import os

PHC2SYS_LOG = "/run/linuxptp/phc2sys.log"
MAX_BYTES = 4 * 1024 * 1024   # ~2 h at 8 lines/s; tmpfs, so keep it small
TAIL_BYTES = 8192


def tail_lines(path: str, lines: int, max_bytes: int = MAX_BYTES,
               tail_bytes: int = TAIL_BYTES) -> str | None:
    """Last ``lines`` lines of ``path`` as one string, or None if the file
    doesn't exist. Truncates the file once it exceeds ``max_bytes``."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_bytes))
            data = f.read().decode("utf-8", errors="replace")
        if size > max_bytes:
            os.truncate(path, 0)
    except OSError:
        return None
    # Drop a partial first line from mid-file seeks.
    chunk = data.splitlines()
    if size > tail_bytes and chunk:
        chunk = chunk[1:]
    return "\n".join(chunk[-lines:])
