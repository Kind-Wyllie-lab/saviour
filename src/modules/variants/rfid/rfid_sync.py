"""
Store-and-forward sync of RFID reads from the local SQLite store
(``rfid_store.RfidStore``) to the long-term MariaDB.

Every few seconds the oldest unsynced rows are sent in one ``INSERT IGNORE``
transaction, then marked synced locally. If the module dies between the two,
the rows are sent again and the server's unique key on ``(store_id, seq)``
ignores them, so a retry can never duplicate. A server or network outage only
grows the local backlog; nothing is lost.

The connection settings are pushed by the controller (``set_rfid_db_config``)
and kept in a root-only JSON file rather than the module config, so the
password is never echoed back in ``get_config`` and survives the config
pruning that runs on every start.

The schema the server needs is ``docs/sql/rfid_schema.sql``.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rfid_store import RfidStore

_INSERT = (
    "INSERT IGNORE INTO rfid_read "
    "(store_id, seq, module_id, ts, ts_ns, unit, tag, type_code, kind, session) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)

_EPOCH = _dt.datetime(1970, 1, 1)


def ts_ns_to_mysql(ts_ns: int) -> str:
    """UTC DATETIME(6) literal, exact to the microsecond (no float rounding)."""
    t = _EPOCH + _dt.timedelta(microseconds=ts_ns // 1000)
    return t.strftime("%Y-%m-%d %H:%M:%S.%f")


@dataclass
class DbSettings:
    host: str = ""
    port: int = 3306
    database: str = ""
    username: str = ""
    password: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.host and self.database and self.username)

    @classmethod
    def from_dict(cls, d: dict | None) -> DbSettings:
        d = d or {}
        try:
            port = int(d.get("port") or 3306)
        except (TypeError, ValueError):
            port = 3306
        return cls(host=str(d.get("host") or "").strip(), port=port,
                   database=str(d.get("database") or "").strip(),
                   username=str(d.get("username") or "").strip(),
                   password=str(d.get("password") or ""))

    def key(self) -> tuple:
        return (self.host, self.port, self.database, self.username, self.password)


def load_settings(path: str) -> DbSettings:
    try:
        with open(path) as f:
            return DbSettings.from_dict(json.load(f))
    except FileNotFoundError:
        return DbSettings()


def save_settings(path: str, settings: DbSettings) -> None:
    """Write atomically, readable by the owner only (the module runs as root)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(asdict(settings), f)
    os.replace(tmp, path)


def pymysql_connect(s: DbSettings, timeout_s: float = 10):
    import pymysql  # lazy: only the rfid module needs it (apt python3-pymysql)
    return pymysql.connect(host=s.host, port=s.port, user=s.username,
                           password=s.password, database=s.database,
                           connect_timeout=timeout_s, read_timeout=30,
                           write_timeout=30, autocommit=False)


