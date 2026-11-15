"""Staging for the small landed groups: imf, un, fsds (D174) and antitrust (D175).

Each dataset stages the same way, rebuilt from every landing each run (the
largest is under a million rows a landing):
- every included run's raw rows go through transform/<sql>.sql, one row out
  per raw row, and the dataset's assertions must match no row;
- a key keeps its newest value (D132). The key includes the vintage, so a new
  edition or revision adds rows and an earlier one stays as it was landed;
- lineage (run_id, loaded_at) is not a value: a key whose values did not
  change keeps the lineage of the run that first landed them, so landing the
  same thing twice changes nothing. Precisely: a key's row is the first row of
  its last run of unchanged values, ordered by run;
- inside one landing a key listed with two different value sets stops the
  group: staging never picks between two things one landing says;
- the key is checked unique in the result.
"""

from dataclasses import dataclass
from pathlib import Path

import duckdb

from pharos.runs import raw_select
from pharos.stage import StageError, _require_none, _sql

LINEAGE = ("run_id", "loaded_at")


@dataclass(frozen=True)
class Dataset:
    source: str
    raw: str                  # raw dataset under raw/<source>/
    sql: str                  # transform/<sql>.sql, one row per raw row
    table: str                # staging table: the newest value per key
    key: tuple[str, ...]
    asserts: tuple[tuple[str, str], ...] = ()   # (condition that must match no row, what it means)


WEO_OBSERVATIONS = Dataset(
    source="imf", raw="weo_observations", sql="imf_weo_observations", table="weo_observations",
    key=("country", "indicator", "year", "vintage"),
    asserts=(("value IS NULL OR isnan(value)", "WEO values that are missing"),
             ("obs_date IS NULL OR vintage IS NULL", "WEO values with no year or no publication date")),
)

WEO_SERIES = Dataset(
    source="imf", raw="weo_series", sql="imf_weo_series", table="weo_series",
    key=("country", "indicator", "vintage"),
    asserts=(("vintage IS NULL", "WEO series with no publication date"),),
)

WPP_POPULATION = Dataset(
    source="un", raw="wpp_population", sql="un_wpp_population", table="wpp_population",
    key=("loc_id", "var_id", "year", "vintage"),
    asserts=(("mid_period IS DISTINCT FROM year + 0.5", "population rows not dated mid-year (July 1)"),
             ("loc_id IS NULL OR var_id IS NULL OR year IS NULL OR vintage IS NULL",
              "population rows with no location, variant, year or file date")),
)

FSDS_PRE = Dataset(
    source="sec", raw="fsds_pre", sql="sec_fsds_pre", table="fsds_pre",
    key=("adsh", "report", "line"),
    asserts=(("NOT regexp_full_match(adsh, '[0-9]{10}-[0-9]{2}-[0-9]{6}')",
              "presentation rows whose accession is not 0000000000-00-000000"),
             ("report IS NULL OR line IS NULL", "presentation rows whose report or line is not a number"),
             ("obs_date IS NULL OR vintage IS NULL", "presentation rows with no quarter or no file date")),
)

# Merger-enforcement statistics (D175). Each file's own arithmetic is asserted
# here. Two rows do not add up in the FTC's own file, read twice on 2026-11-15
# with the same result, and are named: FY1999 (FTC 45 + DOJ 68 = 113 against a
# total of 111) and FY1993 (granted 1,201 + not granted 448 = 1,649 against
# 1,689 requested). Any other row that fails to add up stops the group.
_YEAR = ("fiscal_year IS NULL OR obs_date IS NULL OR vintage IS NULL", "rows with no fiscal year or no retrieval date")
_CELL = ("enforced IS NULL OR closed IS NULL OR enforced < 0 OR closed < 0 OR obs_date IS NULL OR vintage IS NULL",
         "table cells with no count, a negative count, or no edition or retrieval date")

HSR_FISCAL_YEAR = Dataset(
    source="ftc", raw="hsr_fiscal_year", sql="ftc_hsr_fiscal_year", table="hsr_fiscal_year",
    key=("fiscal_year", "vintage"),
    asserts=(_YEAR,
             ("second_requests IS DISTINCT FROM second_requests_ftc + second_requests_doj AND fiscal_year <> 1999",
              "fiscal years whose FTC and DOJ second requests do not add to the total"),
             ("early_termination_requested IS DISTINCT FROM early_termination_granted + early_termination_not_granted "
              "AND fiscal_year <> 1993",
              "fiscal years whose early terminations granted and not granted do not add to those requested"),
             ("adjusted_transactions > transactions_reported OR second_requests > adjusted_transactions",
              "fiscal years with more adjusted transactions than reported, or more second requests than adjusted")),
)

