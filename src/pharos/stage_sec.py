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
- A first landing (no base, one landing read, nothing collapsed inside it)
  takes a fast path: the merge would return its rows unchanged, so they are
  written straight to Parquet and the assertions and the key check run on
  that file. Same table; the one difference is that identical copies of a
  row stop the group instead of collapsing (none in companyfacts, measured).
  At 126M rows the merge spilled 60 GB and was killed (2026-11-05).

Company Facts is the complete-document exception (D140). Its two logical
tables are per-CIK partition directories. A run hard-links unchanged CIKs,
merges lineage only inside changed companies, and replaces or removes those
partitions before the ordinary checks and atomic publish. The old one-file
serving table is converted once; subsequent nightly cost follows the change
set rather than the 126M-row corpus.

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

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import duckdb
import exchange_calendars as xc

from pharos.groups import HELD
from pharos.parquet import parquet_files, scan
from pharos.paths import data_root
from pharos.progress import say, step
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
    quarter_from: str | None = None   # staged by quarter (D155, D156): the date a base without
                                      # a quarter column derives it from; None: SEC sends the column


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
    quarter_from="filing_date",    # D156: quarter derived in sec_submission_filings.sql
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

FILING_DATASETS = (INDEX, SUBMISSIONS, DETAILS, TICKERS)

# Company Facts (D57, D128, D139): a full baseline followed by per-CIK full
# documents for companies changed in submissions. The merge replaces touched
# keys and keeps untouched companies from the base (D84).
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
DATASETS = FILING_DATASETS + FACT_DATASETS


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


def stage_table(con, ds: Dataset, included: list[str], base: Base | None,
                target: Path | None = None) -> Merged:
    """Stage ds.table: the base's table with the landings of the included runs
    after the base's run merged in (newest value per key, D132).

    base: the serving version the group last published, or None. A version
    without this dataset's table counts as no base.
    target: the staging folder. Given, the table is written there as
    <table>.parquet (and ds.table is a view over it), and a first landing
    takes the fast path."""
    base_file = base.folder / f"{ds.table}.parquet" if base is not None else None
    if base_file is not None and not base_file.exists():
        base, base_file = None, None
    if base is not None and base.run not in included:
        raise StageError(f"base run {base.run} is not among the runs staged "
                         f"({included[0]}..{included[-1]})")
    later = [r for r in included if base is None or r > base.run]

    if target is not None and base is None and ds.per_landing is None:
        first = _first_landing(con, ds, later, target)
        if first is not None:
            return first

    new = _rows(con, ds, later) if later else None
    if new is None and base is None:
        raise StageError(f"no raw files for sec/{ds.raw}")
    if new is None:   # nothing landed since the base: the base stands
        con.execute(f"CREATE TABLE {ds.table} AS SELECT * FROM {scan(base_file)}")
        _require_unique(con, ds)
        _write(con, ds, target)
        return Merged(base.run, ())

    _merge(con, ds, new, scan(base_file) if base is not None else None)
    _write(con, ds, target)
    return Merged(base.run if base is not None else None, tuple(later))


def _merge(con, ds: Dataset, new: str, base_rel: str | None) -> None:
    """CREATE TABLE ds.table: the rows of base_rel (an SQL relation, None for no
    base) with the new landings merged in, newest value per key (D132), the key
    checked unique. stage_table passes the whole base; stage_quarter_table only
    the base's touched quarters (D155)."""
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

    if base_rel is not None:
        con.execute(f"CREATE VIEW {ds.table}_base AS SELECT * FROM {base_rel}")
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


QUARTER = "quarter"   # the filings table's partition column (D155)


