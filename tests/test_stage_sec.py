"""SEC staging (stage_sec.py), on raw landings written straight through write_raw
in the full-index loader's shape: no network, no loader."""

from datetime import date, datetime, timezone

import duckdb
import pandas as pd
import pytest

from pharos.runs import write_raw
from pharos.stage import StageError
from pharos.stage_sec import FOLD, FULL, bind_sessions, rebuild_night, stage_filings

R1, R2, R3 = "20261105T073000Z", "20261106T073000Z", "20261107T073000Z"   # Thursday to Saturday
RUNS = [R1, R2, R3]
SUNDAY = "20261108T073000Z"
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


def staged(runs, base=None, run_id=None):
    con = duckdb.connect()
    bind_sessions(con)
    result = stage_filings(con, runs, run_id or runs[-1], base)
    return Staged(con, result)


class Staged:
    """A staged connection and what stage_filings reported; queries pass through."""

    def __init__(self, con, result):
        self.con, self.result = con, result

    def execute(self, *a):
        return self.con.execute(*a)


def save_base(root, run_id):
    """Stage the runs up to run_id and keep its filing_versions as a staging folder."""
    con = staged([r for r in RUNS if r <= run_id])
    folder = root / "staging" / run_id
    folder.mkdir(parents=True)
    con.execute(f"COPY filing_versions TO '{(folder / 'filing_versions.parquet').as_posix()}' (FORMAT parquet)")
    return folder


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


# --- roll-forward (D125) ---------------------------------------------------------

def table(con, name):
    return sorted(con.execute(f"SELECT * FROM {name}").fetchall(), key=repr)


SCENARIOS = {
    "unchanged": [[index_row(1, A1)], [index_row(1, A1)], [index_row(1, A1)]],
    "changed": [[index_row(1, A1, filed=date(2026, 10, 6))], [index_row(1, A1, filed=date(2026, 10, 7))],
                [index_row(1, A1, filed=date(2026, 10, 7))]],
    # the case that needs every version kept, not only the winners: A, B, back to A
    "changed back": [[index_row(1, A1, filed=date(2026, 10, 6))], [index_row(1, A1, filed=date(2026, 10, 7))],
                     [index_row(1, A1, filed=date(2026, 10, 6))]],
    "removed": [[index_row(1, A1), index_row(2, A2)], [index_row(1, A1)], [index_row(1, A1)]],
    "added": [[index_row(1, A1)], [index_row(1, A1)], [index_row(1, A1), index_row(2, A2)]],
}


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("base_run", [R1, R2])
def test_fold_equals_full_rebuild(root, scenario, base_run):
    for run, rows in zip(RUNS, SCENARIOS[scenario]):
        land(run, rows)
    full = staged(RUNS)
    folded = staged(RUNS, base=save_base(root, base_run))
    assert folded.result.mode == FOLD and folded.result.base_run == base_run
    for name in ("filing_versions", "filings"):
        assert table(folded, name) == table(full, name), name


def test_no_base_rebuilds(root):
    land(R1, [index_row(1, A1)])
    assert staged([R1]).result.mode == FULL
    assert staged([R1], base=root / "staging" / R1).result.mode == FULL   # folder missing


def test_rebuild_night_builds_both_for_comparison(root):
    land(R1, [index_row(1, A1)])
    land(SUNDAY, [index_row(1, A1), index_row(2, A2)])
    con = staged([R1, SUNDAY], base=save_base(root, R1))
    assert con.result.mode == FULL and con.result.compared
    assert table(con, "filing_versions_incremental") == table(con, "filing_versions")


def test_base_outside_the_staged_runs_stops(root):
    land(R1, [index_row(1, A1)])
    land(R2, [index_row(1, A1)])
    base = save_base(root, R2)
    with pytest.raises(StageError, match="not among the runs staged"):
        staged([R1], base=base)


@pytest.mark.parametrize("run_id, sunday", [
    ("20261108T073000Z", True),    # 02:30 New York, Sunday
    ("20261108T033000Z", False),   # 22:30 New York, still Saturday
    ("20261109T043000Z", True),    # 23:30 New York, still Sunday
])
def test_rebuild_night_is_sunday_in_new_york(run_id, sunday):
    assert rebuild_night(run_id) is sunday