FTC_MERGER_ACTIONS = Dataset(
    source="ftc", raw="merger_actions", sql="ftc_merger_actions", table="ftc_merger_actions",
    key=("fiscal_year", "vintage"),
    asserts=(_YEAR,
             ("total IS DISTINCT FROM consents + injunctions + part3 + abandoned",
              "fiscal years whose FTC actions by type do not add to the total")),
)

DOJ_MERGER_OUTCOMES = Dataset(
    source="doj", raw="merger_outcomes", sql="doj_merger_outcomes", table="doj_merger_outcomes",
    key=("fiscal_year", "vintage"),
    asserts=(_YEAR,
             ("complaints IS DISTINCT FROM litigation_complaints + simultaneous_settlement",
              "fiscal years whose DOJ complaints by kind do not add to complaints filed"),
             ("merger_outcomes IS DISTINCT FROM abandoned + resolved_concerns",
              "fiscal years whose DOJ outcomes without a complaint do not add to their total")),
)

FTC_HMID_HHI = Dataset(
    source="ftc", raw="hmid_hhi", sql="ftc_hmid_hhi", table="ftc_hmid_hhi",
    key=("edition_end_fy", "table_no", "post_merger_hhi", "delta_hhi", "vintage"),
    asserts=(_CELL,),
)

FTC_HMID_COMPETITORS = Dataset(
    source="ftc", raw="hmid_competitors", sql="ftc_hmid_competitors", table="ftc_hmid_competitors",
    key=("edition_end_fy", "table_no", "significant_competitors", "vintage"),
    asserts=(_CELL,),
)

DATASETS = {"imf": (WEO_OBSERVATIONS, WEO_SERIES), "un": (WPP_POPULATION,), "fsds": (FSDS_PRE,),
            "antitrust": (HSR_FISCAL_YEAR, FTC_MERGER_ACTIONS, DOJ_MERGER_OUTCOMES, FTC_HMID_HHI,
                          FTC_HMID_COMPETITORS)}


def stage_group(name: str, included: list[str], target: Path) -> None:
    """Stage every dataset of one landed group into target/<table>.parquet."""
    for ds in DATASETS[name]:
        con = duckdb.connect()
        try:
            stage_table(con, ds, included)
            con.execute(f"COPY {ds.table} TO '{(target / f'{ds.table}.parquet').as_posix()}' (FORMAT parquet)")
        finally:
            con.close()


def stage_table(con, ds: Dataset, included: list[str]) -> None:
    """CREATE TABLE ds.table from the included runs' landings."""
    select = raw_select(ds.source, ds.raw, included)
    if select is None:
        raise StageError(f"no raw files for {ds.source}/{ds.raw}")
    raw_view = f"raw_{ds.source}_{ds.raw}"            # the name the SQL file reads
    con.execute(f"CREATE VIEW {raw_view} AS {select}")
    rows = f"{ds.sql}_rows"
    con.execute(f"CREATE TABLE {rows} AS {_sql(ds.sql)}")
    n_in = con.execute(f"SELECT count(*) FROM {raw_view}").fetchone()[0]
    n_out = con.execute(f"SELECT count(*) FROM {rows}").fetchone()[0]
    if n_in != n_out:
        raise StageError(f"{ds.sql}: {n_in:,} raw rows in, {n_out:,} out (a join fanned out or dropped rows)")
    for condition, what in ds.asserts:
        _require_none(con, rows, condition, what)

    columns = [c[0] for c in con.execute(f"DESCRIBE {rows}").fetchall()]
    values = [c for c in columns if c not in LINEAGE]
    key = ", ".join(ds.key)
    vals = f"row({', '.join(values)})"
    twice = con.execute(f"""
        SELECT {key}, run_id FROM {rows} GROUP BY ALL HAVING count(DISTINCT {vals}) > 1
        ORDER BY ALL LIMIT 5""").fetchall()
    if twice:
        raise StageError(f"{ds.table}: a key listed with different values inside one landing "
                         f"(first {len(twice)}: {twice}); staging never picks between them")
    con.execute(f"""
        CREATE TABLE {ds.table} AS
        WITH one AS (   -- identical copies inside a landing collapse to one
            SELECT DISTINCT ON ({key}, run_id) * FROM {rows}),
        runs AS (
            SELECT *, {vals} IS DISTINCT FROM lag({vals}) OVER (PARTITION BY {key} ORDER BY run_id) AS changed
            FROM one)
        SELECT {', '.join(columns)} FROM runs
        QUALIFY changed AND run_id = max(run_id) FILTER (WHERE changed) OVER (PARTITION BY {key})""")
    dup = con.execute(f"SELECT {key}, count(*) FROM {ds.table} GROUP BY ALL HAVING count(*) > 1 "
                      "ORDER BY ALL LIMIT 5").fetchall()
    if dup:
        raise StageError(f"{ds.table}: key ({key}) not unique (first {len(dup)}: {dup})")
