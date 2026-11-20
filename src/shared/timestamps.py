"""
UTC timestamps for everything SAVIOUR writes (docs/CHANGELOG.md 2026-11-20).

Every stamp is UTC and says so with a trailing ``Z``:

- compact, used for session times and file-ish stamps: ``20261120-143000Z``
- ISO, used in JSON: ``2026-11-20T14:30:00Z``

Reading accepts both, and also the legacy forms written before this change
without a zone (``20261120-143000``, ``2026-11-20T14:30:00``), which were the
controller's local time; the fleet ran on Europe/London, so that zone is used
to convert them (a BST-era legacy stamp is an hour ahead of UTC).

Local-time intentions (habitat recording windows, the DMX day/night schedule)
are not timestamps and don't go through here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, tzinfo

COMPACT = "%Y%m%d-%H%M%S"
ISO = "%Y-%m-%dT%H:%M:%S"
# The zone legacy (zone-less) stamps were written in.
LEGACY_ZONE = "Europe/London"

_COMPACT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(Z?)$")
_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def _legacy_zone() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(LEGACY_ZONE)
    except Exception:                                       # noqa: BLE001 -- no tzdata: system local
        return datetime.now().astimezone().tzinfo or UTC


def utc_now() -> datetime:
    return datetime.now(UTC)


def stamp(dt: datetime | None = None) -> str:
    """``20261120-143000Z`` for ``dt`` (default now). A naive ``dt`` is
    taken as UTC."""
    dt = _as_utc(dt or utc_now())
    return dt.strftime(COMPACT) + "Z"


def iso(dt: datetime | None = None, *, ms: bool = False) -> str:
    """``2026-11-20T14:30:00Z`` (``ms``: with milliseconds)."""
    dt = _as_utc(dt or utc_now())
    if ms:
        return dt.strftime(ISO) + f".{dt.microsecond // 1000:03d}Z"
    return dt.strftime(ISO) + "Z"


def clock(dt: datetime | None = None) -> str:
    """``14:30:00Z``: time of day, for log lines."""
    return _as_utc(dt or utc_now()).strftime("%H:%M:%SZ")


def from_epoch(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, UTC)


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def parse(value: str | None) -> datetime | None:
    """A stamp in any form above as an aware UTC datetime, or None.
    Zone-less (legacy) stamps are read as Europe/London local time."""
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    m = _COMPACT_RE.match(s)
    if m:
        dt = datetime(*(int(x) for x in m.groups()[:6]))
        return dt.replace(tzinfo=UTC) if m[7] else _legacy(dt)
    m = _ISO_RE.match(s)
    if m:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
        except ValueError:
            return None
        return dt.astimezone(UTC) if dt.tzinfo else _legacy(dt)
    return None


def _legacy(naive: datetime) -> datetime:
    return naive.replace(tzinfo=_legacy_zone()).astimezone(UTC)


def utc_date(value: str | None) -> str | None:
    """``YYYYMMDD`` (UTC) of a stamp, e.g. a session's date folder."""
    dt = parse(value)
    return dt.strftime("%Y%m%d") if dt else None
