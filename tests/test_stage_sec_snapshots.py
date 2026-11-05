"""Company-details and ticker staging (stage_sec.DETAILS, stage_sec.TICKERS), on
raw landings written straight through write_raw in the loaders' shapes."""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from pharos.runs import write_raw
from pharos.stage import StageError
from pharos.stage_sec import DETAILS, TICKERS, Base, connect, stage_table

R1, R2, R3 = "20261105T073000Z", "20261106T073000Z", "20261107T073000Z"
STAMP = datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


def ticker(t="AAA", cik=1, name="A CORP", exchange="NYSE"):
    return {"cik": cik, "name": name, "ticker": t, "exchange": exchange}


def detail(cik=1, name="A CORP", sic="1000", tickers=("AAA",), source=None):
    return {"cik": cik, "entity_type": "operating", "sic": sic, "name": name,
            "tickers": list(tickers), "addresses": "{}", "former_names": "[]", "filing_files": "[]",
            "source_file": source or f"CIK{cik:010d}.json"}


def land(dataset, run_id, rows):
    write_raw(pd.DataFrame(rows), "sec", dataset, run_id, last_modified=STAMP)


def staged(ds, runs, base=None):
    con = connect()
    return con, stage_table(con, ds, runs, base)


def rows(con, table, cols):
    return con.execute(f"SELECT {cols} FROM {table} ORDER BY ALL").fetchall()


def test_ticker_dates_are_the_run_date(root):
    land("tickers", R1, [ticker()])
    con, _ = staged(TICKERS, [R1])
    assert rows(con, "tickers", "ticker, obs_date, available_date, vintage, run_id") == [
        ("AAA", date(2026, 11, 5), date(2026, 11, 5), date(2026, 11, 5), R1)]


def test_unchanged_ticker_keeps_its_first_run_and_dates(root):
    land("tickers", R1, [ticker()])
    land("tickers", R2, [ticker()])
    con, _ = staged(TICKERS, [R1, R2])
    assert rows(con, "tickers", "obs_date, run_id") == [(date(2026, 11, 5), R1)]


def test_ticker_moving_to_another_cik_takes_the_newest(root):
    land("tickers", R1, [ticker(cik=1)])
    land("tickers", R2, [ticker(cik=2)])
    con, _ = staged(TICKERS, [R1, R2])
    assert rows(con, "tickers", "cik, obs_date, run_id") == [(2, date(2026, 11, 6), R2)]


def test_ticker_dropped_from_the_file_is_kept(root):
    # newest value per key: a key missing from later landings stays (D84, D132)
    land("tickers", R1, [ticker("AAA"), ticker("BBB", cik=2)])
    land("tickers", R2, [ticker("AAA")])
    con, _ = staged(TICKERS, [R1, R2])
    assert rows(con, "tickers", "ticker, run_id") == [("AAA", R1), ("BBB", R1)]


def test_ticker_twice_in_one_landing_stops(root):
    land("tickers", R1, [ticker(cik=1), ticker(cik=2)])
    with pytest.raises(StageError, match="different values inside one landing"):
        staged(TICKERS, [R1])


def test_detail_change_in_a_list_is_a_change(root):
    land("submission_details", R1, [detail(tickers=("AAA",))])
    land("submission_details", R2, [detail(tickers=("AAA", "AAB"))])
    con, _ = staged(DETAILS, [R1, R2])
    assert rows(con, "submission_details", "tickers, run_id") == [(["AAA", "AAB"], R2)]


def test_details_merge_into_base_equals_reading_every_landing(root):
    for run, sic in zip((R1, R2, R3), ("1000", "2000", "2000")):
        land("submission_details", run, [detail(sic=sic), detail(cik=2)])
    con, _ = staged(DETAILS, [R1, R2])
    folder = root / "serving" / R2
    folder.mkdir(parents=True)
    con.execute(f"COPY submission_details TO '{(folder / 'submission_details.parquet').as_posix()}' (FORMAT parquet)")
    full, _ = staged(DETAILS, [R1, R2, R3])
    merged, result = staged(DETAILS, [R1, R2, R3], base=Base(R2, folder))
    assert result.runs_read == (R3,)
    everything = "SELECT * FROM submission_details ORDER BY cik"
    assert merged.execute(everything).fetchall() == full.execute(everything).fetchall()
    assert rows(full, "submission_details", "cik, sic, run_id") == [(1, "2000", R2), (2, "1000", R1)]


