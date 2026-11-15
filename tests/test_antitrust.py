"""The antitrust group (D175): the loader reads files placed by hand in the data
root, and staging asserts each file's own arithmetic. No test here reaches a network."""

from datetime import date

import duckdb
import pytest

from pharos import config
from pharos.loaders import antitrust
from pharos.stage import StageError
from pharos.stage_landed import DATASETS, stage_table

RUN_A, RUN_B = "20261115T120000Z", "20261116T120000Z"

FILES = {
    "hsr_by_fiscal_year.csv": (
        "FY,TransactionsReported,FilingsReceived,AdjustedTransactions,SecondRequestTotal,SecondRequestFTC,"
        "SecondRequestDOJ,EarlyTerminationTransactions,EarlyTerminationTransactionsGranted,"
        "EarlyTerminationTransactionsNotGranted\n"
        "1999,4642,9151,4340,111,45,68,4110,3103,1007\n"    # FTC + DOJ = 113: the named exception
        "2025,2006,3851,1944,41,20,21,911,265,646\n"),
    "ftc_merger_actions.csv": "FY,consents,injunctions,part3,abandoned,total\n2025,3,3,0,2,8\n",
    "doj_merger_outcomes.csv": (
        "FY,complaints,litigation_complaints,simultaneous_settlement,abandoned,resolved_concerns,merger_outcomes\n"
        "2024,0,0,0,12,2,14\n"),
    "ftc_hmid_hhi.csv": (
        "edition_end_fy,table,markets,post_merger_hhi,delta_hhi,enforced,closed\n"
        '2011,3.1,All Markets,"0 - 1,799",0 - 99,0,14\n'),
    "ftc_hmid_competitors.csv": (
        "edition_end_fy,table,markets,significant_competitors,enforced,closed\n"
        "2011,4.1,All Markets,2 to 1,297,6\n"),
}


@pytest.fixture
def placed(root, monkeypatch):
    """The five files in <root>/manual/antitrust/, each dated 2026-11-15 in sources.yaml."""
    folder = root / "manual" / "antitrust"
    folder.mkdir(parents=True)
    for name, text in FILES.items():
        (folder / name).write_text(text, encoding="utf-8")
    dates = {name: {"retrieved": date(2026, 11, 15)} for name in FILES}
    monkeypatch.setattr(config, "sources", lambda: {"antitrust": {"files": dates}})
    return folder, dates


def staged(table, runs):
    ds = next(d for d in DATASETS["antitrust"] if d.table == table)
    con = duckdb.connect()
    stage_table(con, ds, runs)
    return con


def test_load_lands_every_file_and_stage_dates_it(placed):
    assert antitrust.load(RUN_A) == {"hsr_by_fiscal_year.csv": 2, "ftc_merger_actions.csv": 1,
                                     "doj_merger_outcomes.csv": 1, "ftc_hmid_hhi.csv": 1,
                                     "ftc_hmid_competitors.csv": 1}
    rows = staged("hsr_fiscal_year", [RUN_A]).execute(
        "SELECT fiscal_year, second_requests, obs_date, available_date, vintage FROM hsr_fiscal_year ORDER BY 1"
    ).fetchall()
    assert rows == [(1999, 111, date(1999, 9, 30), date(2026, 11, 15), date(2026, 11, 15)),
                    (2025, 41, date(2025, 9, 30), date(2026, 11, 15), date(2026, 11, 15))]
    cell = staged("ftc_hmid_hhi", [RUN_A]).execute(
        "SELECT edition_end_fy, table_no, post_merger_hhi, enforced, closed, obs_date FROM ftc_hmid_hhi").fetchall()
    assert cell == [(2011, "3.1", "0 - 1,799", 0, 14, date(2011, 9, 30))]


def test_landing_the_same_files_twice_changes_nothing(placed):
    antitrust.load(RUN_A)
    antitrust.load(RUN_B)
    runs = staged("ftc_merger_actions", [RUN_A, RUN_B]).execute(
        "SELECT fiscal_year, total, run_id FROM ftc_merger_actions").fetchall()
    assert runs == [(2025, 8, RUN_A)]


def test_a_redated_file_is_a_new_vintage(placed):
    folder, dates = placed
    antitrust.load(RUN_A)
    dates["ftc_merger_actions.csv"]["retrieved"] = date(2027, 11, 20)
    (folder / "ftc_merger_actions.csv").write_text(
        "FY,consents,injunctions,part3,abandoned,total\n2025,3,3,0,3,9\n", encoding="utf-8")
    antitrust.load(RUN_B)
    rows = staged("ftc_merger_actions", [RUN_A, RUN_B]).execute(
        "SELECT total, vintage FROM ftc_merger_actions ORDER BY vintage").fetchall()
    assert rows == [(8, date(2026, 11, 15)), (9, date(2027, 11, 20))]


def test_a_row_that_does_not_add_up_stops_the_group(placed):
    folder, _ = placed
    (folder / "ftc_merger_actions.csv").write_text(
        "FY,consents,injunctions,part3,abandoned,total\n2025,3,3,0,2,9\n", encoding="utf-8")
    antitrust.load(RUN_A)
    with pytest.raises(StageError, match="do not add to the total"):
        staged("ftc_merger_actions", [RUN_A])


def test_a_changed_header_or_a_missing_file_lands_nothing(placed, root):
    folder, _ = placed
    (folder / "doj_merger_outcomes.csv").write_text("FY,complaints\n2024,0\n", encoding="utf-8")
    with pytest.raises(antitrust.SchemaDriftError, match="columns"):
        antitrust.load(RUN_A)
    (folder / "doj_merger_outcomes.csv").unlink()
    with pytest.raises(antitrust.SchemaDriftError, match="not on disk"):
        antitrust.load(RUN_A)
    assert not (root / "raw").exists()


def test_sources_yaml_must_name_exactly_the_files(placed, monkeypatch):
    monkeypatch.setattr(config, "sources", lambda: {"antitrust": {"files": {"hsr_by_fiscal_year.csv": {}}}})
    with pytest.raises(antitrust.SchemaDriftError, match="sources.yaml"):
        antitrust.load(RUN_A)
