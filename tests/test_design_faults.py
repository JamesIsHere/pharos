"""The five faults of design.md section 7, end to end (step 6).

Each fault is injected where it would really happen, then driven through the
real pipeline: refresh() (load -> stage -> checks -> publish -> manifest), then
health.evaluate(). The assertion is the outcome a user would see: the check's
color, whether serving/ moved, and the health status. The tests in
test_faults.py tamper with staged tables to prove each check's SQL fires; these
prove the pipeline delivers the fault to the check.

Synthetic data only (open issue #10): one ticker with two months of XNYS
sessions and one FRED series, so the suite runs anywhere, CI included.
"""

import json
from datetime import date, datetime, timedelta, timezone

import exchange_calendars as xc
import pandas as pd
import pytest

from pharos import config, health
from pharos.audit import audit
from pharos import refresh as refresh_mod
from pharos.paths import data_root
from pharos.publish import current_version
from pharos.runs import write_raw
from pharos.stage import stage
from conftest import tiingo_rows

RUN1, RUN2 = "20261007T140000Z", "20261008T140000Z"
FIRST = date(2026, 8, 3)
SESSIONS = [d.date() for d in xc.get_calendar("XNYS").sessions_in_range("2026-08-03", "2026-10-06")]


def bars(days, close=lambda d: 100 + 0.5 * SESSIONS.index(d) if d in SESSIONS else 130.0, full=True):
    n = len(days)
    closes = [close(d) for d in days]
    return pd.DataFrame({
        "ticker": ["NVDA"] * n, "yahoo_symbol": ["NVDA"] * n, "obs_date": days,
        "open": closes, "high": closes, "low": closes, "close": closes, "adj_close": closes,
        "volume": [100] * n, "dividends": [0.0] * n, "stock_splits": [0.0] * n,
        "pull_start": [date(2026, 1, 1) if full else days[0]] * n, "pull_end": [days[-1]] * n})


def fred_raw(run_id):
    write_raw(pd.DataFrame({"fred_id": ["GDP"], "obs_date": [date(2026, 4, 1)],
                            "realtime_start": [date(2026, 7, 30)], "realtime_end": [date(9999, 12, 31)],
                            "value": ["100.5"]}), "fred", "observations", run_id)
    write_raw(pd.DataFrame({"fred_id": ["GDP"], "title": ["Gross Domestic Product"], "frequency_short": ["Q"],
                            "seasonal_adjustment_short": ["SAAR"], "observation_start": ["2026-04-01"],
                            "realtime_start": [date(1991, 12, 4)], "realtime_end": [date(9999, 12, 31)],
                            "units": ["Billions of Dollars"]}), "fred", "series", run_id)


def run(monkeypatch, run_id, prices):
    """One refresh() whose loaders land `prices` and the FRED fixture."""
    monkeypatch.setattr(refresh_mod, "new_run_id", lambda: run_id)
    monkeypatch.setattr(refresh_mod.yahoo, "load_prices", lambda r: write_raw(prices, "yf", "prices", r))
    monkeypatch.setattr(refresh_mod.fred, "load_macro", fred_raw)
    days = sorted(set(prices["obs_date"]))
    monkeypatch.setattr(refresh_mod.tiingo, "load_prices", lambda r: write_raw(
        tiingo_rows("NVDA", days, [100 + 0.5 * i for i in range(len(days))]), "tiingo", "prices", r))
    return refresh_mod.refresh()[1]


def serving_bytes():
    root = data_root() / "serving"
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def status(publication, check_id):
    r = next(r for r in publication.evaluation.results if r["check_id"] == check_id)
    return r["status"], r["failing_row_count"]


def at(month, day, hour):
    return datetime(2026, month, day, hour, tzinfo=timezone.utc)


