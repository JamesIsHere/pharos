"""Opening-balance audit (D39, D41): every common date is compared; an
isolated day over 0.5% is reported, anything else over it blocks, and so does
an active ticker with fewer than 5 dates compared. Every comparison is written
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


def off(*days, by=1.006):
    """Tiingo closes with the given sessions off by `by`."""
    return [(100 + 0.5 * i) * (by if d in days else 1) for i, d in enumerate(SESSIONS)]


def test_agreeing_sources_record_the_baseline(published):
    land()
    a = audit()
    assert a.passed and a.failures == [] and a.disagreements == [] and a.baseline.name == f"{RUN}.parquet"
    rows = duckdb.sql(f"SELECT problem FROM read_parquet('{a.comparisons.as_posix()}')").fetchall()
    assert rows == [(None,)] * len(SESSIONS)                # every common date compared
    assert [r[:4] for r in read(a.baseline)] == [
        ("fred:GDP", date(2026, 4, 1), date(2026, 4, 1), 1),
        ("yf:close:NVDA", SESSIONS[0], SESSIONS[-1], len(SESSIONS))]


def test_isolated_day_is_reported_and_does_not_block(published):
    land(tiingo_closes=off(SESSIONS[5]))
    a = audit()
    assert a.passed and a.failures == []
    assert [(f["ticker"], f["obs_date"], f["problem"]) for f in a.disagreements] == [
        ("NVDA", SESSIONS[5], "print disagreement")]


@pytest.mark.parametrize("days", [(SESSIONS[5], SESSIONS[6]), (SESSIONS[0],), (SESSIONS[-1],)],
                         ids=["two consecutive", "first date", "last date"])
def test_disagreement_that_is_not_isolated_blocks(published, days):
    land(tiingo_closes=off(*days))
    a = audit()
    assert not a.passed and a.baseline is None and not any(baseline_dir().glob("*.parquet"))
    assert [(f["obs_date"], f["problem"]) for f in a.failures] == [(d, "disagreement not isolated") for d in days]
    assert len(read(a.comparisons)) == len(SESSIONS)        # the passing comparisons are recorded too


def test_within_tolerance_does_not_count(published):
    land(tiingo_closes=off(SESSIONS[5], SESSIONS[6], by=1.004))
    a = audit()
    assert a.passed and a.disagreements == []


def test_fewer_than_5_common_dates_records_nothing(published):
    land(tiingo_days=SESSIONS[:4])
    a = audit()
    assert not a.passed
    assert [(f["ticker"], f["problem"]) for f in a.failures] == [("NVDA", "only 4 common dates compared")]


def test_active_ticker_missing_from_tiingo_records_nothing(published):
    land(tiingo_ticker="MSFT")                   # Tiingo answered, but not for NVDA
    a = audit()
    assert not a.passed
    assert [(f["ticker"], f["problem"]) for f in a.failures] == [("NVDA", "only 0 common dates compared")]


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
    assert "FAILED" in out and "no baseline recorded" in out and "only 4 common dates" in out
