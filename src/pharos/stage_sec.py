"""Raw SEC landings -> the sec_universe group's staging tables (M2 step 2).

Built one table at a time; the group is declared in groups.py and given a
stager in stage.STAGERS only once its loaders are wired into refresh, so until
then nothing here runs on a real night.

Rules applied here (D84, D85):
- Every landing of a dataset is read, as M1 reads raw (D18).
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
"""

import exchange_calendars as xc

from pharos.runs import raw_select
from pharos.stage import StageError, _require_none, _transform

SEC_START = "1993-01-01"   # the full-index's first quarter (D52)
FILINGS_KEY = ("cik", "accession", "form", "name")   # the index's own row key (D61)
LINEAGE = ("run_id", "loaded_at", "last_modified")


def bind_sessions(con) -> None:
    """xnys_sessions: every XNYS trading day from 1993, for available_date (D99).
    The calendar runs about a year past today, so a filing dated today has a
    next session."""
    calendar = xc.get_calendar("XNYS", start=SEC_START)
    con.execute("CREATE TABLE xnys_sessions (session DATE)")
    con.executemany("INSERT INTO xnys_sessions VALUES (?)", [(d.date(),) for d in calendar.sessions])


def bind_sec_raw(con, dataset: str, included: list[str]) -> str:
    """raw_sec_<dataset> over the included runs' files. Returns the view name."""
    select = raw_select("sec", dataset, included)
    if select is None:
        raise StageError(f"no raw files for sec/{dataset}")
    view = f"raw_sec_{dataset}"
    con.execute(f"CREATE VIEW {view} AS {select}")
    return view


def collapse(con, source: str, target: str, key: tuple[str, ...]) -> None:
    """Landings -> one row per key (D84, D85): identical rows keep the earliest
    run's lineage; a changed key keeps the newest landing's values."""
    columns = [c[0] for c in con.execute(f"DESCRIBE {source}").fetchall()]
    values = [c for c in columns if c not in LINEAGE]
    lineage = ["min(run_id) AS run_id"] + [f"arg_min({c}, run_id) AS {c}" for c in LINEAGE if c != "run_id"]
    by_key = ", ".join(key)
    con.execute(f"""
        CREATE TABLE {target} AS
        SELECT {', '.join(values)}, {', '.join(LINEAGE)}
        FROM (
            SELECT {', '.join(values)}, {', '.join(lineage)}, max(run_id) AS last_seen
            FROM {source}
            GROUP BY {', '.join(values)}
        )
        QUALIFY last_seen = max(last_seen) OVER (PARTITION BY {by_key})""")


def stage_filings(con, included: list[str]) -> str:
    """`filings`: every full-index row, one per (cik, accession, form, name)."""
    raw = bind_sec_raw(con, "full_index", included)
    rows = _transform(con, "sec_filings", raw)
    _require_none(con, rows, "date_filed IS NULL", "index rows with no filing date")
    _require_none(con, rows, "accession = ''",
                  "index rows whose filename is not edgar/data/<cik>/<accession>.txt")
    _require_none(con, rows, "path_cik IS DISTINCT FROM cik", "index rows whose filename names another CIK")
    _require_none(con, rows, "available_date IS NULL",
                  "index rows filed after the last XNYS session in the calendar (D99)")
    con.execute(f"CREATE VIEW sec_filings_rows AS SELECT * EXCLUDE (path_cik) FROM {rows}")
    collapse(con, "sec_filings_rows", "filings", FILINGS_KEY)
    return "filings"