def stage_quarter_table(ds: Dataset, included: list[str], base: Base | None,
                        target: Path) -> Merged:
    """Stage ds.table one folder per quarter: filings (D155) and
    submission_filings (D156).

    EDGAR's full index re-lands only its current quarters each night (2026Q3
    and 2026Q4, 415,235 of 27,390,800 rows on 2026-11-11), and submissions only
    its last 10 days of filings (2026Q4, 112,689 of 27,389,291), so only the quarters
    in the new landings are merged, with stage_table's rule over the base's rows
    of those quarters; every other quarter is hard-linked from the base. The
    result equals a full rebuild because a key never spans quarters (0 keys on
    2026-11-11). A new row whose key the base holds in an untouched quarter
    stops the group: the merge would no longer see both copies. A one-file base
    (published before D155 / D156) is split into quarters once; one without a
    quarter column gets it from ds.quarter_from, as the dataset's SQL does."""
    out = target / f"{ds.table}.parquet"
    base_path = base.folder / f"{ds.table}.parquet" if base is not None else None
    if base_path is not None and not base_path.exists():
        base, base_path = None, None
    if base is not None and base.run not in included:
        raise StageError(f"base run {base.run} is not among the runs staged "
                         f"({included[0]}..{included[-1]})")
    later = [r for r in included if base is None or r > base.run]

    con = connect()
    try:
        bind_sessions(con)
        new = _rows(con, ds, later) if later else None
        if new is None and base is None:
            raise StageError(f"no raw files for sec/{ds.raw}")
        touched = [] if new is None else \
            sorted(q for (q,) in con.execute(f"SELECT DISTINCT {QUARTER} FROM {new}").fetchall())
        bad = [q for q in touched if q is None or not re.fullmatch(r"[0-9]{4}Q[1-4]", q)]
        if bad:
            raise StageError(f"{ds.table}: new rows whose quarter is not YYYYQn: {bad[:5]}")
        quarters = ", ".join(f"'{q}'" for q in touched)
        base_sql = _quarter_base(con, ds, base_path) if base_path is not None else None

        if base_path is not None and base_path.is_dir():
            for part in sorted(p for p in base_path.iterdir() if p.is_dir()):
                if part.name.removeprefix(f"{QUARTER}=") not in touched:
                    _clone_partitions(part, out / part.name)
        elif base_path is not None:   # a one-file base from before D155: split it once
            where = f"WHERE {QUARTER} NOT IN ({quarters})" if touched else ""
            _partition_write(con, f"SELECT * FROM {base_sql} {where}", out, by=QUARTER)

        if new is not None:
            base_rel = None
            if base_path is not None:
                base_rel = f"(SELECT * FROM {base_sql} WHERE {QUARTER} IN ({quarters}))"
                key = ", ".join(f"n.{k}" for k in ds.key)
                on = " AND ".join(f"n.{k} IS NOT DISTINCT FROM b.{k}" for k in ds.key)
                moved = con.execute(f"""
                    SELECT {key}, n.{QUARTER} FROM {new} AS n SEMI JOIN (
                        SELECT {', '.join(ds.key)} FROM {base_sql}
                        WHERE {QUARTER} NOT IN ({quarters})) AS b ON {on}
                    ORDER BY ALL LIMIT 5""").fetchall()
                if moved:
                    raise StageError(f"{ds.table}: new rows whose key the base holds in another "
                                     f"quarter (first {len(moved)}: {moved}); D155 merges by quarter")
            _merge(con, ds, new, base_rel)
            delta = target / f".{ds.table}-{later[-1]}.delta"
            if delta.exists():
                shutil.rmtree(delta)
            _partition_write(con, f"SELECT * FROM {ds.table}", delta, by=QUARTER)
            out.mkdir(parents=True, exist_ok=True)
            for part in delta.iterdir():
                shutil.move(str(part), str(out / part.name))
            delta.rmdir()
    finally:
        con.close()

    if not parquet_files(out):
        raise StageError(f"no rows available for sec/{ds.raw}")
    return Merged(base.run if base is not None else None, tuple(later) if new is not None else ())


def quarter_of(column: str) -> str:
    """SQL for a date column's quarter as 'YYYYQn', the full index's own form (D156)."""
    return f"CAST(year({column}) AS VARCHAR) || 'Q' || CAST(quarter({column}) AS VARCHAR)"


