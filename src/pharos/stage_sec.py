"""Raw SEC landings -> the SEC group's staging tables (M2 step 2).

stage_group() is the SEC group's stager (stage.STAGERS["sec"]). It runs on a
real night only once groups.SEC is registered in GROUPS.

Each dataset stages as a merge (D132): newest value per key.
- The base is the dataset's table in the serving version the group last
  published (Base); with none, every landing is read.
- Only landings from runs after the base's run are read. A key they carry
  takes the value of the newest of them that has it; every other key keeps
  its base row, so a key missing from later landings stays (D84: staging keeps
  what SEC once said).
- Lineage (run_id, loaded_at, and whatever says where or when a row was
  landed rather than what SEC says) is not a value. A key whose values are
  unchanged keeps the lineage of the run that first landed them, so an
  identical re-landing changes nothing (James, 2026-11-05). Precisely: a
  key's row is the first row of its last run of unchanged values, ordered by
  run. Merging run by run and reading every landing at once give the same
  table, so a base can be dropped and rebuilt from raw.
- Inside one landing, a key with two different value sets stops the group:
  staging never picks between two things a single landing says. Identical
  copies collapse to one. The one measured exception is D126's
  acceptance-time conflict, below.
- Shape the SQL can't express is asserted here and stops the group: one row out
  per raw row, the dataset's assertions, and the key unique in the result.

Acceptance-time conflicts (D126): submissions.zip lists some filings twice
under one CIK, in the main file and an overflow file (47 on 2026-11-04). Copies
that differ in nothing but acceptance time (6, always by the New York offset,
4 or 5 hours) collapse to one row with acceptance NULL and
acceptance_conflict true; nothing says which copy is right, and acceptance is
not used for available_date (D99). Copies that differ in anything else stop
the group.

Memory (#111): DuckDB's default limit is 80% of RAM. connect() caps it at
MEMORY_LIMIT and spills to <data root>/tmp/duckdb, not the working folder.
8 GB is a default, flagged: it is measured on the first real run.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import duckdb
import exchange_calendars as xc

from pharos.groups import HELD
from pharos.paths import data_root
from pharos.runs import raw_select
from pharos.stage import StageError, _require_none, _sql

# the earliest filing date in either source: submissions.zip lists 9 filings
# before 1993, the first 1980-01-02 (2026-11-04); the full-index starts 1993 (D52)
CALENDAR_START = "1980-01-01"
MEMORY_LIMIT = "8GB"   # #111: a default, measured on the first real run
RUN_DATES = ("obs_date", "available_date", "vintage")   # derived from the run on snapshot files


@dataclass(frozen=True)
class Base:
    """The serving version a group's tables are merged into."""
    run: str      # the run that staged them (the version's source_run for the group)
    folder: Path  # the serving version holding <table>.parquet


@dataclass(frozen=True)
class Merged:
    base_run: str | None      # the base merged into, or None: every landing was read
    runs_read: tuple[str, ...]


@dataclass(frozen=True)
class Dataset:
    """One SEC raw dataset, merged into one staging table by key."""
    raw: str                  # raw dataset under raw/sec/
    sql: str                  # transform/<sql>.sql, one row per raw row
    table: str                # staging table: the newest value per key
    key: tuple[str, ...]
    lineage: tuple[str, ...]  # not values: where and when a row was landed
    asserts: tuple[tuple[str, str], ...] = ()   # (condition that must match no row, what it means)
    per_landing: Callable[[object, str], str] | None = None   # rows -> rows, inside each landing


def connect() -> duckdb.DuckDBPyConnection:
    """A connection for SEC staging: memory capped, spilling inside the data root (#111)."""
    spill = data_root() / "tmp" / "duckdb"
    spill.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{MEMORY_LIMIT}'")
    con.execute(f"SET temp_directory = '{spill.as_posix()}'")
    return con