@pytest.fixture
def published(root, monkeypatch):
    """RUN1 published cleanly: a full NVDA pull 2026-08-03 .. 10-06, plus GDP."""
    monkeypatch.setattr(config, "watchlist", lambda: [
        {"ticker": "NVDA", "yahoo_symbol": "NVDA", "active_from": FIRST, "active_to": None}])
    pub = run(monkeypatch, RUN1, bars(SESSIONS))
    assert pub.evaluation.verdict == "passed" and current_version() == pub.version
    assert health.evaluate(at(10, 7, 15)).status == "yellow"       # C13: no baseline yet
    assert audit().passed                                            # the opening-balance audit records it
    assert health.evaluate(at(10, 7, 15)).status == "green"
    return monkeypatch


def test_fault_1_month_missing_from_a_full_pull_blocks(published):
    september = [d for d in SESSIONS if d.month == 9]
    before = serving_bytes()
    pub = run(published, RUN2, bars([d for d in SESSIONS if d.month != 9]))
    assert status(pub, "C04") == ("error", len(september))   # every September session is a hole
    c04 = next(r for r in pub.evaluation.results if r["check_id"] == "C04")
    assert sorted(r["obs_date"] for r in json.loads(c04["sample"])) == [str(d) for d in september]   # every row kept (D42)
    assert pub.version is None and serving_bytes() == before
    h = health.evaluate(at(10, 8, 15))
    assert h.status == "red" and h.published_run == RUN1


def test_fault_2_duplicate_day(published):
    # In raw, a repeated bar is absorbed by staging's dedup: that is the design working.
    day = SESSIONS[-1]
    pub = run(published, RUN2, pd.concat([bars(SESSIONS[-5:], full=False), bars([day], full=False)]))
    assert status(pub, "C07") == ("pass", 0) and pub.version is not None

    # C07 guards the dedup itself: a duplicate that survives staging must block.
    from test_faults import tamper
    from pharos.publish import publish
    run3 = "20261009T140000Z"
    write_raw(bars(SESSIONS[-5:], full=False), "yf", "prices", run3)
    fred_raw(run3)
    write_raw(tiingo_rows("NVDA", SESSIONS[-5:], [1.0] * 5), "tiingo", "prices", run3)
    stage(run3)
    tamper(run3, "observations", "SELECT * FROM t UNION ALL (SELECT * FROM t "
                                 f"WHERE series_id = 'yf:close:NVDA' AND obs_date = DATE '{day}' LIMIT 1)")
    before = serving_bytes()
    pub = publish(run3)
    assert status(pub, "C07")[0] == "error"
    assert pub.version is None and serving_bytes() == before


def test_fault_3_last_publish_two_days_old_is_red(published):
    # backdating the last published run by 2 days == looking 2 days later
    published_at = health.evaluate(at(10, 7, 15)).published_at
    h = health.evaluate(published_at + timedelta(days=2))
    assert h.status == "red"
    assert next(r for r in h.results if r["check_id"] == "C01")["status"] == "error"


def test_fault_4_close_times_ten_warns_and_publishes(published):
    new_day = date(2026, 10, 7)
    last = 100 + 0.5 * (len(SESSIONS) - 1)
    prices = bars(SESSIONS[-5:] + [new_day], close=lambda d: last * 10 if d == new_day
                  else 100 + 0.5 * SESSIONS.index(d), full=False)
    pub = run(published, RUN2, prices)
    assert status(pub, "C10") == ("warn", 1)
    assert pub.version is not None and current_version() == pub.version   # a warning never blocks
    assert health.evaluate(at(10, 8, 15)).status == "yellow"


def test_fault_5_past_close_changed_in_a_later_snapshot_blocks(published):
    changed = SESSIONS[-3]
    prices = bars(SESSIONS[-5:], close=lambda d: 999.0 if d == changed else 100 + 0.5 * SESSIONS.index(d),
                  full=False)                                  # incremental: same vintage as RUN1
    before = serving_bytes()
    pub = run(published, RUN2, prices)
    assert status(pub, "C14") == ("error", 1)                  # staging kept both values for C14
    assert pub.version is None and serving_bytes() == before
    assert health.evaluate(at(10, 8, 15)).status == "red"