def _quarter_base(con, ds: Dataset, base_path: Path) -> str:
    """The base as a relation with a quarter column: a base published before
    D156 has none, so it is derived from ds.quarter_from."""
    rel = scan(base_path)
    columns = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()]
    if QUARTER in columns:
        return rel
    if ds.quarter_from is None:
        raise StageError(f"{ds.table}: the base {base_path} has no {QUARTER} column")
    return f"(SELECT *, {quarter_of(ds.quarter_from)} AS {QUARTER} FROM {rel})"


def _write(con, ds: Dataset, target: Path | None) -> None:
    if target is not None:
        con.execute(f"COPY {ds.table} TO '{(target / f'{ds.table}.parquet').as_posix()}' (FORMAT parquet)")


def _first_landing(con, ds: Dataset, runs: list[str], target: Path) -> Merged | None:
    """The fast path: the one landing among runs, through the dataset's SQL,
    streamed to target/<table>.parquet and checked on that file. None when the
    runs hold no landing or more than one (the merge decides those)."""
    landed = [r for r in runs if raw_select("sec", ds.raw, [r]) is not None]
    if len(landed) != 1:
        return None
    raw_view = f"raw_sec_{ds.raw}"            # the name the SQL file reads
    con.execute(f"CREATE OR REPLACE VIEW {raw_view} AS {raw_select('sec', ds.raw, landed)}")
    out = target / f"{ds.table}.parquet"
    con.execute(f"COPY ({_sql(ds.sql)}) TO '{out.as_posix()}' (FORMAT parquet)")
    n_in = con.execute(f"SELECT count(*) FROM {raw_view}").fetchone()[0]
    con.execute(f"DROP VIEW {raw_view}")
    con.execute(f"CREATE VIEW {ds.table} AS SELECT * FROM read_parquet('{out.as_posix()}')")
    n_out = con.execute(f"SELECT count(*) FROM {ds.table}").fetchone()[0]
    if n_in != n_out:
        raise StageError(f"{ds.sql}: {n_in:,} raw rows in, {n_out:,} out (a join fanned out or dropped rows)")
    for condition, what in ds.asserts:
        _require_none(con, ds.table, condition, what)
    _require_unique(con, ds)
    return Merged(None, tuple(landed))


def _require_unique(con, ds: Dataset) -> None:
    key = ", ".join(ds.key)
    dup = con.execute(f"SELECT {key}, count(*) FROM {ds.table} GROUP BY ALL HAVING count(*) > 1 "
                      "ORDER BY ALL LIMIT 5").fetchall()
    if dup:
        raise StageError(f"{ds.table}: key ({key}) not unique after the merge (first {len(dup)}: {dup})")


def _link_or_copy(src: str, dst: str) -> str:
    """Clone an immutable partition cheaply; copy when hard links are unavailable."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


def _clone_partitions(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for file in parquet_files(source):
        relative = file.relative_to(source)
        out = target / relative
        out.parent.mkdir(parents=True, exist_ok=True)
        _link_or_copy(str(file), str(out))


def _partition_write(con, select: str, target: Path, by: str = "cik") -> None:
    """Write a fresh Hive partition directory, one directory per value of `by`
    (a CIK for Company Facts, a quarter for filings, D155)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY ({select}) TO '{target.as_posix()}' "
                f"(FORMAT parquet, PARTITION_BY ({by}), WRITE_PARTITION_COLUMNS true, "
                "FILENAME_PATTERN 'part_{i}')")


def _partition_ciks(path: Path) -> set[int]:
    if not path.is_dir():
        return set()
    return {int(p.name.removeprefix("cik=")) for p in path.iterdir()
            if p.is_dir() and p.name.startswith("cik=") and p.name[4:].isdigit()}


