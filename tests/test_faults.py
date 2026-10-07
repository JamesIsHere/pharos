"""Fault injection: each real check in checks/ must fire on the fault it exists
to catch. A clean staged run passes first, so a check that fires on clean data
can't hide behind its fault test. Faults are injected into the staged tables
of a tiny synthetic run (conftest.py), never into real data."""

import duckdb
import pytest

from pharos.checks import run_checks
from pharos.paths import data_root
from pharos.runs import write_raw
from pharos.stage import stage

RUN2 = "20261008T140000Z"


def staged_file(run_id, table):
    return data_root() / "staging" / run_id / f"{table}.parquet"


def tamper(run_id, table, select):
    """Rewrite one staged table as `select` over its current rows (named t)."""
    path = staged_file(run_id, table)
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{path.as_posix()}')")
    con.execute(f"COPY ({select}) TO '{path.as_posix()}' (FORMAT parquet)")
    con.close()


def result(evaluation, check_id):
    return next(r for r in evaluation.results if r["check_id"] == check_id)


def test_clean_run_passes(staged_run):
    e = run_checks(staged_run)
    assert e.verdict == "passed", [(r["check_id"], r["status"], r["error"]) for r in e.results
                                   if r["status"] != "pass"]


@pytest.mark.parametrize("dropped", ["yf:close:NVDA", "fred:GDP"])
def test_c03_missing_catalog_series_blocks(staged_run, dropped):
    tamper(staged_run, "series_catalog", f"SELECT * FROM t WHERE series_id <> '{dropped}'")
    e = run_checks(staged_run)
    r = result(e, "C03")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert dropped in r["sample"]
    assert e.verdict == "blocked"


def test_c04_interior_price_hole_blocks(staged_run):
    tamper(staged_run, "observations", "SELECT * FROM t WHERE NOT (series_id = 'yf:close:NVDA' "
                                       "AND obs_date = DATE '2026-10-05')")
    # NVDA's window starts at active_from 2026-10-05, so a missing first day is a hole
    e = run_checks(staged_run)
    r = result(e, "C04")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert '"obs_date": "2026-10-05"' in r["sample"]
    assert e.verdict == "blocked"


def test_c04_trailing_gap_is_not_a_hole(staged_run):
    # the last day missing is lateness: C02's to report, not C04's
    tamper(staged_run, "observations", "SELECT * FROM t WHERE NOT (series_id = 'yf:close:NVDA' "
                                       "AND obs_date = DATE '2026-10-06')")
    assert result(run_checks(staged_run), "C04")["status"] == "pass"


def test_c04_macro_quarter_hole_blocks(staged_run):
    # GDP has 2026-04-01; adding 2026-10-01 leaves 2026-07-01 missing between them
    tamper(staged_run, "observations", "SELECT * FROM t UNION ALL "
                                       "SELECT * REPLACE (DATE '2026-10-01' AS obs_date) FROM t "
                                       "WHERE series_id = 'fred:GDP'")
    r = result(run_checks(staged_run), "C04")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert '"obs_date": "2026-07-01"' in r["sample"]


def test_c04_price_date_only_in_older_vintage_blocks(staged_run):
    # a full re-pull (new vintage) lacks 2026-10-05; the old vintage still has it.
    # Serving would fill the date from the old adjustment basis, so C04 must fire.
    tamper(staged_run, "observations", """
        SELECT * FROM t WHERE NOT (series_id = 'yf:close:NVDA' AND obs_date = DATE '2026-10-05')
        UNION ALL
        SELECT * REPLACE (vintage - 1 AS vintage) FROM t
        WHERE series_id = 'yf:close:NVDA' AND obs_date = DATE '2026-10-05'""")
    r = result(run_checks(staged_run), "C04")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)


def test_withdrawn_value_warns_c16_and_passes_c04(staged_run):
    tamper(staged_run, "observations", "SELECT * REPLACE (CASE WHEN series_id = 'fred:GDP' THEN NULL "
                                       "ELSE value END AS value) FROM t")
    e = run_checks(staged_run)
    assert result(e, "C04")["status"] == "pass"
    r = result(e, "C16")
    assert (r["status"], r["failing_row_count"]) == ("warn", 1)
    assert e.verdict == "passed"


