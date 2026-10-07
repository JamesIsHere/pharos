"""Fault injection: each real check in checks/ must fire on the fault it exists
to catch. A clean staged run passes first, so a check that fires on clean data
can't hide behind its fault test. Faults are injected into the staged tables
of a tiny synthetic run (conftest.py), never into real data."""

import json
from datetime import date

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
    """Land run_to as an identical copy of run_from through the real writer.
    .df() turns DATE into TIMESTAMP, which C08 rightly blocks, so DATE columns go back to dates."""
    for source, dataset in [("yf", "prices"), ("fred", "observations"), ("fred", "series")]:
        rel = duckdb.sql(f"SELECT * EXCLUDE (run_id, loaded_at) FROM "
                         f"read_parquet('{raw_file(run_from, source, dataset).as_posix()}')")
        frame = rel.df()
        for column, kind in zip(rel.columns, rel.types):
            if str(kind) == "DATE":
                frame[column] = frame[column].dt.date
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


GDP_KEY = "series_id = 'fred:GDP'"


@pytest.mark.parametrize("rows, status", [
    (f"SELECT * REPLACE (value * 1.01 AS value) FROM t WHERE {NVDA_DAY}", "error"),
    (f"SELECT * REPLACE (value * 1.01 AS value) FROM t WHERE {GDP_KEY}", "error"),
    (f"SELECT * REPLACE (NULL AS value) FROM t WHERE {GDP_KEY}", "error"),        # withdrawn vs number
    # conflict in a superseded price vintage: already reviewed and re-pulled (D23)
    (f"SELECT * REPLACE (vintage - 30 AS vintage) FROM t WHERE {NVDA_DAY} UNION ALL "
     f"SELECT * REPLACE (vintage - 30 AS vintage, value * 1.01 AS value) FROM t WHERE {NVDA_DAY}", "pass"),
])
def test_c14_same_key_different_value(staged_run, rows, status):
    tamper(staged_run, "observations", f"SELECT * FROM t UNION ALL {rows}")
    r = result(run_checks(staged_run), "C14")
    assert r["status"] == status
    if status == "error":
        assert r["failing_row_count"] == 1


def manifest_row(run_id, status, published_at):
    path = data_root() / "health" / "run_manifest" / f"{run_id}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    duckdb.execute(f"COPY (SELECT ?::VARCHAR AS run_id, ?::VARCHAR AS status, ?::TIMESTAMPTZ AS published_at) "
                   f"TO '{path.as_posix()}' (FORMAT parquet)", [run_id, status, published_at])


# the fixture run is 2026-10-07 14:00 UTC
@pytest.mark.parametrize("manifests, status", [
    ([], "error"),                                                              # never published
    ([("20261006T150000Z", "published", "2026-10-06 15:00:00+00")], "pass"),       # 23h
    ([("20261006T110000Z", "published", "2026-10-06 11:00:00+00")], "error"),      # 27h
    # a blocked run doesn't reset the clock (D32)
    ([("20261006T110000Z", "published", "2026-10-06 11:00:00+00"),
      ("20261007T130000Z", "blocked", None)], "error"),
])
def test_c01_last_publish_age(staged_run, manifests, status):
    for m in manifests:
        manifest_row(*m)
    e = run_checks(staged_run)
    assert result(e, "C01")["status"] == status
    assert e.verdict == "passed"   # monitor: never blocks


def test_c01_evaluated_at_view_time(staged_run):
    from datetime import datetime, timezone
    manifest_row("20261007T130000Z", "published", "2026-10-07 13:00:00+00")
    assert result(run_checks(staged_run), "C01")["status"] == "pass"
    later = datetime(2026, 10, 8, 16, 0, tzinfo=timezone.utc)   # 27h after the publish
    assert result(run_checks(staged_run, now=later), "C01")["status"] == "error"


def freshness(evaluation):
    """(C02 status, C17 status, series ids each reports)."""
    out = []
    for cid in ("C02", "C17"):
        r = result(evaluation, cid)
        assert r["error"] is None, r["error"]
        ids = sorted({row["series_id"] for row in json.loads(r["sample"])}) if r["sample"] else []
        out += [r["status"], ids]
    return tuple(out)


def at(day, hour=12):
    from datetime import datetime, timezone
    return datetime(2026, *day, hour, tzinfo=timezone.utc)


