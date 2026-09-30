"""Tests for src/shared/ptp_log.py (phc2sys output out of the journal)."""

import os

from src.shared.ptp_log import tail_lines

LINE = ("phc2sys[359.907]: CLOCK_REALTIME phc offset       -20 s2 freq  "
        "+10469 delay     37")


def test_missing_file_is_none(tmp_path):
    assert tail_lines(str(tmp_path / "nope.log"), 5) is None


def test_returns_last_lines(tmp_path):
    p = tmp_path / "phc2sys.log"
    p.write_text("\n".join(f"{LINE} #{i}" for i in range(20)) + "\n")
    out = tail_lines(str(p), 3).splitlines()
    assert out == [f"{LINE} #{i}" for i in (17, 18, 19)]


def test_drops_partial_first_line_after_a_mid_file_seek(tmp_path):
    p = tmp_path / "phc2sys.log"
    p.write_text("\n".join(f"{LINE} #{i}" for i in range(500)) + "\n")
    out = tail_lines(str(p), 1000, tail_bytes=300).splitlines()
    assert all(line.startswith("phc2sys[") for line in out)
    assert out[-1].endswith("#499")


def test_truncates_once_over_the_cap(tmp_path):
    p = tmp_path / "phc2sys.log"
    p.write_text("\n".join(f"{LINE} #{i}" for i in range(100)) + "\n")
    out = tail_lines(str(p), 2, max_bytes=1000)
    assert out.splitlines()[-1].endswith("#99")  # still read before truncating
    assert os.path.getsize(p) == 0