def _changes(run_id: str, ds: Dataset) -> tuple[set[int], bool]:
    """CIKs whose complete documents replace their previous partitions.

    New runs carry an explicit change manifest, including empty/404 documents.
    The fallback infers CIKs from rows for old raw history and test fixtures
    written before that manifest existed.
    """
    changes = raw_select("sec", "companyfacts_changes", [run_id])
    if changes is not None:
        con = duckdb.connect()
        try:
            rows = con.execute(f"SELECT DISTINCT cik, mode FROM ({changes})").fetchall()
        finally:
            con.close()
        modes = {mode for _, mode in rows}
        if not modes <= {"full", "incremental"} or len(modes) != 1:
            raise StageError(f"companyfacts_changes: unknown modes {sorted(modes)} in {run_id}")
        return {cik for cik, _ in rows}, "full" in modes

    # An empty manifest has a load record but intentionally no raw file.
    record = data_root() / "health" / "loads" / f"{run_id}__sec__companyfacts_changes.parquet"
    if record.exists():
        return set(), False

    raw = raw_select("sec", ds.raw, [run_id])
    if raw is None:
        return set(), False
    con = duckdb.connect()
    try:
        return {row[0] for row in con.execute(f"SELECT DISTINCT cik FROM ({raw})").fetchall()}, False
    finally:
        con.close()


def _check_new_rows(con, ds: Dataset, rows: str) -> None:
    """The generic merge's conflict rule, scoped to replacement partitions."""
    columns = [c[0] for c in con.execute(f"DESCRIBE {rows}").fetchall()]
    values = [c for c in columns if c not in ds.lineage]
    key = ", ".join(ds.key)
    vals = f"row({', '.join(values)})"
    twice = con.execute(f"""
        SELECT {key}, run_id FROM {rows} GROUP BY ALL HAVING count(DISTINCT {vals}) > 1
        ORDER BY ALL LIMIT 5""").fetchall()
    if twice:
        raise StageError(f"{ds.table}: a key listed with different values inside one landing "
                         f"(first {len(twice)}: {twice}); staging never picks between them")


