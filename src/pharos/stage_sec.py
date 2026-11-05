"""Raw SEC landings -> the sec_universe group's staging tables (M2 step 2).

Built one table at a time; the group is declared in groups.py and given a
stager in stage.STAGERS only once its loaders are wired into refresh, so until
then nothing here runs on a real night.

Rules applied here (D84, D85):
- Rows that agree on everything but lineage (run_id, loaded_at, the landing's
  last_modified) collapse to one, keeping the earliest run's lineage, so an
  identical rerun changes nothing.
- A key whose values differ across landings takes the value of the newest
  landing that has the key; C38 (step 3) lists the change. "Newest" is run
  order, the one ordering staging uses (D121). Two different values for one key
  inside the same newest landing both survive, for C23 to block: staging never
  picks between two things a single landing says.
- Shape the SQL can't express is asserted here and stops the group: one row out
  per raw row, and every derived column present.

The full-index rolls forward (D125). Every night re-lands the previous and
current quarter in full, so reading every landing (D84) grows with the number
of nights, not with SEC's index (#109). Staging keeps `filing_versions`: every
value a key has had, with the run that first landed it and the run that last
did (last_seen). A night folds its own landings into the versions of the run
the group last published and takes `filings` (the newest version per key) from
the result. Folding is exact: the versions of A + B equal the versions of A
plus those of B, since first run, last run and lineage are min, max and
arg-min. Every Sunday (New York date), and whenever there is no base to fold
into, the versions are rebuilt from every landing; on a Sunday with a base the
fold is built too, as `filing_versions_incremental`, for C39 to compare (step 3).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xc

from pharos.runs import raw_select
from pharos.stage import StageError, _require_none, _transform

SEC_START = "1993-01-01"   # the full-index's first quarter (D52)
FILINGS_KEY = ("cik", "accession", "form", "name")   # the index's own row key (D61)
LINEAGE = ("run_id", "loaded_at", "last_modified")
NEW_YORK = ZoneInfo("America/New_York")
FULL, FOLD = "full", "fold"


@dataclass(frozen=True)
class FilingsStaged:
    mode: str                 # FULL: rebuilt from every landing; FOLD: folded into the base
    base_run: str | None      # the run whose filing_versions were folded into (and, on a
                              # rebuild night, compared against), or None
    compared: bool            # filing_versions_incremental was built beside the rebuild


def rebuild_night(run_id: str) -> bool:
    """A run whose New York date is a Sunday rebuilds from every landing (D93, D125)."""
    started = datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return started.astimezone(NEW_YORK).weekday() == 6


def bind_sessions(con) -> None:
    """xnys_sessions: every XNYS trading day from 1993, for available_date (D99).
    The calendar runs about a year past today, so a filing dated today has a
    next session."""
    calendar = xc.get_calendar("XNYS", start=SEC_START)
    con.execute("CREATE TABLE xnys_sessions (session DATE)")
    con.executemany("INSERT INTO xnys_sessions VALUES (?)", [(d.date(),) for d in calendar.sessions])


def bind_sec_raw(con, dataset: str, included: list[str], view: str | None = None) -> str:
    """A view over the included runs' files of sec/<dataset>. Returns its name."""
    select = raw_select("sec", dataset, included)
    if select is None:
        raise StageError(f"no raw files for sec/{dataset}")
    view = view or f"raw_sec_{dataset}"
    con.execute(f"CREATE VIEW {view} AS {select}")
    return view


def fold(con, source: str, target: str) -> None:
    """Rows (each with last_seen; a raw row's is its own run) -> one row per
    distinct value set: first run's lineage, latest last_seen. Applying it to
    earlier versions plus new rows equals applying it to all the rows at once."""
    columns = [c[0] for c in con.execute(f"DESCRIBE {source}").fetchall()]
    values = [c for c in columns if c not in LINEAGE + ("last_seen",)]
    lineage = ["min(run_id) AS run_id"] + [f"arg_min({c}, run_id) AS {c}" for c in LINEAGE if c != "run_id"]
    con.execute(f"""
        CREATE TABLE {target} AS
        SELECT {', '.join(values)}, {', '.join(lineage)}, max(last_seen) AS last_seen
        FROM {source}
        GROUP BY {', '.join(values)}""")


def newest(con, versions: str, target: str, key: tuple[str, ...]) -> None:
    """Versions -> the version each key last landed with (D85)."""
    con.execute(f"""
        CREATE TABLE {target} AS
        SELECT * EXCLUDE (last_seen) FROM {versions}
        QUALIFY last_seen = max(last_seen) OVER (PARTITION BY {', '.join(key)})""")


def _index_rows(con, included: list[str], name: str) -> str:
    """The included runs' full-index rows as staging rows (sec_filings.sql),
    asserted, with last_seen = their run. Returns a view name."""
    raw = bind_sec_raw(con, "full_index", included, view=f"raw_sec_full_index_{name}")
    con.execute(f"CREATE VIEW raw_sec_full_index AS SELECT * FROM {raw}")
    rows = _transform(con, "sec_filings", "raw_sec_full_index")
    con.execute("DROP VIEW raw_sec_full_index")
    con.execute(f"ALTER TABLE sec_filings RENAME TO sec_filings_{name}")
    rows = f"sec_filings_{name}"
    _require_none(con, rows, "date_filed IS NULL", "index rows with no filing date")
    _require_none(con, rows, "accession = ''",
                  "index rows whose filename is not edgar/data/<cik>/<accession>.txt")
    _require_none(con, rows, "path_cik IS DISTINCT FROM cik", "index rows whose filename names another CIK")
    _require_none(con, rows, "available_date IS NULL",
                  "index rows filed after the last XNYS session in the calendar (D99)")
    con.execute(f"CREATE VIEW {rows}_v AS SELECT * EXCLUDE (path_cik), run_id AS last_seen FROM {rows}")
    return f"{rows}_v"


def stage_filings(con, included: list[str], run_id: str, base: Path | None) -> FilingsStaged:
    """`filing_versions` and `filings` (one row per (cik, accession, form, name)).

    base: the staging folder of the run the group last published, or None.
    Its filing_versions.parquet is folded into unless tonight rebuilds."""
    base_file = base / "filing_versions.parquet" if base is not None else None
    have_base = base_file is not None and base_file.exists()
    if have_base and base.name not in included:
        raise StageError(f"base run {base.name} is not among the runs staged ({included[0]}..{included[-1]})")
    rebuild = rebuild_night(run_id) or not have_base

    if rebuild:
        fold(con, _index_rows(con, included, "all"), "filing_versions")
    if have_base:
        later = [r for r in included if r > base.name]
        con.execute(f"CREATE VIEW base_versions AS SELECT * FROM read_parquet('{base_file.as_posix()}')")
        source = "base_versions"
        if later:
            new = _index_rows(con, later, "new")
            con.execute(f"CREATE VIEW fold_input AS SELECT * FROM base_versions UNION ALL BY NAME SELECT * FROM {new}")
            source = "fold_input"
        fold(con, source, "filing_versions_incremental" if rebuild else "filing_versions")
    newest(con, "filing_versions", "filings", FILINGS_KEY)
    return FilingsStaged(FULL if rebuild else FOLD, base.name if have_base else None, rebuild and have_base)
