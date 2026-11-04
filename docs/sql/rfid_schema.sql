-- SAVIOUR RFID long-term database (MariaDB 10.5+).
--
-- Written to by the `rfid` module's sync thread
-- (src/modules/variants/rfid/rfid_sync.py), which batches reads from the
-- module's local SQLite store with INSERT IGNORE. The module never runs DDL:
-- an admin runs this file once on the server, e.g.
--
--     sudo mariadb < docs/sql/rfid_schema.sql
--
-- then sets the two passwords below (ALTER USER ... IDENTIFIED BY ...).
-- Change the database name or the rig subnet ('192.168.1.%') to suit; the
-- subnet should be the network the modules reach the server on, the same one
-- they export to Samba over.

CREATE DATABASE IF NOT EXISTS habitat_rfid
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE habitat_rfid;

CREATE TABLE IF NOT EXISTS rfid_read (
    id         BIGINT UNSIGNED   NOT NULL AUTO_INCREMENT PRIMARY KEY,
    -- (store_id, seq) identifies a read. store_id is random per module
    -- database file, so a re-imaged module starts a fresh sequence instead
    -- of colliding with its old one; seq is never reused within a store.
    -- The unique key is what makes a re-sent batch a no-op.
    store_id   CHAR(16)          NOT NULL,
    seq        BIGINT UNSIGNED   NOT NULL,
    module_id  VARCHAR(64)       NOT NULL,
    -- UTC, from the module's PTP-disciplined clock. ts_ns is the same
    -- instant in epoch nanoseconds, for joining against SAVIOUR session
    -- files (whose timestamps are epoch ns).
    ts         DATETIME(6)       NOT NULL,
    ts_ns      BIGINT            NOT NULL,
    -- Reader unit's RS485 bus address (one unit per antenna).
    unit       SMALLINT UNSIGNED NOT NULL,
    -- Transponder ID, upper-case hex, type byte excluded.
    tag        VARCHAR(32)       NOT NULL,
    -- Raw transponder type byte (0x20 = Trovan Unique) or NULL if unknown.
    type_code  TINYINT UNSIGNED  NULL,
    -- 'read' = a transponder; 'zero' = an all-zero ID whose meaning is not
    -- yet known (possibly "tag left the field"). Filter on kind = 'read'
    -- for animal detections.
    kind       VARCHAR(8)        NOT NULL DEFAULT 'read',
    -- SAVIOUR session name when the module was recording, else NULL.
    session    VARCHAR(128)      NULL,
    UNIQUE KEY uq_store_seq (store_id, seq),
    KEY ix_tag_ts  (tag, ts),
    KEY ix_unit_ts (unit, ts),
    KEY ix_ts      (ts)
) ENGINE = InnoDB;

-- Writer account for the modules: INSERT only (INSERT IGNORE needs nothing
-- more) plus SELECT so the controller's "Test connection" can check the table.
CREATE USER IF NOT EXISTS 'saviour_rfid'@'192.168.1.%' IDENTIFIED BY 'CHANGE-ME';
GRANT SELECT, INSERT ON habitat_rfid.rfid_read TO 'saviour_rfid'@'192.168.1.%';

-- Read-only account for analysis.
CREATE USER IF NOT EXISTS 'rfid_reader'@'%' IDENTIFIED BY 'CHANGE-ME-TOO';
GRANT SELECT ON habitat_rfid.* TO 'rfid_reader'@'%';

FLUSH PRIVILEGES;