def raw_file(run_id, source="yf", dataset="prices"):
    return data_root() / "raw" / source / dataset / f"{run_id}.parquet"


def reland(run_from, run_to):
    """Land run_to as an identical copy of run_from through the real writer."""
    for source, dataset in [("yf", "prices"), ("fred", "observations"), ("fred", "series")]:
        frame = duckdb.sql(f"SELECT * EXCLUDE (run_id, loaded_at) FROM "
                           f"read_parquet('{raw_file(run_from, source, dataset).as_posix()}')").df()
        write_raw(frame, source, dataset, run_to)


def test_c05_recorded_file_missing_blocks(staged_run):
    reland(staged_run, RUN2)
    stage(RUN2)
    raw_file(staged_run).unlink()
    e = run_checks(RUN2)
    r = result(e, "C05")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert "recorded file missing" in r["sample"]
    assert e.verdict == "blocked"


def test_c05_truncated_file_blocks(staged_run):
    path = raw_file(staged_run)
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{path.as_posix()}')")
    con.execute(f"COPY (SELECT * FROM t LIMIT 1) TO '{path.as_posix()}' (FORMAT parquet)")
    con.close()
    r = result(run_checks(staged_run), "C05")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert "row count changed" in r["sample"]


def test_c05_file_without_record_blocks(staged_run):
    stray = raw_file("20991231T000000Z")
    stray.write_bytes(raw_file(staged_run).read_bytes())
    r = result(run_checks(staged_run), "C05")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert "file has no load record" in r["sample"]


def test_c06_control_total_mismatch_blocks(staged_run):
    record = data_root() / "health" / "loads" / f"{staged_run}__yf__prices.parquet"
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{record.as_posix()}')")
    con.execute(f"COPY (SELECT * REPLACE (rows_downloaded + 1 AS rows_downloaded) FROM t) "
                f"TO '{record.as_posix()}' (FORMAT parquet)")
    con.close()
    e = run_checks(staged_run)
    r = result(e, "C06")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert e.verdict == "blocked"


def test_c07_duplicated_day_blocks(staged_run):
    tamper(staged_run, "observations", "SELECT * FROM t UNION ALL SELECT * FROM t "
                                       "WHERE series_id = 'yf:close:NVDA' AND obs_date = DATE '2026-10-05'")
    e = run_checks(staged_run)
    r = result(e, "C07")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert e.verdict == "blocked"


def test_c07_rows_differing_only_in_units_block(staged_run):
    tamper(staged_run, "observations", "SELECT * FROM t UNION ALL SELECT * REPLACE ('EUR' AS units) FROM t "
                                       "WHERE series_id = 'yf:close:NVDA' AND obs_date = DATE '2026-10-05'")
    assert result(run_checks(staged_run), "C07")["status"] == "error"


def test_c07_ignores_value_conflict(staged_run):
    # same key, different value: a source correction, C14's to report (D27)
    tamper(staged_run, "observations", "SELECT * FROM t UNION ALL SELECT * REPLACE (value * 1.01 AS value) FROM t "
                                       "WHERE series_id = 'yf:close:NVDA' AND obs_date = DATE '2026-10-05'")
    assert result(run_checks(staged_run), "C07")["status"] == "pass"


@pytest.mark.parametrize("table, select, problem", [
    ("observations", "SELECT * EXCLUDE (loaded_at) FROM t", "missing"),
    ("series_catalog", "SELECT *, 1 AS extra FROM t", "unexpected"),
    ("observations", "SELECT * REPLACE (CAST(value AS FLOAT) AS value) FROM t", "type"),
])
def test_c08_schema_drift_blocks(staged_run, table, select, problem):
    tamper(staged_run, table, select)
    r = result(run_checks(staged_run), "C08")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert problem in r["sample"]


NVDA_DAY = "series_id = 'yf:close:NVDA' AND obs_date = DATE '2026-10-05'"


