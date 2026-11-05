"""Company-details and ticker staging (stage_sec.DETAILS, stage_sec.TICKERS), on
raw landings written straight through write_raw in the loaders' shapes."""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from pharos.runs import write_raw
from pharos.stage import StageError
from pharos.stage_sec import DETAILS, TICKERS, Base, connect, stage_table

R1, R2, R3 = "20261105T073000Z", "20261106T073000Z", "20261107T073000Z"
STAMP = datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


def ticker(t="AAA", cik=1, name="A CORP", exchange="NYSE"):
    return {"cik": cik, "name": name, "ticker": t, "exchange": exchange}


def detail(cik=1, name="A CORP", sic="1000", tickers=("AAA",), source=None):
    return {"cik": cik, "entity_type": "operating", "sic": sic, "name": name,
            "tickers": list(tickers), "addresses": "{}", "former_names": "[]", "filing_files": "[]",
            "source_file": source or f"CIK{cik:010d}.json"}


def land(dataset, run_id, rows):
    write_raw(pd.DataFrame(rows), "sec", dataset, run_id, last_modified=STAMP)


def staged(ds, runs, base=None):
    con = connect()
    return con, stage_table(con, ds, runs, base)


def rows(con, table, cols):
    return con.execute(f"SELECT {cols} FROM {table} ORDER BY ALL").fetchall()


def test_ticker_dates_are_the_run_date(root):
    land("tickers", R1, [ticker()])
    con, _ = staged(TICKERS, [R1])
    assert rows(con, "tickers", "ticker, obs_date, available_date, vintage, run_id") == [
        ("AAA", date(2026, 11, 5), date(2026, 11, 5), date(2026, 11, 5), R1)]


def test_unchanged_ticker_keeps_its_first_run_and_dates(root):
    land("tickers", R1, [ticker()])
    land("tickers", R2, [ticker()])
    con, _ = staged(TICKERS, [R1, R2])
    assert rows(con, "tickers", "obs_date, run_id") == [(date(2026, 11, 5), R1)]


def test_ticker_moving_to_another_cik_takes_the_newest(root):
    land("tickers", R1, [ticker(cik=1)])
    land("tickers", R2, [ticker(cik=2)])
    con, _ = staged(TICKERS, [R1, R2])
    assert rows(con, "tickers", "cik, obs_date, run_id") == [(2, date(2026, 11, 6), R2)]


def test_ticker_dropped_from_the_file_is_kept(root):
    # newest value per key: a key missing from later landings stays (D84, D132)
    land("tickers", R1, [ticker("AAA"), ticker("BBB", cik=2)])
    land("tickers", R2, [ticker("AAA")])
    con, _ = staged(TICKERS, [R1, R2])
    assert rows(con, "tickers", "ticker, run_id") == [("AAA", R1), ("BBB", R1)]


def test_ticker_twice_in_one_landing_stops(root):
    land("tickers", R1, [ticker(cik=1), ticker(cik=2)])
    with pytest.raises(StageError, match="different values inside one landing"):
        staged(TICKERS, [R1])


def test_detail_change_in_a_list_is_a_change(root):
    land("submission_details", R1, [detail(tickers=("AAA",))])
    land("submission_details", R2, [detail(tickers=("AAA", "AAB"))])
    con, _ = staged(DETAILS, [R1, R2])
    assert rows(con, "submission_details", "tickers, run_id") == [(["AAA", "AAB"], R2)]


def test_details_merge_into_base_equals_reading_every_landing(root):
    for run, sic in zip((R1, R2, R3), ("1000", "2000", "2000")):
        land("submission_details", run, [detail(sic=sic), detail(cik=2)])
    con, _ = staged(DETAILS, [R1, R2])
    folder = root / "serving" / R2
    folder.mkdir(parents=True)
    con.execute(f"COPY submission_details TO '{(folder / 'submission_details.parquet').as_posix()}' (FORMAT parquet)")
    full, _ = staged(DETAILS, [R1, R2, R3])
    merged, result = staged(DETAILS, [R1, R2, R3], base=Base(R2, folder))
    assert result.runs_read == (R3,)
    everything = "SELECT * FROM submission_details ORDER BY cik"
    assert merged.execute(everything).fetchall() == full.execute(everything).fetchall()
    assert rows(full, "submission_details", "cik, sic, run_id") == [(1, "2000", R2), (2, "1000", R1)]
