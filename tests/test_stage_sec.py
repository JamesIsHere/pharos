"""SEC staging (stage_sec.py), on raw landings written straight through write_raw
in the full-index loader's shape: no network, no loader."""

from datetime import date, datetime, timezone

import duckdb
import pandas as pd
import pytest

from pharos.runs import write_raw
from pharos.stage import StageError
from pharos.stage_sec import bind_sessions, stage_filings

R1, R2, R3 = "20261105T073000Z", "20261106T073000Z", "20261107T073000Z"
STAMP = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


def index_row(cik, accession, form="10-K", name=None, filed=date(2026, 10, 6), quarter="2026Q4",
              filename=None):
    return {"cik": cik, "company_name": name if name is not None else f"COMPANY {cik}",
            "form_type": form, "date_filed": filed,
            "filename": filename or f"edgar/data/{cik}/{accession}.txt", "quarter": quarter,
            "last_modified": STAMP}


def land(run_id, rows):
    write_raw(pd.DataFrame(rows), "sec", "full_index", run_id, last_modified=STAMP)


def staged(runs):
    con = duckdb.connect()
    bind_sessions(con)
    stage_filings(con, runs)
    return con


def filings(con, cols="cik, accession, form, name, date_filed, run_id"):
    return con.execute(f"SELECT {cols} FROM filings ORDER BY cik, accession, form").fetchall()


A1, A2 = "0000000001-26-000001", "0000000002-26-000002"


def test_accession_and_dates(root):
    # Friday 2026-10-09: the next session is Monday 2026-10-12 (Columbus Day is a trading day)
    land(R1, [index_row(1, A1, filed=date(2026, 10, 9))])
    con = staged([R1])
    assert filings(con, "accession, obs_date, available_date, vintage, quarter") == [
        (A1, date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 9), "2026Q4")]


def test_available_date_skips_a_holiday(root):
    # Wednesday 2026-11-25: Thursday is Thanksgiving, so the next session is Friday
    land(R1, [index_row(1, A1, filed=date(2026, 11, 25))])
    assert filings(staged([R1]), "available_date") == [(date(2026, 11, 27),)]


def test_identical_relanding_keeps_first_run(root):
    rows = [index_row(1, A1), index_row(2, A2)]
    land(R1, rows)
    land(R2, rows)
    con = staged([R1, R2])
    assert [r[-1] for r in filings(con)] == [R1, R1]


def test_changed_value_takes_newest_landing(root):
    land(R1, [index_row(1, A1, filed=date(2026, 10, 6))])
    land(R2, [index_row(1, A1, filed=date(2026, 10, 7))])
    land(R3, [index_row(1, A1, filed=date(2026, 10, 7))])
    assert filings(staged([R1, R2, R3]), "date_filed, run_id") == [(date(2026, 10, 7), R2)]


def test_value_changed_back_takes_newest_landing(root):
    land(R1, [index_row(1, A1, filed=date(2026, 10, 6))])
    land(R2, [index_row(1, A1, filed=date(2026, 10, 7))])
    land(R3, [index_row(1, A1, filed=date(2026, 10, 6))])
    assert filings(staged([R1, R2, R3]), "date_filed") == [(date(2026, 10, 6),)]


def test_row_missing_from_later_landing_is_kept(root):
    # C37 (step 3) reports it; staging keeps what SEC once said (D84)
    land(R1, [index_row(1, A1), index_row(2, A2)])
    land(R2, [index_row(1, A1)])
    assert [r[1] for r in filings(staged([R1, R2]))] == [A1, A2]


def test_key_is_cik_accession_form_name(root):
    # D61: one accession under two CIKs, two forms, and two names are four rows
    land(R1, [index_row(1, A1), index_row(2, A1, filename=f"edgar/data/2/{A1}.txt"),
              index_row(1, A1, form="SC TO-T"), index_row(1, A1, name="OLD NAME")])
    assert len(filings(staged([R1]))) == 4


def test_two_values_in_one_landing_both_survive(root):
    # staging never picks between two things one landing says: C23 blocks
    land(R1, [index_row(1, A1, filed=date(2026, 10, 6)), index_row(1, A1, filed=date(2026, 10, 7))])
    assert len(filings(staged([R1]))) == 2


def test_empty_name_is_a_key_like_any_other(root):
    # the index has two rows with an empty company name (spike, 2026-11-04)
    rows = [{**index_row(1, A1), "company_name": None}]
    land(R1, rows)
    land(R2, rows)
    assert filings(staged([R1, R2]), "name, run_id") == [(None, R1)]


def test_only_included_runs_are_read(root):
    land(R1, [index_row(1, A1)])
    land(R2, [index_row(2, A2)])
    assert [r[0] for r in filings(staged([R1]))] == [1]


@pytest.mark.parametrize("filename, why", [
    ("edgar/data/1/0000000001-26-000001.htm", "not edgar/data/<cik>/<accession>.txt"),
    ("edgar/data/1/000000001-26-000001.txt", "not edgar/data/<cik>/<accession>.txt"),
    ("edgar/data/9/0000000001-26-000001.txt", "names another CIK"),
])
def test_malformed_filename_stops(root, filename, why):
    land(R1, [index_row(1, A1, filename=filename)])
    with pytest.raises(StageError, match=why):
        staged([R1])


def test_missing_filing_date_stops(root):
    # a dated row beside it keeps the column a DATE in the raw file, as the loader writes it
    land(R1, [index_row(1, A1, filed=None), index_row(2, A2)])
    with pytest.raises(StageError, match="1 index rows with no filing date"):
        staged([R1])


def test_no_raw_stops(root):
    with pytest.raises(StageError, match="no raw files for sec/full_index"):
        staged([R1])
