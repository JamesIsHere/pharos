"""Opening-balance audit (D39): a baseline is recorded only when every active
ticker reconciles on at least 5 random dates; every comparison is written
either way. Synthetic data only: one ticker with 11 XNYS sessions, plus GDP."""

from datetime import date

import duckdb
import exchange_calendars as xc
import pandas as pd
import pytest

from pharos import cli, config
from pharos.audit import AuditError, audit, baseline_dir
from pharos.publish import publish
from pharos.runs import write_raw
from pharos.stage import stage
from conftest import tiingo_rows

RUN = "20261007T140000Z"
SESSIONS = [d.date() for d in xc.get_calendar("XNYS").sessions_in_range("2026-09-22", "2026-10-06")]


def land(tiingo_closes=None, tiingo_days=None, tiingo_ticker="NVDA"):
    """Land, stage and publish RUN: NVDA closes 100.0, 100.5, ... and the same
    from Tiingo unless overridden."""
    n = len(SESSIONS)
    closes = [100 + 0.5 * i for i in range(n)]
    write_raw(pd.DataFrame({
        "ticker": ["NVDA"] * n, "yahoo_symbol": ["NVDA"] * n, "obs_date": SESSIONS,
        "open": closes, "high": closes, "low": closes, "close": closes, "adj_close": closes,
        "volume": [100] * n, "dividends": [0.0] * n, "stock_splits": [0.0] * n,
        "pull_start": [date(2026, 1, 1)] * n, "pull_end": [SESSIONS[-1]] * n}), "yf", "prices", RUN)
    write_raw(pd.DataFrame({"fred_id": ["GDP"], "obs_date": [date(2026, 4, 1)],
                            "realtime_start": [date(2026, 7, 30)], "realtime_end": [date(9999, 12, 31)],
                            "value": ["100.5"]}), "fred", "observations", RUN)
    write_raw(pd.DataFrame({"fred_id": ["GDP"], "title": ["Gross Domestic Product"], "frequency_short": ["Q"],
                            "seasonal_adjustment_short": ["SAAR"], "observation_start": ["2026-04-01"],
                            "realtime_start": [date(1991, 12, 4)], "realtime_end": [date(9999, 12, 31)],
                            "units": ["Billions of Dollars"]}), "fred", "series", RUN)
    days = tiingo_days or SESSIONS
    write_raw(tiingo_rows(tiingo_ticker, days, tiingo_closes or [100 + 0.5 * SESSIONS.index(d) for d in days]),
              "tiingo", "prices", RUN)
    stage(RUN)
    assert publish(RUN).version is not None


@pytest.fixture
def published(root, monkeypatch):
    monkeypatch.setattr(config, "watchlist", lambda: [
        {"ticker": "NVDA", "yahoo_symbol": "NVDA", "active_from": SESSIONS[0], "active_to": None}])
    return root


def read(path):
    return duckdb.sql(f"SELECT * FROM read_parquet('{path.as_posix()}') ORDER BY ALL").fetchall()


def test_agreeing_sources_record_the_baseline(published):
    land()
    a = audit()
    assert a.passed and a.failures == [] and a.baseline.name == f"{RUN}.parquet"
    rows = duckdb.sql(f"SELECT ticker, pick, problem FROM read_parquet('{a.comparisons.as_posix()}')").fetchall()
    assert len(rows) == 6 and sum(p == "random" for _, p, _ in rows) == 5
    assert all(problem is None for _, _, problem in rows)
    assert [r[:4] for r in read(a.baseline)] == [
        ("fred:GDP", date(2026, 4, 1), date(2026, 4, 1), 1),
        ("yf:close:NVDA", SESSIONS[0], SESSIONS[-1], len(SESSIONS))]


def test_one_date_over_tolerance_records_nothing_but_writes_every_comparison(published):
    land()
    sampled = [r[1] for r in duckdb.sql(
        f"SELECT ticker, obs_date FROM read_parquet('{audit().comparisons.as_posix()}')").fetchall()]
    # start over with Tiingo 0.6% off on one sampled date (same run_id, same draw)
    for p in sorted(published.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    off = sampled[0]
    land(tiingo_closes=[(100 + 0.5 * i) * (1.006 if d == off else 1) for i, d in enumerate(SESSIONS)])
    a = audit()
    assert not a.passed and a.baseline is None and not any(baseline_dir().glob("*.parquet"))
    assert [(f["ticker"], f["obs_date"], f["problem"]) for f in a.failures] == [("NVDA", off, "over tolerance")]
    assert len(read(a.comparisons)) == 6                    # the passing comparisons are recorded too


def test_fewer_than_5_random_dates_records_nothing(published):
    land(tiingo_days=SESSIONS[:4])
    a = audit()
    assert not a.passed
    assert [(f["ticker"], f["problem"]) for f in a.failures] == [("NVDA", "only 4 random dates compared")]


def test_active_ticker_missing_from_tiingo_records_nothing(published):
    land(tiingo_ticker="MSFT")                   # Tiingo answered, but not for NVDA
    a = audit()
    assert not a.passed
    assert [(f["ticker"], f["problem"]) for f in a.failures] == [("NVDA", "only 0 random dates compared")]


def test_ended_ticker_is_not_required(published, monkeypatch):
    monkeypatch.setattr(config, "watchlist", lambda: [
        {"ticker": "NVDA", "yahoo_symbol": "NVDA", "active_from": SESSIONS[0], "active_to": None},
        {"ticker": "ATVI", "yahoo_symbol": "ATVI", "active_from": SESSIONS[0], "active_to": date(2026, 9, 1)}])
    land()
    assert audit().passed                        # ATVI ended: no rows anywhere, not required


def test_existing_baseline_stops_a_second_audit(published):
    land()
    first = audit().baseline
    with pytest.raises(AuditError, match="already recorded"):
        audit()
    assert sorted(baseline_dir().glob("*.parquet")) == [first]


def test_nothing_published_stops_the_audit(root):
    with pytest.raises(AuditError, match="nothing is published"):
        audit()


def test_cli_exit_code(published, capsys):
    land(tiingo_days=SESSIONS[:4])
    assert cli.main(["audit"]) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "no baseline recorded" in out and "only 4 random dates" in out
