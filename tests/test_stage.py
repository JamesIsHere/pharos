"""stage() turns raw snapshots into staging tables. These tests pin its controls:
identical reruns collapse, a source correction survives as a conflict, an
incomplete run is never staged, and a join that fans out or leaves a NULL
vintage stops the run instead of writing."""

from datetime import date

import duckdb
import pandas as pd
import pytest

from pharos import config
from pharos.runs import write_raw
from pharos.stage import StageError, stage

# R1 and R1B are the same New York day, so a full pull in each shares a vintage (D20)
R1, R1B, R2, R3 = "20261007T140000Z", "20261007T150000Z", "20261008T140000Z", "20261009T140000Z"


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(config, "sources", lambda: {
        "backfill_start": date(2005, 1, 1), "fred": {"series": ["GDP"], "expected_lag_days": {"GDP": 35}},
        "yahoo": {"expected_lag_days": 0}})
    monkeypatch.setattr(config, "watchlist", lambda: [
        {"ticker": "NVDA", "yahoo_symbol": "NVDA", "active_from": None, "active_to": None}])
    return tmp_path


def prices(closes, pull_start=date(2005, 1, 1)) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "ticker": ["NVDA"] * n, "yahoo_symbol": ["NVDA"] * n,
        "obs_date": [date(2026, 10, 1 + i) for i in range(n)],
        "open": closes, "high": closes, "low": closes, "close": closes, "adj_close": closes,
        "volume": [100] * n, "dividends": [0.0] * n, "stock_splits": [0.0] * n,
        "pull_start": [pull_start] * n, "pull_end": [date(2026, 10, 9)] * n})


def fred_obs(value="100.5") -> pd.DataFrame:
    return pd.DataFrame({"fred_id": ["GDP"], "obs_date": [date(2026, 4, 1)],
                         "realtime_start": [date(2026, 7, 30)], "realtime_end": [date(9999, 12, 31)],
                         "value": [value]})


def fred_series(windows=((date(1991, 12, 4), date(9999, 12, 31), "Billions of Dollars"),)) -> pd.DataFrame:
    return pd.DataFrame({
        "fred_id": ["GDP"] * len(windows), "title": ["Gross Domestic Product"] * len(windows),
        "frequency_short": ["Q"] * len(windows), "seasonal_adjustment_short": ["SAAR"] * len(windows),
        "observation_start": ["1947-01-01"] * len(windows),
        "realtime_start": [w[0] for w in windows], "realtime_end": [w[1] for w in windows],
        "units": [w[2] for w in windows]})


def land(run_id, px=None, obs=None, series=None, skip=()):
    for (source, dataset), frame in {("yf", "prices"): px if px is not None else prices([10.0, 11.0]),
                                     ("fred", "observations"): obs if obs is not None else fred_obs(),
                                     ("fred", "series"): series if series is not None else fred_series()}.items():
        if dataset not in skip:
            write_raw(frame, source, dataset, run_id)


def staged(path, sql="SELECT * FROM obs ORDER BY ALL"):
    con = duckdb.connect()
    con.execute(f"CREATE VIEW obs AS SELECT * FROM read_parquet('{(path / 'observations.parquet').as_posix()}')")
    return con.execute(sql).fetchall()


def test_identical_rerun_collapses_to_first_run(root):
    land(R1)
    land(R1B)
    rows = staged(stage(R1B), "SELECT series_id, count(*), min(run_id) FROM obs GROUP BY 1 ORDER BY 1")
    assert rows == [("fred:GDP", 1, R1), ("yf:close:NVDA", 2, R1)]


def test_source_correction_survives_as_conflict(root):
    land(R1)
    land(R1B, px=prices([10.0, 11.5]))
    rows = staged(stage(R1B), "SELECT obs_date, value, run_id FROM obs WHERE series_id = 'yf:close:NVDA' ORDER BY ALL")
    assert rows == [(date(2026, 10, 1), 10.0, R1), (date(2026, 10, 2), 11.0, R1), (date(2026, 10, 2), 11.5, R1B)]


def test_full_pull_on_a_new_day_starts_a_new_vintage(root):
    land(R1)
    land(R2)
    rows = staged(stage(R2), "SELECT vintage, count(*) FROM obs WHERE series_id = 'yf:close:NVDA' GROUP BY 1 ORDER BY 1")
    assert rows == [(date(2026, 10, 7), 2), (date(2026, 10, 8), 2)]


def test_incremental_run_inherits_vintage(root):
    land(R1)
    land(R2, px=prices([10.0, 11.0, 12.0], pull_start=date(2026, 9, 25)))
    rows = staged(stage(R2), "SELECT DISTINCT vintage FROM obs WHERE series_id = 'yf:close:NVDA'")
    assert rows == [(date(2026, 10, 7),)]


def test_incremental_without_full_pull_stops(root):
    land(R1, px=prices([10.0], pull_start=date(2026, 9, 25)))
    with pytest.raises(StageError, match="no full-history pull"):
        stage(R1)
    assert not (root / "staging").exists()


def test_incomplete_run_is_not_staged_or_read(root):
    land(R1)
    land(R2, px=prices([99.0, 99.0]), skip=("series",))
    with pytest.raises(StageError, match="not complete"):
        stage(R2)
    land(R3)
    rows = staged(stage(R3), "SELECT count(*) FILTER (WHERE value = 99.0), list(DISTINCT run_id ORDER BY run_id) FROM obs")
    assert rows == [(0, [R1, R3])]  # R2's prices were never read


def test_overlapping_units_windows_stop(root):
    land(R1, series=fred_series(((date(1991, 12, 4), date(9999, 12, 31), "Billions of Dollars"),
                                 (date(2020, 1, 1), date(9999, 12, 31), "WRONG"))))
    with pytest.raises(StageError, match="fanned out"):
        stage(R1)


def test_withdrawn_value_kept_as_null(root):
    land(R1, obs=fred_obs("."))
    assert staged(stage(R1), "SELECT value FROM obs WHERE series_id = 'fred:GDP'") == [(None,)]
