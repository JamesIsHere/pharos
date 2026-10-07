"""board.py and the health page (step 8). These pin: the coverage heatmap and
C04 read one definition, so a hole is the same fact in both (D44); the spot
check sample is fixed for a run and moves with the next one (D44); failing rows
group by the source they name (D42); the page renders from a served root and
the app never writes health/ (D43)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import polars as pl
import streamlit as st
from streamlit.testing.v1 import AppTest

from pharos import board
from pharos.checks import run_checks
from pharos.paths import PROJECT_ROOT, data_root
from pharos.publish import current_version
from test_health import served  # noqa: F401  (fixture)
from test_design_faults import published  # noqa: F401  (fixture)

HEALTH_PAGE = PROJECT_ROOT / "app" / "pages" / "1_Health.py"


def drop_served_day(day: str):
    """Delete one NVDA close from the served version (synthetic root only)."""
    path = current_version() / "observations.parquet"
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{path.as_posix()}')")
    con.execute(f"COPY (SELECT * FROM t WHERE NOT (series_id = 'yf:close:NVDA' AND obs_date = DATE '{day}')) "
                f"TO '{path.as_posix()}' (FORMAT parquet)")
    con.close()


def test_heatmap_hole_is_c04s_hole(served):
    drop_served_day("2026-10-05")
    c04 = next(r for r in run_checks(served, tables=current_version(), record=False).results
               if r["check_id"] == "C04")
    assert c04["status"] == "error"
    assert [r["obs_date"] for r in json.loads(c04["sample"])] == ["2026-10-05"]
    october = board.coverage(board.connect()).filter(pl.col("series_id") == "yf:close:NVDA")
    assert october.select("expected", "present", "share_present").row(0) == (2, 1, 0.5)


def test_quarterly_coverage_fills_its_quarter(served):
    gdp = board.coverage(board.connect()).filter(pl.col("series_id") == "fred:GDP")
    assert [str(m) for m in gdp["month"]] == ["2026-04-01", "2026-05-01", "2026-06-01"]
    assert gdp["share_present"].to_list() == [1.0, 1.0, 1.0]


def test_spot_checks_fixed_per_run(served):
    con = board.connect()
    first, again = board.spot_checks(con), board.spot_checks(board.connect())
    assert first.equals(again)
    assert set(first["source"]) == {"fred", "yf"}
    assert all(link.startswith("https://") for link in first["link"])


def test_spot_check_seed_is_the_run_id(served):
    con = board.connect()
    seeds = [con.execute("SELECT hash(?, 'yf:close:NVDA', DATE '2026-10-05', DATE '2026-10-07')", [r]).fetchone()[0]
             for r in (served, "20991231T000000Z")]
    assert seeds[0] != seeds[1]


def test_failing_rows_grouped_by_named_source():
    rows = [{"series_id": "yf:close:NVDA"}, {"series_id": "fred:GDP"}, {"series_id": "yf:close:AAPL"},
            {"ticker": "NVDA", "obs_date": "2026-10-05"}, {"run_id": "x", "source": "fred", "dataset": "series"},
            {"last_published_at": None, "series_id": None}]
    groups = board.failing_rows_by_source({"sample": json.dumps(rows)})
    assert {k: len(v) for k, v in groups.items()} == {"yf": 2, "fred": 2, "tiingo": 1, "run": 1}


def test_health_page_renders_and_writes_nothing(published):
    """On a root built by a real refresh() (full run manifest) and audit."""
    monkeypatch = published
    monkeypatch.syspath_prepend(str(HEALTH_PAGE.parents[1]))    # streamlit adds app/ when it runs Home.py
    st.cache_data.clear(); st.cache_resource.clear()            # every synthetic root publishes at 14:30
    health_dir = data_root() / "health"
    before = {p: p.stat().st_mtime_ns for p in health_dir.rglob("*") if p.is_file()}
    page = AppTest.from_file(str(HEALTH_PAGE), default_timeout=60).run()
    assert not page.exception
    assert [h.value for h in page.header] == ["Failing checks", "Coverage", "Rows landed by run",
                                              "Last 30 runs", "Spot checks"]
    assert len(page.error) + len(page.warning) + len(page.success) == 1       # the status bar
    assert {p: p.stat().st_mtime_ns for p in health_dir.rglob("*") if p.is_file()} == before


def test_acknowledged_state_is_green_on_the_page_and_listed(published):
    """D46 on the page: the bar is green, and C16 still appears among the
    checks, as acknowledged, with its note on the row."""
    from pharos import config
    from test_design_faults import bar, health_page
    from test_faults import GDP_ACK
    monkeypatch = published
    path = current_version() / "observations.parquet"
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{path.as_posix()}')")
    con.execute(f"COPY (SELECT * REPLACE (CASE WHEN series_id = 'fred:GDP' THEN NULL ELSE value END AS value) "
                f"FROM t) TO '{path.as_posix()}' (FORMAT parquet)")
    con.close()
    monkeypatch.setattr(config, "expected_states", lambda: [GDP_ACK])
    page = health_page(monkeypatch, datetime(2026, 10, 7, 15, tzinfo=timezone.utc))
    assert bar(page) == "green"
    checks = page.dataframe[0].value
    assert checks[["check", "status"]].values.tolist() == [["C16", "acknowledged"]]
    assert page.dataframe[1].value["acknowledged"].tolist() == ["test: withdrawn"]
    assert [m.value for m in page.metric][3].split(" / ")[1:] == ["1", "0", "0"]