def bind_sessions(con) -> None:
    """xnys_sessions: every XNYS trading day from 1980, for available_date (D99).
    The calendar runs about a year past today, so a filing dated today has a
    next session."""
    calendar = xc.get_calendar("XNYS", start=CALENDAR_START)
    con.execute("CREATE TABLE xnys_sessions (session DATE)")
    con.executemany("INSERT INTO xnys_sessions VALUES (?)", [(d.date(),) for d in calendar.sessions])


def acceptance_conflicts(con, rows: str) -> str:
    """Inside each landing, copies of one filing that differ only in acceptance
    time -> one row, acceptance NULL, acceptance_conflict true (D126)."""
    columns = [c[0] for c in con.execute(f"DESCRIBE {rows}").fetchall()]
    lineage = ("run_id", "loaded_at", "source_file")
    values = [c for c in columns if c not in lineage + ("acceptance",)]
    # only keys listed more than once in a landing (47 of 27.4M on 2026-11-04)
    # go through the wide grouping; every other row passes straight through
    con.execute(f"""
        CREATE TABLE {rows}_repeated AS
        SELECT cik, accession, run_id FROM {rows} GROUP BY ALL HAVING count(*) > 1""")
    con.execute(f"""
        CREATE VIEW {rows}_one AS
        SELECT r.*, false AS acceptance_conflict
        FROM {rows} AS r ANTI JOIN {rows}_repeated USING (cik, accession, run_id)
        UNION ALL BY NAME
        SELECT {', '.join(values)},
               CASE WHEN min(acceptance) = max(acceptance) THEN min(acceptance) END AS acceptance,
               min(acceptance) IS DISTINCT FROM max(acceptance)                    AS acceptance_conflict,
               run_id, arg_min(loaded_at, source_file) AS loaded_at, min(source_file) AS source_file
        FROM {rows} SEMI JOIN {rows}_repeated USING (cik, accession, run_id)
        GROUP BY {', '.join(values)}, run_id""")
    return f"{rows}_one"


INDEX = Dataset(
    raw="full_index", sql="sec_filings", table="filings",
    key=("cik", "accession", "form", "name"),            # the index's own row key (D61)
    lineage=("run_id", "loaded_at", "last_modified"),
    asserts=(("date_filed IS NULL", "index rows with no filing date"),
             ("accession = ''", "index rows whose filename is not edgar/data/<cik>/<accession>.txt"),
             ("path_cik IS DISTINCT FROM cik", "index rows whose filename names another CIK"),
             ("available_date IS NULL", "index rows filed after the last XNYS session in the calendar (D99)")),
)

SUBMISSIONS = Dataset(
    raw="submission_filings", sql="sec_submission_filings", table="submission_filings",
    # form is in the key as in the full-index (D61, D134): a combined filing is listed once
    # per form (1 of 27,378,595 rows on the 2026-11-05 file); without form, 0 keys repeat
    # with different values beyond D126's acceptance-time pairs
    key=("cik", "accession", "form"),
    lineage=("run_id", "loaded_at", "source_file"),      # source_file: where in the zip, not what SEC says
    asserts=(("NOT regexp_full_match(accession, '[0-9]{10}-[0-9]{2}-[0-9]{6}')",
              "filing-history rows whose accession is not 0000000000-00-000000"),
             ("available_date IS NULL", "filings dated after the last XNYS session in the calendar (D99)")),
    per_landing=acceptance_conflicts,
)

DETAILS = Dataset(
    raw="submission_details", sql="sec_submission_details", table="submission_details",
    key=("cik",),                                        # measured unique, 991,508 rows (2026-11-05)
    lineage=("run_id", "loaded_at", "source_file") + RUN_DATES,
    asserts=(("cik IS NULL", "company-detail rows with no CIK"),),
)

TICKERS = Dataset(
    raw="tickers", sql="sec_tickers", table="tickers",
    key=("ticker",),                                     # measured unique, 10,409 rows (2026-11-05)
    lineage=("run_id", "loaded_at") + RUN_DATES,
    asserts=(("ticker IS NULL OR ticker = ''", "ticker rows with no ticker"),),
)

DATASETS = (INDEX, SUBMISSIONS, DETAILS, TICKERS)

