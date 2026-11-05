"""Raw SEC landings -> the sec_universe group's staging tables (M2 step 2).

Built one table at a time; the group is declared in groups.py and given a
stager in stage.STAGERS only once its loaders are wired into refresh, so until
then nothing here runs on a real night.

Rules applied here (D84, D85):
- Rows that agree on everything but lineage (run_id, loaded_at, and where a
  row sits in the source: the landing's last_modified, the zip member it came
  from) collapse to one, keeping the earliest run's lineage, so an identical
  rerun changes nothing.
- A key whose values differ across landings takes the value of the newest
  landing that has the key; C38 (step 3) lists the change. "Newest" is run
  order, the one ordering staging uses (D121). Two different values for one key
  inside the same newest landing both survive, for the uniqueness checks to
  block: staging never picks between two things a single landing says. The one
  measured exception is D126's acceptance-time conflict, below.
- Shape the SQL can't express is asserted here and stops the group: one row out
  per raw row, and every derived column present.

Both filing tables roll forward (D125). The full-index re-lands two quarters in
full every night and the filing history a 10-day overlap, so reading every
landing grows with the number of nights, not with SEC's data (#109). Staging
keeps a versions table per dataset: every value a key has had, with the run
that first landed it and the run that last did (last_seen). A night folds its
own landings into the versions of the run the group last published and takes
the table (the newest version per key) from the result. Folding is exact:
first run, last run and lineage are min, max and arg-min, so folding A then B
equals grouping A and B at once. Every Sunday (New York date), and whenever
there is no base to fold into, the versions are rebuilt from every landing; on
a Sunday with a base the fold is built too, as <versions>_incremental, for C39
to compare (step 3).

Acceptance-time conflicts (D126): submissions.zip lists some filings twice
under one CIK, in the main file and an overflow file (47 on 2026-11-04). Copies
that differ in nothing but acceptance time (6, always by the New York offset,
4 or 5 hours) collapse to one row with acceptance NULL and
acceptance_conflict true; nothing says which copy is right, and acceptance is
not used for available_date (D99). Copies that differ in anything else both
survive, for C30.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import exchange_calendars as xc

from pharos.runs import raw_select
from pharos.stage import StageError, _require_none, _sql

# the earliest filing date in either source: submissions.zip lists 9 filings
# before 1993, the first 1980-01-02 (2026-11-04); the full-index starts 1993 (D52)
CALENDAR_START = "1980-01-01"
NEW_YORK = ZoneInfo("America/New_York")
FULL, FOLD = "full", "fold"


@dataclass(frozen=True)
class Rolled:
    mode: str                 # FULL: rebuilt from every landing; FOLD: folded into the base
    base_run: str | None      # the run whose versions were folded into (and, on a rebuild
                              # night, compared against), or None
    compared: bool            # <versions>_incremental was built beside the rebuild


@dataclass(frozen=True)
class Dataset:
    """One SEC raw dataset rolled forward into a versions table and a table."""
    raw: str                  # raw dataset under raw/sec/
    sql: str                  # transform/<sql>.sql, one row per raw row
    versions: str             # staging table: every version of every key
    table: str                # staging table: the newest version per key
    key: tuple[str, ...]
    lineage: tuple[str, ...]  # not values: where and when a row was landed
    asserts: tuple[tuple[str, str], ...]   # (condition that must match no row, what it means)
    per_landing: Callable[[object, str], str] | None = None   # rows -> rows, inside each landing


def rebuild_night(run_id: str) -> bool:
    """A run whose New York date is a Sunday rebuilds from every landing (D93, D125)."""
    started = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return started.astimezone(NEW_YORK).weekday() == 6


def bind_sessions(con) -> None:
    """xnys_sessions: every XNYS trading day from 1980, for available_date (D99).
    The calendar runs about a year past today, so a filing dated today has a
    next session."""
    calendar = xc.get_calendar("XNYS", start=CALENDAR_START)
    con.execute("CREATE TABLE xnys_sessions (session DATE)")
    con.executemany("INSERT INTO xnys_sessions VALUES (?)", [(d.date(),) for d in calendar.sessions])


def fold(con, source: str, target: str, lineage: tuple[str, ...]) -> None:
    """Rows (each with last_seen; a raw row's is its own run) -> one row per
    distinct value set: first run's lineage, latest last_seen. Applying it to
    earlier versions plus new rows equals applying it to all the rows at once."""
    columns = [c[0] for c in con.execute(f"DESCRIBE {source}").fetchall()]
    values = [c for c in columns if c not in lineage + ("last_seen",)]
    kept = ["min(run_id) AS run_id"] + [f"arg_min({c}, run_id) AS {c}" for c in lineage if c != "run_id"]
    con.execute(f"""
        CREATE TABLE {target} AS
        SELECT {', '.join(values)}, {', '.join(kept)}, max(last_seen) AS last_seen
        FROM {source}
        GROUP BY {', '.join(values)}""")


def newest(con, versions: str, target: str, key: tuple[str, ...]) -> None:
    """Versions -> the version each key last landed with (D85)."""
    con.execute(f"""
        CREATE TABLE {target} AS
        SELECT * EXCLUDE (last_seen) FROM {versions}
        QUALIFY last_seen = max(last_seen) OVER (PARTITION BY {', '.join(key)})""")


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
    raw="full_index", sql="sec_filings", versions="filing_versions", table="filings",
    key=("cik", "accession", "form", "name"),            # the index's own row key (D61)
    lineage=("run_id", "loaded_at", "last_modified"),
    asserts=(("date_filed IS NULL", "index rows with no filing date"),
             ("accession = ''", "index rows whose filename is not edgar/data/<cik>/<accession>.txt"),
             ("path_cik IS DISTINCT FROM cik", "index rows whose filename names another CIK"),
             ("available_date IS NULL", "index rows filed after the last XNYS session in the calendar (D99)")),
)

SUBMISSIONS = Dataset(
    raw="submission_filings", sql="sec_submission_filings", versions="submission_filing_versions",
    table="submission_filings",
    key=("cik", "accession"),                            # measured 2026-11-05 (C30, D126)
    lineage=("run_id", "loaded_at", "source_file"),      # source_file: where in the zip, not what SEC says
    asserts=(("NOT regexp_full_match(accession, '[0-9]{10}-[0-9]{2}-[0-9]{6}')",
              "filing-history rows whose accession is not 0000000000-00-000000"),
             ("available_date IS NULL", "filings dated after the last XNYS session in the calendar (D99)")),
    per_landing=acceptance_conflicts,
)


def _rows(con, ds: Dataset, runs: list[str], tag: str) -> str:
    """The runs' raw rows through the dataset's SQL, row count and assertions
    checked, collapsed inside each landing where the dataset says so, with
    last_seen = their run. Returns a view name."""
    select = raw_select("sec", ds.raw, runs)
    if select is None:
        raise StageError(f"no raw files for sec/{ds.raw}")
    raw_view = f"raw_sec_{ds.raw}"            # the name the SQL file reads
    con.execute(f"CREATE OR REPLACE VIEW {raw_view} AS {select}")
    rows = f"{ds.sql}_{tag}"
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
    con.execute(f"CREATE VIEW {rows}_seen AS SELECT *, run_id AS last_seen FROM {rows}")
    return f"{rows}_seen"


def roll_forward(con, ds: Dataset, included: list[str], run_id: str, base: Path | None) -> Rolled:
    """Stage ds.versions and ds.table.

    base: the staging folder of the run the group last published, or None.
    Its <versions>.parquet is folded into unless tonight rebuilds."""
    base_file = base / f"{ds.versions}.parquet" if base is not None else None
    have_base = base_file is not None and base_file.exists()
    if have_base and base.name not in included:
        raise StageError(f"base run {base.name} is not among the runs staged ({included[0]}..{included[-1]})")
    rebuild = rebuild_night(run_id) or not have_base

    if rebuild:
        fold(con, _rows(con, ds, included, "all"), ds.versions, ds.lineage)
    if have_base:
        later = [r for r in included if r > base.name]
        con.execute(f"CREATE VIEW {ds.versions}_base AS SELECT * FROM read_parquet('{base_file.as_posix()}')")
        source = f"{ds.versions}_base"
        if later:
            new = _rows(con, ds, later, "new")
            con.execute(f"CREATE VIEW {ds.versions}_input AS SELECT * FROM {source} "
                        f"UNION ALL BY NAME SELECT * FROM {new}")
            source = f"{ds.versions}_input"
        fold(con, source, f"{ds.versions}_incremental" if rebuild else ds.versions, ds.lineage)
    newest(con, ds.versions, ds.table, ds.key)
    return Rolled(FULL if rebuild else FOLD, base.name if have_base else None, rebuild and have_base)


def stage_filings(con, included: list[str], run_id: str, base: Path | None) -> Rolled:
    """`filing_versions` and `filings`: the full-index, one row per (cik, accession, form, name)."""
    return roll_forward(con, INDEX, included, run_id, base)


def stage_submission_filings(con, included: list[str], run_id: str, base: Path | None) -> Rolled:
    """`submission_filing_versions` and `submission_filings`: the filing history,
    one row per (cik, accession)."""
    return roll_forward(con, SUBMISSIONS, included, run_id, base)