class RfidSync:
    """Runs :meth:`run` under ``shared.supervised.supervise``; everything it
    does per iteration is in :meth:`step` so tests can drive it directly."""

    BACKOFF_BASE_S = 5.0
    BACKOFF_MAX_S = 600.0
    PRUNE_EVERY_S = 3600.0

    def __init__(self, store: RfidStore, *, module_id: Callable[[], str],
                 get_settings: Callable[[], DbSettings],
                 interval_s: float = 5.0, batch_rows: int = 500,
                 retain_days: float = 30.0,
                 connect: Callable = pymysql_connect,
                 clock: Callable[[], float] = time.time,
                 logger: logging.Logger | None = None):
        self.store = store
        self.module_id = module_id
        self.get_settings = get_settings
        self.interval_s = interval_s
        self.batch_rows = batch_rows
        self.retain_days = retain_days
        self.connect = connect
        self.clock = clock
        self.logger = logger or logging.getLogger(__name__)

        self._conn = None
        self._conn_key: tuple | None = None
        self._failures = 0
        self._last_prune = 0.0
        self._wake = threading.Event()

        self.state = "not_configured"   # not_configured | ok | error
        self.last_ok_ts: float | None = None
        self.last_error: str | None = None
        self.synced_total = 0

    # ── Loop ───────────────────────────────────────────────────────────────

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            delay = self.step()
            if delay > 0:
                self._wake.wait(delay)
                self._wake.clear()
        self._close()

    def wake(self) -> None:
        """Run the next step now (new settings arrived)."""
        self._wake.set()

    def step(self) -> float:
        """One sync attempt. Returns how long to wait before the next."""
        settings = self.get_settings()
        self._maybe_prune(settings)

        if not settings.configured:
            self._close()
            self.state = "not_configured"
            self.last_error = None
            return self.interval_s

        if self._conn is not None and self._conn_key != settings.key():
            self._close()

        try:
            if self._conn is None:
                self._conn = self.connect(settings)
                self._conn_key = settings.key()
                self.logger.info(
                    f"RFID DB: connected to {settings.host}/{settings.database}")
            else:
                ping = getattr(self._conn, "ping", None)
                if ping is not None:
                    ping(reconnect=True)  # MariaDB drops idle links (wait_timeout)

            rows = self.store.fetch_unsynced(self.batch_rows)
            if rows:
                self._send(rows)
                self.store.mark_synced([r[0] for r in rows])
                self.synced_total += len(rows)
            if self.state != "ok" and self.last_error:
                self.logger.info("RFID DB: sync recovered")
            self.state = "ok"
            self.last_error = None
            self.last_ok_ts = self.clock()
            self._failures = 0
            # A full batch means more is waiting: go again straight away.
            return 0.0 if len(rows) == self.batch_rows else self.interval_s
        except Exception as e:
            self._close()
            self._failures += 1
            msg = f"{type(e).__name__}: {e}"
            if msg != self.last_error:
                self.logger.warning(f"RFID DB: sync failed: {msg}")
            self.state = "error"
            self.last_error = msg
            return min(self.BACKOFF_MAX_S,
                       self.BACKOFF_BASE_S * 2 ** (self._failures - 1))

    def _send(self, rows: list[tuple]) -> None:
        store_id = self.store.store_id
        module_id = self.module_id()
        params = [
            (store_id, seq, module_id, ts_ns_to_mysql(ts_ns), ts_ns, unit, tag,
             type_code, kind, session)
            for seq, ts_ns, unit, tag, type_code, kind, session in rows
        ]
        cur = self._conn.cursor()
        try:
            cur.executemany(_INSERT, params)
            self._conn.commit()
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def _maybe_prune(self, settings: DbSettings) -> None:
        now = self.clock()
        if now - self._last_prune < self.PRUNE_EVERY_S:
            return
        self._last_prune = now
        cutoff_ns = int((now - self.retain_days * 86400) * 1e9)
        try:
            # With no database configured the store is just a rolling local
            # log, so old rows go whether or not they were ever sent.
            n = self.store.prune(cutoff_ns, synced_only=settings.configured)
            if n:
                self.logger.info(f"RFID store: pruned {n} read(s) older than "
                                 f"{self.retain_days:g} days")
        except Exception as e:
            self.logger.warning(f"RFID store: prune failed: {e}")

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self._conn_key = None

    # ── Reporting ──────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Heartbeat payload (ModuleHealthSnapshot.rfid_db)."""
        stats = self.store.stats() if self.store.is_open else {}
        oldest = stats.get("oldest_unsynced_ts_ns")
        settings = self.get_settings()
        return {
            "state": self.state,
            "host": settings.host or None,
            "database": settings.database or None,
            "backlog": stats.get("backlog"),
            "oldest_unsynced_age_s": (
                round(self.clock() - oldest / 1e9, 1) if oldest else None),
            "last_ok_ts": self.last_ok_ts,
            "last_error": self.last_error,
            "synced_total": self.synced_total,
            "store_dropped": stats.get("dropped", 0),
        }

    def test_connection(self, settings: DbSettings) -> tuple[bool, str]:
        """Connect with ``settings`` and check the table is there and readable.
        Independent of the running sync's own connection."""
        if not settings.configured:
            return False, "Host, database and username must all be set"
        conn = None
        try:
            conn = self.connect(settings, timeout_s=5)
            cur = conn.cursor()
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM rfid_read WHERE 1 = 0")
            cur.fetchall()
            cur.close()
            return True, (f"Connected to {settings.host}/{settings.database} "
                          f"(server {version}); table rfid_read found")
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
