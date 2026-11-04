"""
Tests for the RFID local store (rfid_store.py), the MariaDB store-and-forward
sync (rfid_sync.py) and their wiring into RFIDModule.

The sync is exercised against an in-memory fake DB-API connection that
enforces the server's UNIQUE (store_id, seq) the way INSERT IGNORE does, so
"re-send after a crash never duplicates" is tested rather than assumed.
"""

import json
import os
import threading
import time
from unittest.mock import MagicMock

import pytest

from src.modules.variants.rfid.rfid_module import Ping, RFIDModule
from src.modules.variants.rfid.rfid_store import (
    KIND_READ,
    KIND_ZERO,
    RfidStore,
    StoredRead,
)
from src.modules.variants.rfid.rfid_sync import (
    DbSettings,
    RfidSync,
    load_settings,
    save_settings,
    ts_ns_to_mysql,
)

SETTINGS = DbSettings(host="db", database="habitat_rfid", username="rfid",
                      password="pw")


def _read(i: int, ts_s: float = 1_700_000_000.0, kind=KIND_READ) -> StoredRead:
    return StoredRead(ts_ns=int((ts_s + i) * 1e9), unit=i % 72 + 1,
                      tag=f"{i:010X}", type_code=0x20, kind=kind)


@pytest.fixture
def store(tmp_path):
    s = RfidStore(str(tmp_path / "rfid" / "reads.db"))
    s.open()
    yield s
    s.close()


# ── Fake MariaDB ──────────────────────────────────────────────────────────

class FakeServer:
    def __init__(self):
        self.rows: dict[tuple, tuple] = {}   # (store_id, seq) -> row
        self.fail_next_commit = False
        self.connects = 0
        self.refuse = False


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._result = []

    def executemany(self, sql, params):
        assert sql.startswith("INSERT IGNORE INTO rfid_read")
        self.conn.pending.extend(params)

    def execute(self, sql, params=None):
        if "VERSION()" in sql:
            self._result = [("10.11.6-MariaDB",)]
        elif "FROM rfid_read" in sql:
            self._result = [(0,)]

    def fetchone(self):
        return self._result[0]

    def fetchall(self):
        return self._result

    def close(self):
        pass


class FakeConn:
    def __init__(self, server: FakeServer):
        self.server = server
        self.pending: list[tuple] = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        if self.server.fail_next_commit:
            self.server.fail_next_commit = False
            self.pending.clear()
            raise ConnectionError("lost connection during commit")
        for p in self.pending:
            self.server.rows.setdefault((p[0], p[1]), p)   # INSERT IGNORE
        self.pending.clear()

    def rollback(self):
        self.pending.clear()

    def ping(self, reconnect=True):
        pass

    def close(self):
        self.closed = True


def _connector(server: FakeServer):
    def connect(settings, timeout_s=10):
        server.connects += 1
        if server.refuse:
            raise ConnectionRefusedError("Connection refused")
        return FakeConn(server)
    return connect


def _sync(store, server, settings=SETTINGS, clock=None, **kw) -> RfidSync:
    holder = {"s": settings}
    sync = RfidSync(store, module_id=lambda: "rfid_ab12",
                    get_settings=lambda: holder["s"],
                    connect=_connector(server), clock=clock or time.time, **kw)
    sync._settings_holder = holder
    return sync


# ── Store ─────────────────────────────────────────────────────────────────

