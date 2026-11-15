"""Merger-enforcement statistics from the FTC and the DOJ Antitrust Division (D175).

Five small files, placed by hand in <data root>/manual/antitrust/ and landed
from there. Nothing is downloaded: ftc.gov refuses scripted requests (403,
"resembles an abusive automated request", measured 2026-11-15), so the files
were read off the agencies' pages and reports once, and a new fiscal year is
added the same way. config/sources.yaml (antitrust.files) names each file,
where it came from and the date it was retrieved; that date is the vintage.

  hsr_by_fiscal_year.csv      FTC/DOJ Hart-Scott-Rodino annual data, one row a fiscal year
  ftc_merger_actions.csv      FTC merger enforcement actions by type, one row a fiscal year
  doj_merger_outcomes.csv     DOJ Antitrust Division merger outcomes, one row a fiscal year
  ftc_hmid_hhi.csv            FTC Horizontal Merger Investigation Data, the HHI tables of
                              each edition, one row per table cell
  ftc_hmid_competitors.csv    the same reports' significant-competitor tables

The two ftc_hmid files are written by scripts/antitrust_hmid.py from the PDFs
in manual/antitrust/sources/, which ties every table to its own totals.

Rules applied here:
- Every file named in sources.yaml must be on disk and every file the loader
  knows must be named there; a missing one stops the load.
- Parsing is strict: the header line must name exactly the columns below, in
  order, and every value must read as its declared type.
- Every run lands every file whole. Staging keeps a key's first landing while
  its values do not change, so landing the same file twice changes nothing.
"""

from datetime import date

import duckdb

from pharos import config
from pharos.paths import data_root
from pharos.runs import write_raw

FOLDER = ("manual", "antitrust")
N, T = "BIGINT", "VARCHAR"
# file -> (source, raw dataset, columns with their types)
FILES = {
    "hsr_by_fiscal_year.csv": ("ftc", "hsr_fiscal_year", {
        "FY": N, "TransactionsReported": N, "FilingsReceived": N, "AdjustedTransactions": N,
        "SecondRequestTotal": N, "SecondRequestFTC": N, "SecondRequestDOJ": N,
        "EarlyTerminationTransactions": N, "EarlyTerminationTransactionsGranted": N,
        "EarlyTerminationTransactionsNotGranted": N}),
    "ftc_merger_actions.csv": ("ftc", "merger_actions", {
        "FY": N, "consents": N, "injunctions": N, "part3": N, "abandoned": N, "total": N}),
    "doj_merger_outcomes.csv": ("doj", "merger_outcomes", {
        "FY": N, "complaints": N, "litigation_complaints": N, "simultaneous_settlement": N,
        "abandoned": N, "resolved_concerns": N, "merger_outcomes": N}),
    "ftc_hmid_hhi.csv": ("ftc", "hmid_hhi", {
        "edition_end_fy": N, "table": T, "markets": T, "post_merger_hhi": T, "delta_hhi": T,
        "enforced": N, "closed": N}),
    "ftc_hmid_competitors.csv": ("ftc", "hmid_competitors", {
        "edition_end_fy": N, "table": T, "markets": T, "significant_competitors": T,
        "enforced": N, "closed": N}),
}


class SchemaDriftError(RuntimeError):
    pass


def folder():
    return data_root().joinpath(*FOLDER)


def declared() -> dict[str, date]:
    """file -> the date it was retrieved, from config/sources.yaml."""
    files = (config.sources().get("antitrust") or {}).get("files") or {}
    if set(files) != set(FILES):
        raise SchemaDriftError(f"sources.yaml antitrust.files names {sorted(files)}, the loader knows {sorted(FILES)}")
    out = {}
    for name, entry in files.items():
        retrieved = (entry or {}).get("retrieved")
        if not isinstance(retrieved, date):
            raise SchemaDriftError(f"sources.yaml antitrust.files.{name}.retrieved must be a date, got {retrieved!r}")
        out[name] = retrieved
    return out


def read(con, name: str, retrieved: date):
    """One file as a relation: its columns as typed, plus retrieved and file."""
    path = folder() / name
    columns = FILES[name][2]
    with open(path, encoding="utf-8-sig") as f:
        header = f.readline().rstrip("\r\n").split(",")
    if header != list(columns):
        raise SchemaDriftError(f"{path}: columns {header}, expected {list(columns)}")
    spec = ", ".join(f"'{c}': '{t}'" for c, t in columns.items())
    rows = con.sql(f"""
        SELECT *, DATE '{retrieved.isoformat()}' AS retrieved, '{name}' AS file
        FROM read_csv('{path.as_posix()}', header = true, columns = {{{spec}}})""")
    if len(rows) == 0:
        raise SchemaDriftError(f"{path}: the file holds no rows")
    return rows


def load(run_id: str) -> dict[str, int]:
    """Land every file. Returns rows landed by file. Every file is read and
    checked before the first is written, so a bad file lands nothing."""
    retrieved = declared()
    missing = [n for n in FILES if not (folder() / n).is_file()]
    if missing:
        raise SchemaDriftError(f"{folder()}: files not on disk: {missing}")
    con = duckdb.connect()
    try:
        got = {name: read(con, name, retrieved[name]) for name in FILES}
        counts = {name: len(rows) for name, rows in got.items()}
        write_raw(got["hsr_by_fiscal_year.csv"], "ftc", "hsr_fiscal_year", run_id,
                  source_count=counts["hsr_by_fiscal_year.csv"])
        write_raw(got["ftc_merger_actions.csv"], "ftc", "merger_actions", run_id,
                  source_count=counts["ftc_merger_actions.csv"])
        write_raw(got["doj_merger_outcomes.csv"], "doj", "merger_outcomes", run_id,
                  source_count=counts["doj_merger_outcomes.csv"])
        write_raw(got["ftc_hmid_hhi.csv"], "ftc", "hmid_hhi", run_id,
                  source_count=counts["ftc_hmid_hhi.csv"])
        write_raw(got["ftc_hmid_competitors.csv"], "ftc", "hmid_competitors", run_id,
                  source_count=counts["ftc_hmid_competitors.csv"])
    finally:
        con.close()
    return counts
