"""src/shared/timestamps.py (docs/CHANGELOG.md 2026-11-20)."""

from datetime import UTC, datetime

from src.shared import timestamps as ts


def test_stamp_and_iso_are_utc_with_z():
    dt = datetime(2026, 7, 1, 13, 5, 9, 250000, tzinfo=UTC)
    assert ts.stamp(dt) == "20260701-130509Z"
    assert ts.iso(dt) == "2026-07-01T13:05:09Z"
    assert ts.iso(dt, ms=True) == "2026-07-01T13:05:09.250Z"
    assert ts.clock(dt) == "13:05:09Z"


def test_naive_input_is_taken_as_utc():
    assert ts.stamp(datetime(2026, 7, 1, 13, 0, 0)) == "20260701-130000Z"


def test_parse_new_forms():
    assert ts.parse("20260701-130509Z") == datetime(2026, 7, 1, 13, 5, 9, tzinfo=UTC)
    assert ts.parse("2026-07-01T13:05:09Z") == datetime(2026, 7, 1, 13, 5, 9, tzinfo=UTC)
    assert ts.parse("2026-07-01T14:05:09+01:00") == datetime(2026, 7, 1, 13, 5, 9, tzinfo=UTC)


def test_parse_legacy_is_london_local():
    # Summer (BST, UTC+1): an hour ahead of UTC.
    assert ts.parse("20260701-140509") == datetime(2026, 7, 1, 13, 5, 9, tzinfo=UTC)
    assert ts.parse("2026-07-01T14:05:09") == datetime(2026, 7, 1, 13, 5, 9, tzinfo=UTC)
    # Winter (GMT): same as UTC.
    assert ts.parse("20261120-143000") == datetime(2026, 11, 20, 14, 30, tzinfo=UTC)


def test_parse_rejects_junk():
    for bad in (None, "", "yesterday", "2026-13-01T00:00:00Z", 123):
        assert ts.parse(bad) is None


def test_utc_date_crosses_midnight_for_legacy_bst():
    # 00:30 BST on 2 July is 23:30 UTC on 1 July.
    assert ts.utc_date("20260702-003000") == "20260701"
    assert ts.utc_date("20260702-003000Z") == "20260702"
