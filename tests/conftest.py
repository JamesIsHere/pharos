"""Shared fixtures: a throwaway data root with config pinned to one ticker and
one FRED series, and one complete run landed and staged on top of it. Each
series' window starts at its first row, so the clean run has no expected gaps."""

from datetime import date

import pandas as pd
import pytest

from pharos import config
from pharos.runs import write_raw
from pharos.stage import stage

RUN = "20261007T140000Z"


@pytest.fixture(autouse=True)
def no_tiingo_network(monkeypatch):
    """No test reaches Tiingo: an empty key makes the loader refuse, and .env
    loading never overrides a variable that is already set."""
    monkeypatch.setenv("TIINGO_API_KEY", "")


def tiingo_rows(ticker, days, closes):
    n = len(days)
    return pd.DataFrame({"ticker": [ticker] * n, "tiingo_symbol": [ticker.replace(".", "-")] * n,
                         "obs_date": days, "open": closes, "high": closes, "low": closes, "close": closes,
                         "volume": [100] * n, "adj_open": closes, "adj_high": closes, "adj_low": closes,
                         "adj_close": closes, "adj_volume": [100] * n, "div_cash": [0.0] * n,
                         "split_factor": [1.0] * n, "pull_start": [date(2026, 1, 1)] * n,
                         "pull_end": [days[-1]] * n})


@pytest.fixture
def root(monkeypatch, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(data))
    monkeypatch.setattr(config, "sources", lambda: {
        "backfill_start": date(2026, 1, 1), "fred": {"series": ["GDP"], "expected_lag_days": {"GDP": 35}},
        "yahoo": {"required_series": ["close"], "expected_lag_days": 0}})
    monkeypatch.setattr(config, "watchlist", lambda: [
        {"ticker": "NVDA", "yahoo_symbol": "NVDA", "active_from": date(2026, 10, 5), "active_to": None}])
    monkeypatch.setattr(config, "corporate_actions", lambda: [])
    return data


@pytest.fixture
def staged_run(root):
    """One complete run, staged: two NVDA closes and one GDP value, plus the same
    two NVDA closes from the reference source (Tiingo)."""
    n = 2
    write_raw(pd.DataFrame({
        "ticker": ["NVDA"] * n, "yahoo_symbol": ["NVDA"] * n,
        "obs_date": [date(2026, 10, 5), date(2026, 10, 6)],
        "open": [10.0, 11.0], "high": [10.0, 11.0], "low": [10.0, 11.0], "close": [10.0, 11.0],
        "adj_close": [10.0, 11.0], "volume": [100] * n, "dividends": [0.0] * n, "stock_splits": [0.0] * n,
        "pull_start": [date(2026, 1, 1)] * n, "pull_end": [date(2026, 10, 7)] * n}), "yf", "prices", RUN)
    write_raw(pd.DataFrame({"fred_id": ["GDP"], "obs_date": [date(2026, 4, 1)],
                            "realtime_start": [date(2026, 7, 30)], "realtime_end": [date(9999, 12, 31)],
                            "value": ["100.5"]}), "fred", "observations", RUN)
    write_raw(pd.DataFrame({"fred_id": ["GDP"], "title": ["Gross Domestic Product"], "frequency_short": ["Q"],
                            "seasonal_adjustment_short": ["SAAR"], "observation_start": ["2026-04-01"],
                            "realtime_start": [date(1991, 12, 4)], "realtime_end": [date(9999, 12, 31)],
                            "units": ["Billions of Dollars"]}), "fred", "series", RUN)
    write_raw(tiingo_rows("NVDA", [date(2026, 10, 5), date(2026, 10, 6)], [10.0, 11.0]), "tiingo", "prices", RUN)
    stage(RUN)
    return RUN