class TestStore:
    def test_put_flush_and_fetch_in_order(self, store):
        for i in range(5):
            store.put(_read(i))
        store.flush()
        rows = store.fetch_unsynced(10)
        assert [r[3] for r in rows] == [f"{i:010X}" for i in range(5)]
        assert rows[0][4] == 0x20 and rows[0][5] == KIND_READ

    def test_store_id_survives_reopen(self, tmp_path):
        path = str(tmp_path / "reads.db")
        a = RfidStore(path)
        a.open()
        sid = a.store_id
        a.close()
        b = RfidStore(path)
        b.open()
        assert b.store_id == sid and len(sid) == 16
        b.close()

    def test_new_database_gets_new_store_id(self, tmp_path):
        a = RfidStore(str(tmp_path / "a.db"))
        b = RfidStore(str(tmp_path / "b.db"))
        a.open()
        b.open()
        assert a.store_id != b.store_id
        a.close()
        b.close()

    def test_seq_never_reused_after_everything_pruned(self, store):
        # A plain rowid restarts at 1 once the table is empty; the server's
        # UNIQUE (store_id, seq) would then silently drop the new reads.
        for i in range(3):
            store.put(_read(i))
        store.flush()
        seqs = [r[0] for r in store.fetch_unsynced(10)]
        store.mark_synced(seqs)
        assert store.prune(older_than_ns=2**62) == 3
        store.put(_read(9))
        store.flush()
        assert store.fetch_unsynced(10)[0][0] > max(seqs)

    def test_prune_keeps_unsynced_rows(self, store):
        for i in range(4):
            store.put(_read(i))
        store.flush()
        rows = store.fetch_unsynced(10)
        store.mark_synced([rows[0][0], rows[1][0]])
        assert store.prune(older_than_ns=2**62) == 2
        assert len(store.fetch_unsynced(10)) == 2
        assert store.prune(older_than_ns=2**62, synced_only=False) == 2

    def test_stats_backlog_and_oldest(self, store):
        assert store.stats()["backlog"] == 0
        assert store.stats()["oldest_unsynced_ts_ns"] is None
        for i in range(3):
            store.put(_read(i))
        store.flush()
        st = store.stats()
        assert st["backlog"] == 3
        assert st["oldest_unsynced_ts_ns"] == _read(0).ts_ns

    def test_full_queue_counts_drops_without_blocking(self, tmp_path):
        s = RfidStore(str(tmp_path / "r.db"), max_queue=2)
        s.open()
        for i in range(5):
            s.put(_read(i))
        assert s.dropped == 3
        s.close()

    def test_writer_thread_commits(self, store):
        stop = threading.Event()
        t = threading.Thread(target=store.run_writer, args=(stop,))
        t.start()
        for i in range(50):
            store.put(_read(i))
        deadline = time.time() + 5
        while store.stats()["backlog"] < 50 and time.time() < deadline:
            time.sleep(0.05)
        stop.set()
        t.join(5)
        assert store.stats()["backlog"] == 50


# ── Sync ──────────────────────────────────────────────────────────────────

