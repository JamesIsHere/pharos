"""Filing-history staging (stage_sec.SUBMISSIONS), on raw landings
written straight through write_raw in the submissions loader's shape."""

from datetime import date, datetime, timezone

import duckdb
import pandas as pd
import pytest

from pharos.runs import write_raw
from pharos.stage import StageError
from pharos.stage_sec import SUBMISSIONS, Base, bind_sessions, connect, stage_table

R1, R2, R3 = "20261105T073000Z", "20261106T073000Z", "20261107T073000Z"   # Thursday to Saturday
RUNS = [R1, R2, R3]
A1, A2 = "0000000001-26-000001", "0000000002-26-000002"
MAIN, OVERFLOW = "CIK0000000001.json", "CIK0000000001-submissions-001.json"


@pytest.fixture
def root(monkeypatch, tmp_path):
    monkeypatch.setenv("PHAROS_TEST_MODE", "1")
    monkeypatch.setenv("PHAROS_DATA_ROOT", str(tmp_path))
    return tmp_path


def filing(cik=1, accession=A1, filed="2026-10-06", report="2026-09-30",
           accepted="2026-10-06T20:15:00.000Z", items="", source=MAIN, xbrl=1):
    return {"cik": cik, "accession_number": accession, "filing_date": filed, "report_date": report,
            "acceptance_date_time": accepted, "act": "34", "form": "10-K", "file_number": "001-00001",
            "film_number": "26000001", "items": items, "core_type": "10-K", "size": 1000,
            "is_xbrl": xbrl, "is_inline_xbrl": xbrl, "is_xbrl_numeric": xbrl,
            "primary_document": "doc.htm", "primary_doc_description": "10-K", "source_file": source}


def land(run_id, rows):
    write_raw(pd.DataFrame(rows), "sec", "submission_filings", run_id,
              last_modified=datetime(2026, 11, 4, 6, 21, tzinfo=timezone.utc))


class Staged:
    def __init__(self, con, result):
        self.con, self.result = con, result

    def execute(self, *a):
        return self.con.execute(*a)


def staged(runs, base=None):
    con = connect()
    bind_sessions(con)
    return Staged(con, stage_table(con, SUBMISSIONS, runs, base))


def rows(con, cols="cik, accession, acceptance, acceptance_conflict, source_file, run_id"):
    return con.execute(f"SELECT {cols} FROM submission_filings ORDER BY ALL").fetchall()


def test_casts_and_dates(root):
    land(R1, [filing(filed="2026-10-09", report="")])     # a Friday: next session Monday
    assert rows(staged([R1]), "filing_date, report_date, acceptance, obs_date, available_date, vintage") == [
        (date(2026, 10, 9), None, datetime(2026, 10, 6, 20, 15, tzinfo=timezone.utc),
         date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 9))]


def test_filing_before_1993_has_an_available_date(root):
    # submissions.zip lists 9 filings before 1993, the first 1980-01-02 (2026-11-04)
    land(R1, [filing(filed="1980-01-02", report="", accepted="1980-01-02T12:00:00.000Z")])
    assert rows(staged([R1]), "available_date") == [(date(1980, 1, 3),)]


def test_identical_copies_in_one_landing_are_one_row(root):
    land(R1, [filing(source=OVERFLOW), filing(source=MAIN)])
    # lineage keeps the first file name in sort order: '-' sorts before '.'
    assert rows(staged([R1])) == [
        (1, A1, datetime(2026, 10, 6, 20, 15, tzinfo=timezone.utc), False, OVERFLOW, R1)]


def test_copies_differing_only_in_acceptance_collapse_with_a_flag(root):
    # D126: one copy is New York time stamped Z; nothing says which
    land(R1, [filing(accepted="2026-10-06T20:15:00.000Z", source=MAIN),
              filing(accepted="2026-10-07T00:15:00.000Z", source=OVERFLOW)])
    assert rows(staged([R1])) == [(1, A1, None, True, OVERFLOW, R1)]


def test_copies_differing_in_more_than_acceptance_stop(root):
    # staging never picks between two things one landing says (D132)
    land(R1, [filing(accepted="2026-10-06T20:15:00.000Z", items="", source=MAIN),
              filing(accepted="2026-10-07T00:15:00.000Z", items="2.02", source=OVERFLOW)])
    with pytest.raises(StageError, match="different values inside one landing"):
        staged([R1])


def test_a_filing_moving_between_zip_files_is_not_a_change(root):
    land(R1, [filing(source=MAIN)])
    land(R2, [filing(source=OVERFLOW)])
    assert rows(staged([R1, R2]), "source_file, run_id") == [(MAIN, R1)]


def test_changed_value_takes_newest_landing(root):
    land(R1, [filing(xbrl=0)])
    land(R2, [filing(xbrl=1)])
    assert rows(staged([R1, R2]), "is_xbrl, run_id") == [(1, R2)]


def test_accession_off_shape_stops(root):
    land(R1, [filing(accession="0000000001-26-1")])
    with pytest.raises(StageError, match="accession is not"):
        staged([R1])


def test_filing_date_off_shape_fails_loudly(root):
    land(R1, [filing(filed="10/06/2026")])
    with pytest.raises(duckdb.ConversionException):
        staged([R1])


def test_no_raw_stops(root):
    with pytest.raises(StageError, match="no raw files for sec/submission_filings"):
        staged([R1])


# --- merge (D132) on the filing history -----------------------------------------

def table(con):
    return sorted(con.execute("SELECT * FROM submission_filings").fetchall(), key=repr)


def save_base(root, run_id):
    con = staged([r for r in RUNS if r <= run_id])
    folder = root / "serving" / run_id
    folder.mkdir(parents=True)
    con.execute(f"COPY submission_filings TO '{(folder / 'submission_filings.parquet').as_posix()}' (FORMAT parquet)")
    return Base(run_id, folder)


CONFLICT = [filing(accepted="2026-10-06T20:15:00.000Z", source=MAIN),
            filing(accepted="2026-10-07T00:15:00.000Z", source=OVERFLOW)]
SCENARIOS = {
    "conflict, then resolved": [CONFLICT, [filing()], [filing()]],
    "resolved, then conflict": [[filing()], CONFLICT, CONFLICT],
    "conflict, resolved, conflict again": [CONFLICT, [filing()], CONFLICT],
    "moves file, then changes": [[filing(source=MAIN)], [filing(source=OVERFLOW)],
                                 [filing(source=OVERFLOW, xbrl=0), filing(cik=2, accession=A2)]],
}


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("base_run", [R1, R2])
def test_merge_into_base_equals_reading_every_landing(root, scenario, base_run):
    for run, landing in zip(RUNS, SCENARIOS[scenario]):
        land(run, landing)
    full = staged(RUNS)
    merged = staged(RUNS, base=save_base(root, base_run))
    assert merged.result.base_run == base_run
    assert table(merged) == table(full)