# companyfacts (D57, D128): the on-demand group (D131, D135), merged the same way.
# Each landing is a full snapshot, so a merge is the newest landing plus the
# keys SEC has since dropped (D84)
FACTS = Dataset(
    raw="companyfacts", sql="sec_companyfacts", table="company_facts",
    # one fact of one filing; measured unique on the 2026-11-04 file (126,119,741 rows,
    # distinct on these 7 columns as on every column). start_date is NULL for an
    # instant fact, a key value like any other (IS NOT DISTINCT FROM). Defaulted (D127)
    key=("cik", "taxonomy", "concept", "unit", "accession", "start_date", "end_date"),
    lineage=("run_id", "loaded_at"),
    asserts=(("available_date IS NULL", "facts filed after the last XNYS session in the calendar (D99)"),),
)

CONCEPTS = Dataset(
    raw="companyfacts_concepts", sql="sec_companyfacts_concepts", table="company_concepts",
    key=("cik", "taxonomy", "concept"),
    lineage=("run_id", "loaded_at") + RUN_DATES,
    asserts=(("cik IS NULL OR concept IS NULL", "concept rows with no CIK or concept"),),
)

FACT_DATASETS = (FACTS, CONCEPTS)


def _rows(con, ds: Dataset, runs: list[str]) -> str | None:
    """The runs' raw rows through the dataset's SQL, row count and assertions
    checked, collapsed inside each landing where the dataset says so. Returns
    a view name, or None when none of the runs landed a file."""
    select = raw_select("sec", ds.raw, runs)
    if select is None:
        return None
    raw_view = f"raw_sec_{ds.raw}"            # the name the SQL file reads
    con.execute(f"CREATE OR REPLACE VIEW {raw_view} AS {select}")
    rows = f"{ds.sql}_rows"
    con.execute(f"CREATE TABLE {rows} AS {_sql(ds.sql)}")
    n_in = con.execute(f"SELECT count(*) FROM {raw_view}").fetchone()[0]
    n_out = con.execute(f"SELECT count(*) FROM {rows}").fetchone()[0]
    if n_in != n_out:
        raise StageError(f"{ds.sql}: {n_in:,} raw rows in, {n_out:,} out (a join fanned out or dropped rows)")
    con.execute(f"DROP VIEW {raw_view}")
    for condition, what in ds.asserts:
        _require_none(con, rows, condition, what)
    if "path_cik" in [c[0] for c in con.execute(f"DESCRIBE {rows}").fetchall()]:
        con.execute(f"CREATE VIEW {rows}_v AS SELECT * EXCLUDE (path_cik) FROM {rows}")
        rows = f"{rows}_v"
    if ds.per_landing is not None:
        rows = ds.per_landing(con, rows)
    return rows