class TestSync:
    def _fill(self, store, n):
        for i in range(n):
            store.put(_read(i))
        store.flush()

    def test_sends_and_marks_synced(self, store):
        server = FakeServer()
        self._fill(store, 3)
        sync = _sync(store, server)
        assert sync.step() == sync.interval_s
        assert len(server.rows) == 3
        assert store.stats()["backlog"] == 0
        assert sync.state == "ok" and sync.synced_total == 3
        row = server.rows[(store.store_id, 1)]
        assert row[2] == "rfid_ab12"
        assert row[3] == ts_ns_to_mysql(_read(0).ts_ns)
        assert row[6] == f"{0:010X}" and row[7] == 0x20 and row[8] == KIND_READ

    def test_full_batch_goes_again_immediately(self, store):
        server = FakeServer()
        self._fill(store, 5)
        sync = _sync(store, server, batch_rows=2)
        assert sync.step() == 0.0
        assert sync.step() == 0.0
        assert sync.step() == sync.interval_s
        assert len(server.rows) == 5

    def test_crash_between_insert_and_mark_never_duplicates(self, store):
        server = FakeServer()
        self._fill(store, 3)
        sync = _sync(store, server)
        # Server commits, then the local mark is "lost" (module died).
        store.mark_synced = MagicMock()
        sync.step()
        assert len(server.rows) == 3
        del store.mark_synced              # back to the real method
        sync.step()                        # re-sends the same three
        assert len(server.rows) == 3
        assert store.stats()["backlog"] == 0

    def test_failed_commit_keeps_rows_and_backs_off(self, store):
        server = FakeServer()
        self._fill(store, 3)
        sync = _sync(store, server)
        server.fail_next_commit = True
        delay = sync.step()
        assert delay == RfidSync.BACKOFF_BASE_S
        assert sync.state == "error" and "lost connection" in sync.last_error
        assert store.stats()["backlog"] == 3 and not server.rows
        sync.step()
        assert len(server.rows) == 3 and sync.state == "ok"
        assert sync.last_error is None

    def test_backoff_doubles_and_caps(self, store):
        server = FakeServer()
        server.refuse = True
        sync = _sync(store, server)
        delays = [sync.step() for _ in range(10)]
        assert delays[:4] == [5.0, 10.0, 20.0, 40.0]
        assert delays[-1] == RfidSync.BACKOFF_MAX_S

    def test_not_configured_does_nothing(self, store):
        server = FakeServer()
        self._fill(store, 2)
        sync = _sync(store, server, settings=DbSettings(),
                     clock=lambda: _read(0).ts_ns / 1e9)   # reads are fresh
        sync.step()
        assert server.connects == 0 and sync.state == "not_configured"
        assert store.stats()["backlog"] == 2

    def test_settings_change_reconnects(self, store):
        server = FakeServer()
        sync = _sync(store, server)
        sync.step()
        assert server.connects == 1
        sync.step()
        assert server.connects == 1
        sync._settings_holder["s"] = DbSettings(host="db2", database="x",
                                                username="u")
        sync.step()
        assert server.connects == 2

    def test_prune_policy_depends_on_configuration(self, store):
        now = 1_700_000_000.0 + 100 * 86400
        server = FakeServer()
        self._fill(store, 3)              # all ~100 days old, unsynced
        sync = _sync(store, server, settings=DbSettings(), clock=lambda: now)
        sync.step()                        # no DB: rolling local log
        assert store.stats()["backlog"] == 0

        self._fill(store, 3)
        server.refuse = True
        sync2 = _sync(store, server, clock=lambda: now)
        sync2.step()                       # DB configured but down: keep
        assert store.stats()["backlog"] == 3

    def test_status_reports_backlog_age(self, store):
        server = FakeServer()
        server.refuse = True
        self._fill(store, 2)
        t0 = _read(0).ts_ns / 1e9
        sync = _sync(store, server, clock=lambda: t0 + 400)
        sync.step()
        st = sync.status()
        assert st["state"] == "error" and st["backlog"] == 2
        assert st["oldest_unsynced_age_s"] == pytest.approx(400, abs=0.2)
        assert st["host"] == "db"

    def test_test_connection(self, store):
        server = FakeServer()
        sync = _sync(store, server)
        ok, msg = sync.test_connection(SETTINGS)
        assert ok and "rfid_read" in msg
        server.refuse = True
        ok, msg = sync.test_connection(SETTINGS)
        assert not ok and "refused" in msg
        ok, msg = sync.test_connection(DbSettings())
        assert not ok


class TestHelpers:
    def test_ts_ns_to_mysql_exact(self):
        assert ts_ns_to_mysql(1_700_000_000_123_456_789) == "2023-11-14 22:13:20.123456"

    def test_settings_file_round_trip_and_mode(self, tmp_path):
        path = str(tmp_path / "sub" / "db_settings.json")
        assert load_settings(path) == DbSettings()
        save_settings(path, SETTINGS)
        assert load_settings(path) == SETTINGS
        if os.name == "posix":
            assert os.stat(path).st_mode & 0o777 == 0o600
        assert json.load(open(path))["password"] == "pw"

    def test_from_dict_tolerates_bad_port(self):
        assert DbSettings.from_dict({"port": "x"}).port == 3306
        assert DbSettings.from_dict({"port": "3307"}).port == 3307


