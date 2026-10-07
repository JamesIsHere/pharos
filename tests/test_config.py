"""The config files are the declared expectation. These tests stop a bad edit to them
from ever reaching a run: a typo here would otherwise surface as a false alarm
(or a missed one) in the health checks."""

import csv
from datetime import date

import yaml

from pharos.paths import PROJECT_ROOT

CONFIG = PROJECT_ROOT / "config"
WATCHLIST_COLUMNS = ["ticker", "yahoo_symbol", "cik", "trap", "active_from", "active_to"]


def load_watchlist() -> list[dict]:
    with open(CONFIG / "watchlist.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_sources() -> dict:
    with open(CONFIG / "sources.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_watchlist_columns_exact():
    with open(CONFIG / "watchlist.csv", newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == WATCHLIST_COLUMNS


def test_watchlist_has_18_unique_names():
    rows = load_watchlist()
    assert len(rows) == 18
    assert len({r["ticker"] for r in rows}) == 18
    assert len({r["yahoo_symbol"] for r in rows}) == 18


def test_every_name_states_its_trap():
    assert all(r["trap"].strip() for r in load_watchlist())


def test_active_windows_are_valid_dates_after_backfill_start():
    start = load_sources()["backfill_start"]
    for r in load_watchlist():
        lo = date.fromisoformat(r["active_from"]) if r["active_from"] else None
        hi = date.fromisoformat(r["active_to"]) if r["active_to"] else None
        if lo:
            assert lo > start, f"{r['ticker']}: active_from before backfill_start is meaningless"
        if lo and hi:
            assert lo < hi, f"{r['ticker']}: window ends before it starts"


def test_sources_yahoo_settings_explicit():
    yahoo = load_sources()["yahoo"]
    assert yahoo["auto_adjust"] is False
    assert isinstance(yahoo["overlap_days"], int) and yahoo["overlap_days"] > 0
    assert yahoo["required_series"] == ["close"]


def test_sources_fred_series_unique():
    series = load_sources()["fred"]["series"]
    assert series and len(series) == len(set(series))


def test_every_fred_series_has_an_expected_lag():
    cfg = load_sources()
    lags = cfg["fred"]["expected_lag_days"]
    assert sorted(lags) == sorted(cfg["fred"]["series"])
    assert all(isinstance(v, int) and v >= 0 for v in lags.values())
    assert isinstance(cfg["yahoo"]["expected_lag_days"], int)