@pytest.mark.parametrize("change, rule", [
    ("0.0 AS value", "price <= 0"),
    ("-1.0 AS value", "price <= 0"),
    # run date is 2026-10-07; available_date moves too so only one rule breaks
    ("DATE '2026-10-08' AS obs_date, DATE '2026-10-08' AS available_date", "obs_date after run date"),
    ("DATE '2026-10-04' AS available_date", "obs_date after available_date"),
])
def test_c09_impossible_price_row_blocks(staged_run, change, rule):
    tamper(staged_run, "observations",
           f"SELECT * REPLACE ({change}) FROM t WHERE {NVDA_DAY} UNION ALL SELECT * FROM t WHERE NOT ({NVDA_DAY})")
    e = run_checks(staged_run)
    r = result(e, "C09")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert rule in r["sample"]
    assert e.verdict == "blocked"


def test_c09_negative_macro_value_passes(staged_run):
    tamper(staged_run, "observations", "SELECT * REPLACE (CASE WHEN series_id = 'fred:GDP' THEN -100.5 "
                                       "ELSE value END AS value) FROM t")
    assert result(run_checks(staged_run), "C09")["status"] == "pass"


def test_c09_withdrawn_price_passes(staged_run):
    tamper(staged_run, "observations",
           f"SELECT * REPLACE (CASE WHEN {NVDA_DAY} THEN NULL ELSE value END AS value) FROM t")
    assert result(run_checks(staged_run), "C09")["status"] == "pass"


NVDA_DAY2 = "series_id = 'yf:close:NVDA' AND obs_date = DATE '2026-10-06'"


@pytest.mark.parametrize("factor, fires", [(0.5, True), (1.5, True), (1.3, False), (0.7, False)])
def test_c10_price_jump_warns(staged_run, factor, fires):
    # day 1 closes at 10.0; 0.5 is an unadjusted 2-for-1 split, the signature C10 must not exclude (D29)
    tamper(staged_run, "observations",
           f"SELECT * REPLACE (CASE WHEN {NVDA_DAY2} THEN 10.0 * {factor} ELSE value END AS value) FROM t")
    e = run_checks(staged_run)
    r = result(e, "C10")
    assert (r["status"], r["failing_row_count"]) == (("warn", 1) if fires else ("pass", 0))
    assert e.verdict == "passed"  # warn never blocks


def test_c10_ignores_jump_in_older_vintage(staged_run):
    tamper(staged_run, "observations",
           "SELECT * FROM t UNION ALL SELECT * REPLACE (vintage - 30 AS vintage, "
           f"CASE WHEN {NVDA_DAY2} THEN value * 3 ELSE value END AS value) "
           "FROM t WHERE series_id = 'yf:close:NVDA'")
    assert result(run_checks(staged_run), "C10")["status"] == "pass"


def test_c11_orphan_observation_blocks(staged_run):
    tamper(staged_run, "observations", "SELECT * FROM t UNION ALL SELECT * REPLACE ('yf:close:FAKE' AS series_id) "
                                       f"FROM t WHERE {NVDA_DAY}")
    e = run_checks(staged_run)
    r = result(e, "C11")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert "without catalog row" in r["sample"]
    assert e.verdict == "blocked"


@pytest.mark.parametrize("active_to, c11, c15", [
    ("NULL", "error", "pass"),                 # active, no rows: blocks
    ("DATE '2026-10-07'", "error", "pass"),    # ends on the run date: still active
    ("DATE '2026-10-06'", "pass", "warn"),     # ended: source-missing, monitor only
])
def test_series_without_observations_by_state(staged_run, active_to, c11, c15):
    tamper(staged_run, "observations", "SELECT * FROM t WHERE series_id <> 'yf:close:NVDA'")
    tamper(staged_run, "series_catalog", f"SELECT * REPLACE (CASE WHEN series_id = 'yf:close:NVDA' "
                                         f"THEN {active_to} ELSE active_to END AS active_to) FROM t")
    e = run_checks(staged_run)
    assert (result(e, "C11")["status"], result(e, "C15")["status"]) == (c11, c15)


def test_c11_ended_series_with_observations_passes_c15(staged_run):
    tamper(staged_run, "series_catalog", "SELECT * REPLACE (CASE WHEN series_id = 'yf:close:NVDA' "
                                         "THEN DATE '2026-10-06' ELSE active_to END AS active_to) FROM t")
    e = run_checks(staged_run)
    assert (result(e, "C11")["status"], result(e, "C15")["status"]) == ("pass", "pass")
