"""The Yahoo loader without the network: fake yfinance tables stand in for the
real thing, so these tests run anywhere (including CI) and test our rules, not
Yahoo's uptime."""

from datetime import date, datetime

import duckdb
import pandas as pd
import pytest

from pharos import config
from pharos.loaders import yahoo
from pharos.loaders.yahoo import EXPECTED_COLUMNS, NEW_YORK, SchemaDriftError, normalize


def fake_bars(days=("2024-06-07", "2024-06-10"), tz="America/New_York") -> pd.DataFrame:
    idx = pd.DatetimeIndex(pd.to_datetime(list(days)), name="Date")
    if tz:
        idx = idx.tz_localize(tz)
    n = len(days)
    return pd.DataFrame({
        "Open": [1.0] * n, "High": [2.0] * n, "Low": [0.5] * n,
        "Close": [120.888, 121.79][:n], "Adj Close": [120.5, 121.4][:n],
        "Volume": [100] * n, "Dividends": [0.0] * n, "Stock Splits": [0.0, 10.0][:n],
    }, index=idx)


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


# --- normalize: the shape gate ---

def test_correct_table_passes_with_values_unchanged():
    out = normalize(fake_bars(), "NVDA", "NVDA")
    assert list(out.columns) == ["ticker", "yahoo_symbol", "obs_date", "open", "high", "low",
                                 "close", "adj_close", "volume", "dividends", "stock_splits"]
    assert list(out["obs_date"]) == [date(2024, 6, 7), date(2024, 6, 10)]
    assert list(out["close"]) == [120.888, 121.79]
    assert list(out["stock_splits"]) == [0.0, 10.0]


def test_missing_column_is_drift():
    with pytest.raises(SchemaDriftError, match="columns"):
        normalize(fake_bars().drop(columns=["Adj Close"]), "NVDA", "NVDA")


def test_extra_column_is_drift():
    bars = fake_bars()
    bars["Capital Gains"] = 0.0
    with pytest.raises(SchemaDriftError, match="columns"):
        normalize(bars, "NVDA", "NVDA")


@pytest.mark.parametrize("tz", ["UTC", None])
def test_wrong_or_missing_timezone_is_drift(tz):
    with pytest.raises(SchemaDriftError, match="timezone"):
        normalize(fake_bars(tz=tz), "NVDA", "NVDA")


def test_empty_response_contributes_nothing():
    assert normalize(pd.DataFrame(), "ATVI", "ATVI").empty


# --- load_prices: the run ---

def test_load_prices_windows_and_empty_ticker(root, monkeypatch):
    monkeypatch.setattr(config, "sources", lambda: {
        "backfill_start": date(2005, 1, 1),
        "yahoo": {"auto_adjust": False, "overlap_days": 10, "required_series": ["close"]}})
    monkeypatch.setattr(config, "watchlist", lambda: [
        {"ticker": "NVDA", "yahoo_symbol": "NVDA", "active_from": None, "active_to": None},
        {"ticker": "ATVI", "yahoo_symbol": "ATVI", "active_from": None, "active_to": date(2023, 10, 12)},
        {"ticker": "CART", "yahoo_symbol": "CART", "active_from": date(2023, 9, 19), "active_to": None},
    ])
    calls = []

    def fake_fetch(symbol, start, end, auto_adjust):
        calls.append((symbol, start, end, auto_adjust))
        return pd.DataFrame() if symbol == "ATVI" else fake_bars()

    monkeypatch.setattr(yahoo, "fetch", fake_fetch)
    path = yahoo.load_prices("20260101T000000Z")

    today_ny = datetime.now(NEW_YORK).date()
    assert calls == [("NVDA", date(2005, 1, 1), today_ny, False),
                     ("ATVI", date(2005, 1, 1), today_ny, False),
                     ("CART", date(2023, 9, 19), today_ny, False)]
    tickers = duckdb.sql(
        f"SELECT ticker, count(*) FROM read_parquet('{path.as_posix()}') GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert tickers == [("CART", 2), ("NVDA", 2)]
    windows = duckdb.sql(
        f"SELECT DISTINCT ticker, pull_start, pull_end FROM read_parquet('{path.as_posix()}') ORDER BY 1"
    ).fetchall()
    assert windows == [("CART", date(2023, 9, 19), today_ny), ("NVDA", date(2005, 1, 1), today_ny)]


def test_drift_in_any_ticker_writes_nothing(root, monkeypatch):
    monkeypatch.setattr(config, "sources", lambda: {
        "backfill_start": date(2005, 1, 1), "yahoo": {"auto_adjust": False}})
    monkeypatch.setattr(config, "watchlist", lambda: [
        {"ticker": "NVDA", "yahoo_symbol": "NVDA", "active_from": None, "active_to": None},
        {"ticker": "AAPL", "yahoo_symbol": "AAPL", "active_from": None, "active_to": None},
    ])
    monkeypatch.setattr(yahoo, "fetch", lambda symbol, *a: fake_bars() if symbol == "NVDA"
                        else fake_bars().drop(columns=["Volume"]))
    with pytest.raises(SchemaDriftError):
        yahoo.load_prices("20260101T000000Z")
    assert not (root / "raw").exists()
    assert not (root / "health").exists()
