"""The Tiingo loader (D35, D36). Network is never touched: fetch is replaced
with rows in Tiingo's documented shape. These tests pin the shape check, the
column mapping, 404 handling, the key staying out of the URL, and that drift
anywhere writes nothing."""

import urllib.request
from datetime import date

import duckdb
import pytest

from pharos import config
from pharos.loaders import tiingo
from pharos.paths import data_root

START, END = date(2026, 1, 1), date(2026, 10, 7)


def row(day, close, split=1.0, div=0.0):
    return {"date": f"{day}T00:00:00.000Z", "open": close, "high": close, "low": close, "close": close,
            "volume": 1000, "adjOpen": close, "adjHigh": close, "adjLow": close, "adjClose": close,
            "adjVolume": 1000, "divCash": div, "splitFactor": split}


@pytest.fixture
def two_tickers(root, monkeypatch):
    monkeypatch.setattr(config, "watchlist", lambda: [
        {"ticker": "NVDA", "active_from": None, "active_to": None},
        {"ticker": "BRK.B", "active_from": None, "active_to": None},
        {"ticker": "ATVI", "active_from": None, "active_to": date(2023, 10, 12)}])
    return monkeypatch


def test_normalize_maps_columns_and_dates():
    out = tiingo.normalize([row("2026-10-05", 10.0), row("2026-10-06", 20.0, split=10.0)], "NVDA", "NVDA", START, END)
    assert list(out.columns) == ["ticker", "tiingo_symbol", "obs_date", "open", "high", "low", "close", "volume",
                                 "adj_open", "adj_high", "adj_low", "adj_close", "adj_volume", "div_cash",
                                 "split_factor", "pull_start", "pull_end"]
    assert list(out["obs_date"]) == [date(2026, 10, 5), date(2026, 10, 6)]
    assert list(out["split_factor"]) == [1.0, 10.0]


@pytest.mark.parametrize("rows, message", [
    ([{**row("2026-10-05", 10.0), "extra": 1}], "row keys"),
    ([{k: v for k, v in row("2026-10-05", 10.0).items() if k != "splitFactor"}], "row keys"),
    ([{**row("2026-10-05", 10.0), "date": "2026-10-05T04:00:00.000Z"}], "not midnight UTC"),
    ({"detail": "Error"}, "expected a list"),
])
def test_schema_drift_fails_loud(rows, message):
    with pytest.raises(tiingo.SchemaDriftError, match=message):
        tiingo.normalize(rows, "NVDA", "NVDA", START, END)


def test_load_writes_one_file_and_reports_not_found(two_tickers):
    def fetch(sym, start, end):
        if sym == "ATVI":
            raise tiingo.TickerNotFound(sym)
        return [row("2026-10-05", 10.0), row("2026-10-06", 11.0)]
    two_tickers.setattr(tiingo, "fetch", fetch)
    report = tiingo.load_prices("20261007T140000Z", today=END)
    assert report == {"rows": 4, "not_found": ["ATVI"]}
    path = data_root() / "raw" / "tiingo" / "prices" / "20261007T140000Z.parquet"
    got = duckdb.sql(f"SELECT DISTINCT ticker, tiingo_symbol FROM read_parquet('{path.as_posix()}') ORDER BY 1").fetchall()
    assert got == [("BRK.B", "BRK-B"), ("NVDA", "NVDA")]


def test_drift_in_any_ticker_writes_nothing(two_tickers):
    def fetch(sym, start, end):
        r = row("2026-10-05", 10.0)
        return [r] if sym == "NVDA" else [{**r, "extra": 1}]
    two_tickers.setattr(tiingo, "fetch", fetch)
    with pytest.raises(tiingo.SchemaDriftError):
        tiingo.load_prices("20261007T140000Z", today=END)
    assert not (data_root() / "raw" / "tiingo").exists()
    assert not list(data_root().glob("health/loads/*tiingo*"))   # no load record either


def test_key_travels_in_header_not_url(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "secret-token")
    seen = {}

    class Done(Exception):
        pass

    def urlopen(request, timeout):
        seen["url"], seen["auth"] = request.full_url, request.get_header("Authorization")
        raise Done
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(Done):
        tiingo.fetch("BRK-B", START, END)
    assert "secret-token" not in seen["url"] and seen["auth"] == "Token secret-token"
    assert seen["url"].startswith("https://api.tiingo.com/tiingo/daily/BRK-B/prices?startDate=2026-01-01&endDate=2026-10-07")


def test_missing_key_fails_loud(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TIINGO_API_KEY is not set"):
        tiingo._api_key()