def stage_table(con, ds: Dataset, included: list[str], base: Base | None) -> Merged:
    """Stage ds.table: the base's table with the landings of the included runs
    after the base's run merged in (newest value per key, D132).

    base: the serving version the group last published, or None. A version
    without this dataset's table counts as no base."""
    base_file = base.folder / f"{ds.table}.parquet" if base is not None else None
    if base_file is not None and not base_file.exists():
        base, base_file = None, None
    if base is not None and base.run not in included:
        raise StageError(f"base run {base.run} is not among the runs staged "
                         f"({included[0]}..{included[-1]})")
    later = [r for r in included if base is None or r > base.run]

    new = _rows(con, ds, later) if later else None
    if new is None and base is None:
        raise StageError(f"no raw files for sec/{ds.raw}")
    if new is None:   # nothing landed since the base: the base stands
        con.execute(f"CREATE TABLE {ds.table} AS SELECT * FROM read_parquet('{base_file.as_posix()}')")
        _require_unique(con, ds)
        return Merged(base.run, ())

    columns = [c[0] for c in con.execute(f"DESCRIBE {new}").fetchall()]
    values = [c for c in columns if c not in ds.lineage]
    key = ", ".join(ds.key)
    on = " AND ".join(f"b.{k} IS NOT DISTINCT FROM t.{k}" for k in ds.key)   # a NULL name is a key (D61)
    vals = f"row({', '.join(values)})"

    # two different value sets for one key inside one landing: refuse to pick
    twice = con.execute(f"""
        SELECT {key}, run_id FROM {new} GROUP BY ALL HAVING count(DISTINCT {vals}) > 1
        ORDER BY ALL LIMIT 5""").fetchall()
    if twice:
        raise StageError(f"{ds.table}: a key listed with different values inside one landing "
                         f"(first {len(twice)}: {twice}); staging never picks between them")

    if base is not None:
        con.execute(f"CREATE VIEW {ds.table}_base AS SELECT * FROM read_parquet('{base_file.as_posix()}')")
    else:
        con.execute(f"CREATE VIEW {ds.table}_base AS SELECT {', '.join(columns)} FROM {new} WHERE false")
    con.execute(f"CREATE TABLE {ds.table}_touched AS SELECT DISTINCT {key} FROM {new}")
    order = ", ".join(("run_id",) + tuple(c for c in ds.lineage if c != "run_id"))
    con.execute(f"""
        CREATE TABLE {ds.table} AS
        WITH merged AS (
            SELECT * FROM {new}
            UNION ALL BY NAME
            SELECT b.* FROM {ds.table}_base AS b SEMI JOIN {ds.table}_touched AS t ON {on}),
        runs AS (
            SELECT *, {vals} IS DISTINCT FROM lag({vals}) OVER (PARTITION BY {key} ORDER BY {order}) AS changed
            FROM merged)
        SELECT {', '.join(columns)} FROM runs
        QUALIFY changed AND run_id = max(run_id) FILTER (WHERE changed) OVER (PARTITION BY {key})
        UNION ALL BY NAME
        SELECT b.* FROM {ds.table}_base AS b ANTI JOIN {ds.table}_touched AS t ON {on}""")
    _require_unique(con, ds)
    return Merged(base.run if base is not None else None, tuple(later))


def _require_unique(con, ds: Dataset) -> None:
    key = ", ".join(ds.key)
    dup = con.execute(f"SELECT {key}, count(*) FROM {ds.table} GROUP BY ALL HAVING count(*) > 1 "
                      "ORDER BY ALL LIMIT 5").fetchall()
    if dup:
        raise StageError(f"{ds.table}: key ({key}) not unique after the merge (first {len(dup)}: {dup})")


def current_base(group: str = "sec") -> Base | None:
    """The serving version CURRENT names, if it holds the group's tables
    (published by a run, carried forward or not run since); else None."""
    from pharos.publish import composition, current_version   # publish imports stage
    version = current_version()
    if version is None:
        return None
    held = composition(version).get(group)
    if held is None or held["outcome"] not in HELD or held["source_run"] is None:
        return None
    return Base(held["source_run"], version)


def stage_group(included: list[str], target: Path, base: Base | None = None) -> dict[str, Merged]:
    """The SEC group's stager: each dataset merged into its served table and
    written to target/<table>.parquet. One connection per dataset, so memory
    never holds two of the 27M-row tables at once (#111).
    base: default, the live serving version (current_base)."""
    return _stage(DATASETS, included, target, base if base is not None else current_base("sec"))


def stage_companyfacts(included: list[str], target: Path, base: Base | None = None) -> dict[str, Merged]:
    """The companyfacts group's stager (D135): its two datasets merged as above."""
    return _stage(FACT_DATASETS, included, target,
                  base if base is not None else current_base("companyfacts"))


def _stage(datasets, included: list[str], target: Path, base: Base | None) -> dict[str, Merged]:
    merged = {}
    for ds in datasets:
        con = connect()
        if ds.sql in ("sec_filings", "sec_submission_filings", "sec_companyfacts"):
            bind_sessions(con)
        merged[ds.table] = stage_table(con, ds, included, base)
        con.execute(f"COPY {ds.table} TO '{(target / f'{ds.table}.parquet').as_posix()}' (FORMAT parquet)")
        con.close()
    return merged
