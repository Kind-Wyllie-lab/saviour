"""
SQLite persistence layer for the RFID bus monitor.

Two tables:
    units               – known decoders with address, firmware, serial number
    transponder_reads   – every tag read, timestamped from the first arriving byte
"""

import sqlite3
import threading
import time
from typing import Dict, List, Optional


class RFIDDatabase:
    """
    Thread-safe SQLite wrapper.  Each thread gets its own connection via
    threading.local so there are no locking collisions between the reader
    thread and the Flask request threads.
    """

    def __init__(self, path: str = 'rfid_data.db') -> None:
        self.path = path
        self._local = threading.local()
        self._init_schema()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        if not getattr(self._local, 'conn', None):
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self) -> None:
        self._conn().executescript("""
            CREATE TABLE IF NOT EXISTS units (
                address     INTEGER PRIMARY KEY,
                firmware    TEXT    NOT NULL DEFAULT '',
                serial_num  TEXT    NOT NULL DEFAULT '',
                first_seen  REAL    NOT NULL DEFAULT 0,
                last_seen   REAL    NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS transponder_reads (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                unit_address     INTEGER NOT NULL,
                transponder_id   TEXT    NOT NULL,
                transponder_type TEXT    NOT NULL DEFAULT '',
                packet_ts        REAL    NOT NULL,
                received_at      REAL    NOT NULL,
                uploaded         INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_reads_unit
                ON transponder_reads (unit_address);
            CREATE INDEX IF NOT EXISTS idx_reads_ts
                ON transponder_reads (packet_ts DESC);
            CREATE INDEX IF NOT EXISTS idx_reads_uploaded
                ON transponder_reads (uploaded);
        """)
        self._conn().commit()

    # ── Units ─────────────────────────────────────────────────────────────────

    def upsert_unit(self, address: int,
                    firmware: str = '',
                    serial_num: str = '',
                    last_seen: Optional[float] = None) -> None:
        """Insert or update a unit record.  Empty strings do not overwrite
        existing non-empty values."""
        now = last_seen if last_seen is not None else time.time()
        conn = self._conn()
        conn.execute("""
            INSERT INTO units (address, firmware, serial_num, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(address) DO UPDATE SET
                firmware   = CASE WHEN excluded.firmware   != '' THEN excluded.firmware   ELSE firmware   END,
                serial_num = CASE WHEN excluded.serial_num != '' THEN excluded.serial_num ELSE serial_num END,
                last_seen  = excluded.last_seen
        """, (address, firmware, serial_num, now, now))
        conn.commit()

    def get_units(self) -> List[Dict]:
        rows = self._conn().execute(
            'SELECT * FROM units ORDER BY address').fetchall()
        return [dict(r) for r in rows]

    # ── Transponder reads ─────────────────────────────────────────────────────

    def add_read(self, unit_address: int,
                 transponder_id: str,
                 transponder_type: str,
                 packet_ts: float) -> int:
        """
        Persist one transponder read.
        *packet_ts* is the timestamp of the first arriving byte of the packet.
        Returns the new row id.
        """
        conn = self._conn()
        cur = conn.execute("""
            INSERT INTO transponder_reads
                (unit_address, transponder_id, transponder_type, packet_ts, received_at)
            VALUES (?, ?, ?, ?, ?)
        """, (unit_address, transponder_id, transponder_type,
              packet_ts, time.time()))
        conn.commit()
        return cur.lastrowid

    def get_reads(self, limit: int = 200,
                  pending_only: bool = False) -> List[Dict]:
        """Return reads ordered newest-first."""
        where = 'WHERE uploaded = 0' if pending_only else ''
        rows = self._conn().execute(
            f'SELECT * FROM transponder_reads {where} '
            f'ORDER BY packet_ts DESC LIMIT ?', (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_uploaded(self, read_id: int) -> None:
        conn = self._conn()
        conn.execute(
            'UPDATE transponder_reads SET uploaded = 1 WHERE id = ?', (read_id,))
        conn.commit()

    def mark_many_uploaded(self, ids: List[int]) -> None:
        conn = self._conn()
        conn.executemany(
            'UPDATE transponder_reads SET uploaded = 1 WHERE id = ?',
            [(i,) for i in ids])
        conn.commit()

    def pending_count(self) -> int:
        row = self._conn().execute(
            'SELECT COUNT(*) FROM transponder_reads WHERE uploaded = 0').fetchone()
        return row[0] if row else 0
