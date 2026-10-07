"""The check runner's controls: a misconfigured check stops discovery, status
follows severity, only an error-severity gate check blocks, a broken check
fails the run without hiding the others, and every evaluation appends its own
results file. Real checks (checks/*.sql) get their own fault tests."""

import duckdb
import pytest

from pharos.checks import CheckError, discover, run_checks


@pytest.fixture
def checks_dir(tmp_path):
    d = tmp_path / "checks"
    d.mkdir()
    return d


def write_check(directory, name, sql, id=None, severity="error", gate="yes", description="test check"):
    head = {"id": id or name.split("_")[0], "severity": severity, "gate": gate, "description": description}
    lines = [f"-- {k}: {v}" for k, v in head.items() if v is not None]
    (directory / f"{name}.sql").write_text("\n".join(lines) + "\n" + sql + "\n", encoding="utf-8")


PASS_SQL = "SELECT * FROM observations WHERE value IS NULL;"
FAIL_SQL = "SELECT series_id, obs_date FROM observations WHERE series_id LIKE 'yf:%'"


# --- discovery -------------------------------------------------------------

def test_empty_folder_stops(checks_dir):
    with pytest.raises(CheckError, match="no checks"):
        discover(checks_dir)


@pytest.mark.parametrize("field", ["id", "severity", "gate", "description"])
def test_missing_header_field_stops(checks_dir, field):
    head = {"id": "C90", "severity": "error", "gate": "yes", "description": "x"}
    del head[field]
    (checks_dir / "C90.sql").write_text("".join(f"-- {k}: {v}\n" for k, v in head.items()) + PASS_SQL,
                                        encoding="utf-8")
    with pytest.raises(CheckError, match=f"missing.*{field}"):
        discover(checks_dir)


@pytest.mark.parametrize("field,value", [("severity", "fatal"), ("gate", "maybe")])
def test_invalid_header_value_stops(checks_dir, field, value):
    write_check(checks_dir, "C90", PASS_SQL, **{field: value})
    with pytest.raises(CheckError, match=field):
        discover(checks_dir)


def test_filename_must_match_id(checks_dir):
    write_check(checks_dir, "C90_dupes", PASS_SQL, id="C91")
    with pytest.raises(CheckError, match="filename"):
        discover(checks_dir)


def test_repeated_id_stops(checks_dir):
    write_check(checks_dir, "C90_a", PASS_SQL)
    write_check(checks_dir, "C90_b", PASS_SQL)
    with pytest.raises(CheckError, match="more than one file"):
        discover(checks_dir)


def test_description_may_contain_colons(checks_dir):
    write_check(checks_dir, "C90", PASS_SQL, description="cutoff: last run older than 26h")
    assert discover(checks_dir)[0].description == "cutoff: last run older than 26h"


# --- evaluation ------------------------------------------------------------

def by_id(evaluation):
    return {r["check_id"]: r for r in evaluation.results}


def test_status_follows_severity_and_counts(staged_run, checks_dir):
    write_check(checks_dir, "C90", PASS_SQL)
    write_check(checks_dir, "C91", FAIL_SQL, severity="warn")
    write_check(checks_dir, "C92", FAIL_SQL, severity="error", gate="no")
    r = by_id(run_checks(staged_run, checks_dir))
    assert (r["C90"]["status"], r["C90"]["failing_row_count"], r["C90"]["sample"]) == ("pass", 0, None)
    assert (r["C91"]["status"], r["C91"]["failing_row_count"]) == ("warn", 2)
    assert r["C92"]["status"] == "error"
    assert '"series_id": "yf:close:NVDA"' in r["C91"]["sample"]


def test_warnings_and_monitor_errors_do_not_block(staged_run, checks_dir):
    write_check(checks_dir, "C91", FAIL_SQL, severity="warn", gate="yes")
    write_check(checks_dir, "C92", FAIL_SQL, severity="error", gate="no")
    assert run_checks(staged_run, checks_dir).verdict == "passed"


def test_gate_error_blocks(staged_run, checks_dir):
    write_check(checks_dir, "C90", PASS_SQL)
    write_check(checks_dir, "C93", FAIL_SQL, severity="error", gate="yes")
    assert run_checks(staged_run, checks_dir).verdict == "blocked"


def test_broken_check_fails_run_and_others_still_run(staged_run, checks_dir):
    write_check(checks_dir, "C90", "SELECT * FROM no_such_table")
    write_check(checks_dir, "C93", FAIL_SQL)
    e = run_checks(staged_run, checks_dir)
    r = by_id(e)
    assert e.verdict == "failed"
    assert r["C90"]["status"] == "broken" and "no_such_table" in r["C90"]["error"]
    assert r["C93"]["status"] == "error"


def test_every_bound_name_is_queryable(staged_run, checks_dir):
    names = ["observations", "series_catalog", "raw_yf_prices", "raw_fred_observations",
             "raw_fred_series", "loads", "watchlist_windows", "fred_expected", "required_series",
             "trading_days", "this_run"]
    for i, name in enumerate(names):
        write_check(checks_dir, f"C{50 + i}", f"SELECT * FROM {name} WHERE false")
    e = run_checks(staged_run, checks_dir)
    assert e.verdict == "passed", [r["error"] for r in e.results if r["error"]]


def test_trading_days_is_xnys(staged_run, checks_dir):
    # 2026-10-05 and -06 are sessions; 2026-10-03 is a Saturday, 2026-11-26 Thanksgiving
    write_check(checks_dir, "C90", """
        SELECT d FROM (VALUES (DATE '2026-10-05'), (DATE '2026-10-06')) t(d)
        WHERE d NOT IN (SELECT session FROM trading_days)
        UNION ALL
        SELECT session FROM trading_days WHERE session IN (DATE '2026-10-03', DATE '2026-11-26')""")
    assert by_id(run_checks(staged_run, checks_dir))["C90"]["status"] == "pass"


def test_unstaged_run_stops(root, staged_run, checks_dir):
    write_check(checks_dir, "C90", PASS_SQL)
    with pytest.raises(CheckError, match="not complete"):
        run_checks("20261008T140000Z", checks_dir)


def test_each_evaluation_appends_a_file(staged_run, checks_dir):
    write_check(checks_dir, "C90", PASS_SQL)
    write_check(checks_dir, "C93", FAIL_SQL)
    first = run_checks(staged_run, checks_dir).path
    second = run_checks(staged_run, checks_dir).path
    assert first != second and first.exists() and second.exists()
    rows = duckdb.sql(f"SELECT check_id, status, failing_row_count FROM read_parquet('{second.as_posix()}') "
                      "ORDER BY check_id").fetchall()
    assert rows == [("C90", "pass", 0), ("C93", "error", 2)]
