"""write_raw() is the only writer to raw/. These tests pin its four promises:
it writes and stamps lineage, it refuses to overwrite, an empty download is
recorded rather than silent, and the control total counts the file itself."""

import re

import duckdb
import pandas as pd
import pytest

from pharos import runs
from pharos.runs import RawExistsError, new_run_id, write_raw


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


def sample(n: int = 3) -> pd.DataFrame:
    return pd.DataFrame({"ticker": ["AAPL"] * n, "close": [float(i) for i in range(n)]})


def loads(root) -> list[tuple]:
    return duckdb.sql(
        f"""SELECT dataset, path IS NOT NULL, rows_downloaded, rows_written
            FROM read_parquet('{(root / "health" / "loads").as_posix()}/*.parquet')
            ORDER BY dataset"""
    ).fetchall()


def test_run_id_format():
    assert re.fullmatch(r"\d{8}T\d{6}Z", new_run_id())


def test_writes_file_with_lineage(root):
    path = write_raw(sample(), "yf", "prices", "20260101T000000Z")
    assert path == root / "raw" / "yf" / "prices" / "20260101T000000Z.parquet"
    rows = duckdb.sql(
        f"SELECT ticker, close, run_id FROM read_parquet('{path.as_posix()}') ORDER BY close"
    ).fetchall()
    assert rows == [("AAPL", 0.0, "20260101T000000Z"),
                    ("AAPL", 1.0, "20260101T000000Z"),
                    ("AAPL", 2.0, "20260101T000000Z")]
    assert not list(root.rglob("*.partial"))


def test_refuses_to_overwrite(root):
    write_raw(sample(), "yf", "prices", "20260101T000000Z")
    with pytest.raises(RawExistsError):
        write_raw(sample(5), "yf", "prices", "20260101T000000Z")
    assert loads(root) == [("prices", True, 3, 3)]


def test_empty_download_is_recorded_not_silent(root):
    assert write_raw(sample(0), "yf", "events", "20260101T000000Z") is None
    assert not (root / "raw").exists()
    assert loads(root) == [("events", False, 0, 0)]


def test_control_total_counts_rows_in_the_file(root):
    write_raw(sample(7), "fred", "observations", "20260101T000000Z")
    assert loads(root) == [("observations", True, 7, 7)]


def test_control_total_exposes_a_lost_row(root, monkeypatch):
    # Simulate a file that came back short: the record must show the gap, not hide it.
    monkeypatch.setattr(runs, "_count_rows", lambda con, path: 6)
    write_raw(sample(7), "fred", "observations", "20260101T000000Z")
    assert loads(root) == [("observations", True, 7, 6)]