def stage_partitioned_table(ds: Dataset, included: list[str], base: Base | None,
                            target: Path) -> Merged:
    """Stage a complete-document dataset as a per-CIK copy-on-write snapshot.

    The target remains self-contained: unchanged partitions are hard-linked
    from the immutable base, while each touched CIK is replaced by its complete
    newest document.  A legacy one-file base is partitioned once during
    migration.  Empty and HTTP-404 documents remove an old partition.
    """
    out = target / f"{ds.table}.parquet"
    base_path = base.folder / f"{ds.table}.parquet" if base is not None else None
    if base_path is not None and not base_path.exists():
        base, base_path = None, None
    if base is not None and base.run not in included:
        raise StageError(f"base run {base.run} is not among the runs staged "
                         f"({included[0]}..{included[-1]})")
    later = [r for r in included if base is None or r > base.run]

    if base_path is not None:
        with step(f"{ds.table}: link the base"):
            if base_path.is_dir():
                _clone_partitions(base_path, out)
            else:
                con = connect()
                _partition_write(con, f"SELECT * FROM {scan(base_path)}", out)
                con.close()

    for run_id in later:
        touched, full = _changes(run_id, ds)
        if full:
            touched |= _partition_ciks(out)
        say(f"{ds.table}: run {run_id}, {len(touched):,} companies to replace")
        if not touched:
            continue
        timed = lambda part: step(f"{ds.table} {run_id[:8]}: {part}")

        con = connect()
        if ds is FACTS:
            bind_sessions(con)
        new = _rows(con, ds, [run_id])
        if new is not None:
            with timed("read the run's rows and duplicate check"):
                _check_new_rows(con, ds, new)
            cik_list = ", ".join(str(cik) for cik in sorted(touched))
            con.execute(f"CREATE VIEW {ds.table}_new AS SELECT * FROM {new} WHERE cik IN ({cik_list})")
            columns = [c[0] for c in con.execute(f"DESCRIBE {ds.table}_new").fetchall()]
            values = [c for c in columns if c not in ds.lineage]
            on_key = " AND ".join(f"b.{k} IS NOT DISTINCT FROM n.{k}" for k in ds.key)
            same = " AND row(" + ", ".join(f"b.{c}" for c in values) + ") IS NOT DISTINCT FROM row(" \
                   + ", ".join(f"n.{c}" for c in values) + ")"
            if parquet_files(out):
                con.execute(f"CREATE VIEW {ds.table}_old AS SELECT * FROM {scan(out)} "
                            f"WHERE cik IN ({cik_list})")
            else:
                con.execute(f"CREATE VIEW {ds.table}_old AS SELECT {', '.join(columns)} "
                            f"FROM {ds.table}_new WHERE false")
            with timed("read old rows and merge"):
                con.execute(f"""
                CREATE TABLE {ds.table}_replacement AS
                SELECT b.* FROM {ds.table}_old AS b
                SEMI JOIN {ds.table}_new AS n ON {on_key}{same}
                UNION ALL BY NAME
                SELECT n.* FROM {ds.table}_new AS n
                ANTI JOIN {ds.table}_old AS b ON {on_key}{same}""")
            con.execute(f"CREATE VIEW {ds.table} AS SELECT * FROM {ds.table}_replacement")
            with timed("uniqueness check"):
                _require_unique(con, ds)

        delta = target / f".{ds.table}-{run_id}.delta"
        if delta.exists():
            shutil.rmtree(delta)
        with timed("write the replacement folders"):
            if new is not None and con.execute(f"SELECT count(*) FROM {ds.table}_replacement").fetchone()[0]:
                _partition_write(con, f"SELECT * FROM {ds.table}_replacement", delta)
        con.close()

        # Delta was fully built and checked before the snapshot is changed.
        with timed("swap folders"):
            for cik in touched:
                partition = out / f"cik={cik}"
                if partition.exists():
                    shutil.rmtree(partition)
            if delta.exists():
                out.mkdir(parents=True, exist_ok=True)
                for partition in delta.iterdir():
                    shutil.move(str(partition), str(out / partition.name))
                delta.rmdir()

    if not parquet_files(out):
        raise StageError(f"no rows available for sec/{ds.raw}")
    # Each partition is one CIK and every replacement was checked before it
    # was installed.  Re-scanning the 126M-row snapshot here would erase the
    # operating benefit of copy-on-write without adding a new invariant.
    return Merged(base.run if base is not None else None, tuple(later))


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
    if base is not None:
        runs = sorted(set(included) | {base.run})
        return _stage(DATASETS, runs, target, base)

    sec_base = current_base("sec")
    # Migration from versions published before Company Facts joined the SEC
    # group: its tables have a different source run recorded under the legacy
    # group name. After the first unified publish, sec_base owns all six.
    facts_base = current_base("companyfacts") or sec_base
    merged = _stage(FILING_DATASETS,
                    sorted(set(included) | ({sec_base.run} if sec_base else set())),
                    target, sec_base)
    merged.update(_stage(FACT_DATASETS,
                         sorted(set(included) | ({facts_base.run} if facts_base else set())),
                         target, facts_base))
    return merged


def stage_companyfacts(included: list[str], target: Path, base: Base | None = None) -> dict[str, Merged]:
    """Stage only Company Facts; retained for focused rebuilds and tests."""
    return _stage(FACT_DATASETS, included, target,
                  base if base is not None else current_base("companyfacts"))


def _stage(datasets, included: list[str], target: Path, base: Base | None) -> dict[str, Merged]:
    merged = {}
    for ds in datasets:
        with step(f"sec stage {ds.table}"):
            merged[ds.table] = _stage_one(ds, included, target, base)
    return merged


def _stage_one(ds, included: list[str], target: Path, base: Base | None) -> Merged:
    if ds in FACT_DATASETS:
        return stage_partitioned_table(ds, included, base, target)
    if ds in (INDEX, SUBMISSIONS):      # by quarter (D155, D156)
        return stage_quarter_table(ds, included, base, target)
    con = connect()
    if ds.sql in ("sec_filings", "sec_submission_filings", "sec_companyfacts"):
        bind_sessions(con)
    merged = stage_table(con, ds, included, base, target)
    con.close()
    return merged
