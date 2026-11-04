"""
Always-on local store for RFID reads (SQLite, WAL).

Every read the bus delivers lands here whether or not a recording session is
running; ``rfid_sync.RfidSync`` forwards rows to the long-term MariaDB and
marks them synced. Writes go through a queue to a single writer thread, so the
bus reader thread never waits on disk.

Rows are identified on the server by ``(store_id, seq)``:

* ``seq`` is an AUTOINCREMENT rowid -- never reused, even after every row has
  been pruned (a plain rowid can restart at 1, which the server's unique key
  would then silently ignore as a duplicate).
* ``store_id`` is random, generated when the database file is created, so a
  re-imaged module (or a deleted database) starts a new sequence instead of
  colliding with its old one.
"""

from __future__ import annotations

import logging
import os
import queue
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass

KIND_READ = "read"
# A read whose transponder ID is all zero bytes. The Pi 4 logger stored
# ~half its reads like this on the busiest units; what the decoder means by
# it is not yet known (possibly "tag left the field"). Stored, but kept out of
# the live stream / presence tracking.
KIND_ZERO = "zero"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reads (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ns      INTEGER NOT NULL,
    unit       INTEGER NOT NULL,
    tag        TEXT    NOT NULL,
    type_code  INTEGER,
    kind       TEXT    NOT NULL,
    session    TEXT,
    synced     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS reads_unsynced ON reads(seq) WHERE synced = 0;
CREATE INDEX IF NOT EXISTS reads_ts ON reads(ts_ns);
"""

_INSERT = ("INSERT INTO reads (ts_ns, unit, tag, type_code, kind, session) "
           "VALUES (?, ?, ?, ?, ?, ?)")


@dataclass(frozen=True)
class StoredRead:
    ts_ns: int
    unit: int
    tag: str
    type_code: int | None
    kind: str
    session: str | None = None

    def as_row(self) -> tuple:
        return (self.ts_ns, self.unit, self.tag, self.type_code, self.kind,
                self.session)


class RfidStore:
    """SQLite-backed read log. Thread-safe: one connection behind a lock,
    with inserts batched by a writer thread (:meth:`run_writer`)."""

    def __init__(self, path: str, *, max_queue: int = 200_000,
                 batch_rows: int = 500, commit_interval_s: float = 0.5,
                 logger: logging.Logger | None = None):
        self.path = path
        self.batch_rows = batch_rows
        self.commit_interval_s = commit_interval_s
        self.logger = logger or logging.getLogger(__name__)
        self._queue: queue.Queue[StoredRead] = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self.store_id: str = ""
        self.dropped = 0          # reads lost because the queue was full
        self.written = 0          # reads committed since open()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def open(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        # In WAL mode NORMAL never corrupts on power loss; at worst the last
        # few hundred ms of commits are lost, the same window as the queue.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        row = conn.execute("SELECT value FROM meta WHERE key='store_id'").fetchone()
        if row:
            self.store_id = row[0]
        else:
            self.store_id = secrets.token_hex(8)
            conn.execute("INSERT INTO meta (key, value) VALUES ('store_id', ?)",
                         (self.store_id,))
        conn.commit()
        self._conn = conn

    def close(self) -> None:
        self.flush()
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    # ── Writing ────────────────────────────────────────────────────────────

    def put(self, read: StoredRead) -> None:
        """Queue a read for the writer thread. Never blocks."""
        try:
            self._queue.put_nowait(read)
        except queue.Full:
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1000 == 0:
                self.logger.error(
                    f"RFID store queue full -- {self.dropped} read(s) dropped "
                    "(writer thread stalled?)")

    def run_writer(self, stop_event: threading.Event) -> None:
        """Writer loop: drain the queue into SQLite in batches until stopped.
        Meant to run under ``shared.supervised.supervise``."""
        while not stop_event.is_set():
            self._drain(block_s=self.commit_interval_s)
        self._drain(block_s=0)

    def flush(self) -> None:
        """Write everything queued so far (stop, tests)."""
        while not self._queue.empty():
            self._drain(block_s=0)

    def _drain(self, block_s: float) -> None:
        batch: list[StoredRead] = []
        try:
            batch.append(self._queue.get(timeout=block_s) if block_s
                         else self._queue.get_nowait())
        except queue.Empty:
            return
        deadline = time.monotonic() + self.commit_interval_s
        while len(batch) < self.batch_rows and time.monotonic() < deadline:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                if not block_s:
                    break
                time.sleep(0.02)
        self._insert(batch)

    def _insert(self, batch: list[StoredRead]) -> None:
        with self._lock:
            if self._conn is None:
                raise RuntimeError("RFID store is not open")
            self._conn.executemany(_INSERT, [r.as_row() for r in batch])
            self._conn.commit()
        self.written += len(batch)

    # ── Sync support ───────────────────────────────────────────────────────

    def fetch_unsynced(self, limit: int) -> list[tuple]:
        """Oldest unsynced rows: (seq, ts_ns, unit, tag, type_code, kind, session)."""
        with self._lock:
            return self._conn.execute(
                "SELECT seq, ts_ns, unit, tag, type_code, kind, session "
                "FROM reads WHERE synced = 0 ORDER BY seq LIMIT ?", (limit,)
            ).fetchall()

    def mark_synced(self, seqs: list[int]) -> None:
        if not seqs:
            return
        with self._lock:
            for i in range(0, len(seqs), 500):
                chunk = seqs[i:i + 500]
                self._conn.execute(
                    f"UPDATE reads SET synced = 1 WHERE seq IN "
                    f"({','.join('?' * len(chunk))})", chunk)
            self._conn.commit()

    def prune(self, older_than_ns: int, *, synced_only: bool = True) -> int:
        """Delete rows older than ``older_than_ns`` -- only synced ones unless
        ``synced_only`` is False (used when no database is configured, so the
        store is just a rolling local log). Deletes in chunks so the lock is
        never held for long. Returns the number of rows deleted."""
        cond = "ts_ns < ?" + (" AND synced = 1" if synced_only else "")
        total = 0
        while True:
            with self._lock:
                cur = self._conn.execute(
                    f"DELETE FROM reads WHERE seq IN "
                    f"(SELECT seq FROM reads WHERE {cond} LIMIT 5000)",
                    (older_than_ns,))
                self._conn.commit()
            total += cur.rowcount
            if cur.rowcount < 5000:
                return total

    def stats(self) -> dict:
        """Unsynced backlog and the timestamp of its oldest read."""
        with self._lock:
            if self._conn is None:
                return {"backlog": None, "oldest_unsynced_ts_ns": None}
            backlog = self._conn.execute(
                "SELECT COUNT(*) FROM reads WHERE synced = 0").fetchone()[0]
            oldest = self._conn.execute(
                "SELECT ts_ns FROM reads WHERE synced = 0 ORDER BY seq LIMIT 1"
            ).fetchone()
        return {
            "backlog": backlog,
            "oldest_unsynced_ts_ns": oldest[0] if oldest else None,
            "queued": self._queue.qsize(),
            "dropped": self.dropped,
        }