# ── Module wiring ─────────────────────────────────────────────────────────

def _module(tmp_path, store=None):
    m = RFIDModule.__new__(RFIDModule)
    m.logger = MagicMock()
    m.config = MagicMock()
    path = str(tmp_path / "rfid" / "reads.db")
    m.config.get.side_effect = lambda k, d=None: path if k == "rfid.store.path" else d
    m._store = store
    m._store_error = None
    m._sync = None
    m._db_settings = DbSettings()
    m._db_stop = threading.Event()
    m._db_threads = []
    m._zero_reads = 0
    m.is_recording = False
    m._presence = None
    m._csv_handle = None
    m._record_ping = MagicMock()
    m.module_id = "rfid_ab12"
    return m


class TestModuleWiring:
    def test_normal_read_is_stored_and_recorded(self, tmp_path, store):
        m = _module(tmp_path, store)
        m._on_transponder_read(5, bytes.fromhex("000807dc1f"), "Trovan Unique",
                               1.5, 0x20)
        store.flush()
        row = store.fetch_unsynced(1)[0]
        assert row[2:6] == (5, "000807DC1F", 0x20, KIND_READ)
        ping = m._record_ping.call_args[0][0]
        assert isinstance(ping, Ping) and ping.type_code == 0x20

    def test_zero_read_is_stored_but_not_a_tag(self, tmp_path, store):
        m = _module(tmp_path, store)
        m._write_raw_row = MagicMock()
        m._on_transponder_read(45, bytes(5), "Trovan Unique", 2.0, 0x20)
        store.flush()
        assert store.fetch_unsynced(1)[0][5] == KIND_ZERO
        m._record_ping.assert_not_called()      # no live ping / ZMQ / presence
        m._write_raw_row.assert_called_once()   # still in the session CSV
        assert m._zero_reads == 1

    def test_session_name_stored_while_recording(self, tmp_path, store):
        m = _module(tmp_path, store)
        m.is_recording = True
        m.recording = MagicMock(current_session_name="habitat_week1")
        m._on_transponder_read(1, b"\x01\x02\x03\x04\x05", "x", 1.0, None)
        store.flush()
        assert store.fetch_unsynced(1)[0][6] == "habitat_week1"

    def test_set_rfid_db_config_saves_file_not_config(self, tmp_path):
        m = _module(tmp_path)
        r = m.set_rfid_db_config(host="10.0.0.9", port="3306",
                                 database="habitat_rfid", username="rfid",
                                 password="s3cret")
        assert r == {"result": "success"}
        assert m._db_settings.configured and m._db_settings.port == 3306
        m.config.set.assert_not_called()
        saved = load_settings(str(tmp_path / "rfid" / "db_settings.json"))
        assert saved.password == "s3cret"

    def test_start_store_loads_saved_settings_and_stop_closes(self, tmp_path):
        m = _module(tmp_path)
        save_settings(str(tmp_path / "rfid" / "db_settings.json"), SETTINGS)
        m._start_store()
        try:
            assert m._store is not None and m._store.is_open
            # (the module imports rfid_sync under its bare name, so compare
            # values rather than the dataclass instances)
            assert m._db_settings.key() == SETTINGS.key()
            assert m._sync.interval_s == 5.0
            assert {t.name for t in m._db_threads} == {
                "rfid.store_writer", "rfid.db_sync"}
        finally:
            m._stop_store()
        assert m._store is None and m._db_threads == []

    def test_store_open_failure_is_reported_not_raised(self, tmp_path):
        m = _module(tmp_path)
        blocker = tmp_path / "rfid"
        blocker.write_text("not a directory")
        m._start_store()
        assert m._store is None
        st = m.rfid_db_status()
        assert st["state"] == "store_error" and st["last_error"]
        r = m.rfid_db_test()
        assert r["ok"] is False and "not running" in r["message"]