# --- the group's stager (stage.STAGERS["sec"]) -----------------------------------

def land_all(run_id):
    from datetime import date as d
    land("full_index", run_id, [{"cik": 1, "company_name": "A CORP", "form_type": "10-K",
                                 "date_filed": d(2026, 10, 6), "filename": "edgar/data/1/0000000001-26-000001.txt",
                                 "quarter": "2026Q4", "last_modified": STAMP}])
    land("submission_filings", run_id, [{
        "cik": 1, "accession_number": "0000000001-26-000001", "filing_date": "2026-10-06", "report_date": "",
        "acceptance_date_time": "2026-10-06T20:15:00.000Z", "act": "34", "form": "10-K", "file_number": "",
        "film_number": "", "items": "", "core_type": "10-K", "size": 1, "is_xbrl": 1, "is_inline_xbrl": 1,
        "is_xbrl_numeric": 1, "primary_document": "a.htm", "primary_doc_description": "", "source_file": "CIK0000000001.json"}])
    land("submission_details", run_id, [detail()])
    land("tickers", run_id, [ticker()])


def version_holding_sec(root, version_run, source_run, outcome="published"):
    """A serving version whose groups.parquet says the SEC group came from source_run."""
    import duckdb
    folder = root / "serving" / version_run
    folder.mkdir(parents=True)
    duckdb.execute(f"""COPY (SELECT 'sec' AS "group", '{outcome}' AS outcome, '{source_run}' AS source_run,
                                    NULL::VARCHAR AS reason)
                       TO '{(folder / 'groups.parquet').as_posix()}' (FORMAT parquet)""")
    (root / "serving" / "CURRENT").write_text(version_run, encoding="utf-8")
    return folder


def test_stage_group_writes_the_four_tables(root):
    from pharos.groups import SEC
    from pharos.stage_sec import stage_group
    land_all(R1)
    target = root / "staging" / R1
    target.mkdir(parents=True)
    merged = stage_group([R1], target)
    assert sorted(p.stem for p in target.glob("*.parquet")) == sorted(SEC.tables)
    assert all(m.base_run is None for m in merged.values())   # no serving yet


def test_current_base_follows_the_groups_source_run(root):
    from pharos.stage_sec import current_base
    assert current_base() is None                                       # nothing published
    folder = version_holding_sec(root, R2, R1, outcome="carried_forward")
    assert current_base() == Base(R1, folder)


def test_current_base_none_when_sec_never_published(root):
    from pharos.stage_sec import current_base
    version_holding_sec(root, R1, "None", outcome="never_published")
    assert current_base() is None


def test_stage_group_merges_into_the_live_version(root):
    from pharos.stage_sec import stage_group
    land_all(R1)
    first = root / "staging" / R1
    first.mkdir(parents=True)
    stage_group([R1], first)
    version = version_holding_sec(root, R1, R1)
    for f in first.glob("*.parquet"):
        (version / f.name).write_bytes(f.read_bytes())
    land_all(R2)
    second = root / "staging" / R2
    second.mkdir(parents=True)
    merged = stage_group([R1, R2], second)
    assert {m.base_run for m in merged.values()} == {R1}
    assert {m.runs_read for m in merged.values()} == {(R2,)}


def test_sec_check_binder_exposes_its_tables_and_loads(root):
    import duckdb
    from pharos.checks import BINDERS
    from pharos.stage_sec import stage_group
    land_all(R1)
    target = root / "staging" / R1
    target.mkdir(parents=True)
    stage_group([R1], target)
    con = duckdb.connect()
    BINDERS["sec"](con, R1)
    assert con.execute("SELECT count(*) FROM tickers").fetchone() == (1,)
    assert con.execute("SELECT count(DISTINCT dataset) FROM loads").fetchone() == (4,)
    assert con.execute("SELECT run_id FROM this_run").fetchone() == (R1,)