# fixture: NVDA closes 2026-10-05 (Mon) and 10-06 (Tue); GDP 2026-04-01 (Q2), lag 35
@pytest.mark.parametrize("now, expected", [
    (None,         ("pass", [], "pass", [])),                          # run 10-07: due 10-06, GDP Q2
    (at((10, 8)),  ("warn", ["yf:close:NVDA"], "pass", [])),          # 10-07 due: 1 session
    (at((10, 9)),  ("pass", [], "error", ["yf:close:NVDA"])),         # 10-07, 10-08: 2 sessions
    (at((11, 4)),  ("pass", [], "error", ["yf:close:NVDA"])),         # GDP: Q3 due from 11-05
    (at((11, 5)),  ("warn", ["fred:GDP"], "error", ["yf:close:NVDA"])),  # Sep 30 + 35 = Nov 4 < Nov 5
])
def test_c02_c17_periods_behind(staged_run, now, expected):
    e = run_checks(staged_run, now=now)
    assert freshness(e) == expected
    assert e.verdict == "passed"   # monitors never block


def test_c02_weekend_is_not_late(staged_run):
    # latest close Friday 10-02; on Monday 10-05 the only due session is Friday's
    tamper(staged_run, "observations", "SELECT * REPLACE (CASE WHEN series_id = 'yf:close:NVDA' "
                                       "THEN obs_date - 4 ELSE obs_date END AS obs_date) FROM t")
    assert freshness(run_checks(staged_run, now=at((10, 5))))[0::2] == ("pass", "pass")
    assert freshness(run_checks(staged_run, now=at((10, 6))))[0] == "warn"   # Monday's close now due


def test_c17_unknown_frequency_fails_loud(staged_run):
    tamper(staged_run, "series_catalog", "SELECT * REPLACE (CASE WHEN series_id = 'fred:GDP' THEN 'W' "
                                         "ELSE frequency END AS frequency) FROM t")
    assert freshness(run_checks(staged_run))[2:] == ("error", ["fred:GDP"])


def test_c02_ended_series_is_out_of_scope(staged_run):
    tamper(staged_run, "series_catalog", "SELECT * REPLACE (CASE WHEN series_id = 'yf:close:NVDA' "
                                         "THEN DATE '2026-10-06' ELSE active_to END AS active_to) FROM t")
    assert freshness(run_checks(staged_run, now=at((10, 9))))[2:] == ("pass", [])


def rewrite_tiingo(run_id, select):
    """Rewrite this run's Tiingo raw file as `select` over its rows (named t).
    C05 also fires (the file no longer matches its load record); only C18-C20 are asserted."""
    path = raw_file(run_id, "tiingo", "prices")
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{path.as_posix()}')")
    con.execute(f"COPY ({select}) TO '{path.as_posix()}' (FORMAT parquet)")
    con.close()


def test_c18_c19_c20_pass_on_clean_run(staged_run):
    e = run_checks(staged_run)
    assert [result(e, c)["status"] for c in ("C18", "C19", "C20")] == ["pass", "pass", "pass"]


def test_c18_missing_reference_file_is_red(staged_run):
    raw_file(staged_run, "tiingo", "prices").unlink()
    r = result(run_checks(staged_run), "C18")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)
    assert "NVDA" in r["sample"]


def test_c18_ended_ticker_is_not_expected(staged_run, monkeypatch):
    raw_file(staged_run, "tiingo", "prices").unlink()
    from pharos import config
    monkeypatch.setattr(config, "watchlist", lambda: [
        {"ticker": "NVDA", "yahoo_symbol": "NVDA", "active_from": date(2026, 10, 5), "active_to": date(2026, 10, 6)}])
    assert result(run_checks(staged_run), "C18")["status"] == "pass"


def test_c19_duplicate_reference_day_is_red(staged_run):
    rewrite_tiingo(staged_run, "SELECT * FROM t UNION ALL (SELECT * FROM t LIMIT 1)")
    r = result(run_checks(staged_run), "C19")
    assert (r["status"], r["failing_row_count"]) == ("error", 1)


def test_c20_reference_behind_due_session_warns(staged_run):
    from datetime import datetime, timezone
    later = datetime(2026, 10, 8, 12, tzinfo=timezone.utc)      # 10-07 is due, Tiingo ends 10-06
    r = result(run_checks(staged_run, now=later), "C20")
    assert (r["status"], r["failing_row_count"]) == ("warn", 1)


