"""Fault injection: each real check in checks/ must fire on the fault it exists
to catch. A clean staged run passes first, so a check that fires on clean data
can't hide behind its fault test. Faults are injected into the staged tables
of a tiny synthetic run (conftest.py), never into real data."""

import duckdb
import pytest

from pharos.checks import run_checks
from pharos.paths import data_root


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