def test_c12_agreeing_sources_pass(staged_run):
    assert result(run_checks(staged_run), "C12")["status"] == "pass"


@pytest.mark.parametrize("factor, fires", [(1.006, True), (0.994, True), (1.004, False)])
def test_c12_tolerance(staged_run, factor, fires):
    rewrite_tiingo(staged_run, f"SELECT * REPLACE (close * {factor} AS close) FROM t")
    r = result(run_checks(staged_run), "C12")
    assert (r["status"], r["failing_row_count"]) == (("warn", 2) if fires else ("pass", 0))


def test_c12_uses_tiingo_own_split_factor(staged_run):
    # Tiingo raw: 20.0 the day before a 2-for-1 effective 10-06 (factor 2.0), then 11.0.
    # Split-adjusted that is 10.0, matching Yahoo's adjusted 10.0: no warning.
    rewrite_tiingo(staged_run, "SELECT * REPLACE (CASE WHEN obs_date = DATE '2026-10-05' THEN 20.0 ELSE close END AS close, "
                               "CASE WHEN obs_date = DATE '2026-10-06' THEN 2.0 ELSE split_factor END AS split_factor) FROM t")
    assert result(run_checks(staged_run), "C12")["status"] == "pass"
    # the same raw close without the factor is an unadjusted split: 100% off
    rewrite_tiingo(staged_run, "SELECT * REPLACE (1.0 AS split_factor) FROM t")
    r = result(run_checks(staged_run), "C12")
    assert (r["status"], r["failing_row_count"]) == ("warn", 1)


def test_reconcile_sample_is_seeded_by_run_id(staged_run):
    """30 common dates: 5 random + the latest, same draw for the same run_id,
    a different draw for another."""
    import duckdb as _duckdb
    from pharos.checks import bind
    days = [d.date() for d in __import__("exchange_calendars").get_calendar("XNYS")
            .sessions_in_range("2026-08-24", "2026-10-06")][-30:]
    tamper(staged_run, "observations", "SELECT * FROM t WHERE series_id <> 'yf:close:NVDA' UNION ALL "
           "SELECT t.* REPLACE (d.day AS obs_date, d.day AS available_date) FROM t, "
           f"(SELECT unnest([{', '.join(repr(str(d)) for d in days)}]::DATE[]) AS day) AS d "
           "WHERE t.series_id = 'yf:close:NVDA' AND t.obs_date = DATE '2026-10-06'")
    rewrite_tiingo(staged_run, "SELECT t.* REPLACE (d.day AS obs_date, 11.0 AS close) FROM t, "
                   f"(SELECT unnest([{', '.join(repr(str(d)) for d in days)}]::DATE[]) AS day) AS d "
                   "WHERE t.obs_date = DATE '2026-10-06'")

    def draw(run_id):
        con = _duckdb.connect()
        bind(con, staged_run)
        con.execute("UPDATE this_run SET run_id = ?", [run_id])
        rows = con.execute("SELECT obs_date, pick FROM reconcile_sample ORDER BY obs_date").fetchall()
        con.close()
        return rows
    a, b = draw(staged_run), draw("20261008T140000Z")
    assert len(a) == 6 and sum(p == "latest" for _, p in a) == 1 and a[-1] == (days[-1], "latest")
    assert draw(staged_run) == a and a != b


def test_unbuildable_sample_makes_c12_broken_not_a_crash(staged_run):
    tamper(staged_run, "observations", "SELECT * EXCLUDE (value) FROM t")
    e = run_checks(staged_run)                       # returns: no exception
    r = result(e, "C12")
    assert r["status"] == "broken" and '"value" not found' in r["error"]   # the real cause, verbatim
    assert e.verdict == "failed"


def test_c12_no_common_dates_warns(staged_run):
    # every Tiingo date shifted forward a day (10-05 -> 10-06 still overlaps, so
    # keep only the shifted 10-07): nothing to compare must not pass silently
    rewrite_tiingo(staged_run, "SELECT * REPLACE (obs_date + 1 AS obs_date) FROM t WHERE obs_date = DATE '2026-10-06'")
    r = result(run_checks(staged_run), "C12")
    assert (r["status"], r["failing_row_count"]) == ("warn", 1) and "no common dates" in r["sample"]
