"""EDGAR bulk submissions.zip: company details and filing history for every CIK.

One download a night (D58), two raw files a run:
- raw/sec/submission_details/<run_id>.parquet: one row per main CIK file, every
  field except the filing arrays, every run. Lists and objects (tickers,
  exchanges, former names, addresses, and `filing_files`, the overflow files a
  record names with their filingCount, C29) are kept as sent: tickers and
  exchanges as lists, the rest as JSON text.
- raw/sec/submission_filings/<run_id>.parquet: one row per filing, from each
  main file's `filings.recent` arrays and from the overflow files
  (CIK##########-submissions-NNN.json). The first run lands all of it (27.4M
  rows on 2026-11-04); later runs land only filings dated on or after the
  latest filing date already in raw minus `sec.submissions_overlap_days`
  (config/sources.yaml), so a late correction is re-landed (C38).

Rules applied here:
- The zip streams to <data root>/tmp/<run_id>/ through sec.fetch(to=...) and
  is deleted after parsing; raw holds the parsed rows, as for every source.
- Strict shape, measured on the 2026-11-04 file: every main file has exactly
  DETAIL_KEYS + 'filings', `filings` exactly {'recent', 'files'}, every filing
  array set exactly FILING_KEYS, all arrays of one file the same length, and
  every value of the type FILING_TYPES / DETAIL_TYPES names (or null). Any
  other member than CIK files and ALLOWED_OTHER stops the load. Nothing is
  written on drift.
- Values are stored as sent; staging casts (dates are 'YYYY-MM-DD' text).
- Rows are parsed with Polars in batches to temporary Parquet and handed to
  write_raw as one DuckDB relation (D93). The temp folder is removed whether
  or not the load succeeds.
- The details load record's source_count is the number of main CIK files in
  the zip, for C29.
"""

import json
import re
import shutil
import zipfile
from datetime import date, timedelta
from pathlib import Path

import duckdb
import polars as pl

from pharos import config
from pharos.loaders import sec
from pharos.paths import data_root
from pharos.runs import raw_select, write_raw

SOURCE = "sec"
DETAILS, FILINGS = "submission_details", "submission_filings"
URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
MAIN = re.compile(r"CIK(\d{10})\.json")
OVERFLOW = re.compile(r"CIK(\d{10})-submissions-\d{3}\.json")
ALLOWED_OTHER = {"placeholder.txt"}   # a 16-byte 'Placeholder file' in the 2026-11-04 zip
BATCH = 1_000_000                     # filing rows per temporary Parquet file

# JSON key -> (column, Polars type). "json" means: stored as JSON text.
DETAIL_TYPES = {
    "cik": ("cik", pl.Int64), "entityType": ("entity_type", pl.String), "sic": ("sic", pl.String),
    "sicDescription": ("sic_description", pl.String), "ownerOrg": ("owner_org", pl.String),
    "insiderTransactionForOwnerExists": ("insider_transaction_for_owner_exists", pl.Int64),
    "insiderTransactionForIssuerExists": ("insider_transaction_for_issuer_exists", pl.Int64),
    "name": ("name", pl.String), "tickers": ("tickers", pl.List(pl.String)),
    "exchanges": ("exchanges", pl.List(pl.String)), "ein": ("ein", pl.String), "lei": ("lei", pl.String),
    "description": ("description", pl.String), "website": ("website", pl.String),
    "investorWebsite": ("investor_website", pl.String), "category": ("category", pl.String),
    "fiscalYearEnd": ("fiscal_year_end", pl.String), "stateOfIncorporation": ("state_of_incorporation", pl.String),
    "stateOfIncorporationDescription": ("state_of_incorporation_description", pl.String),
    "addresses": ("addresses", "json"), "phone": ("phone", pl.String), "flags": ("flags", pl.String),
    "formerNames": ("former_names", "json"),
}
DETAIL_KEYS = set(DETAIL_TYPES)
FILING_TYPES = {
    "accessionNumber": ("accession_number", pl.String), "filingDate": ("filing_date", pl.String),
    "reportDate": ("report_date", pl.String), "acceptanceDateTime": ("acceptance_date_time", pl.String),
    "act": ("act", pl.String), "form": ("form", pl.String), "fileNumber": ("file_number", pl.String),
    "filmNumber": ("film_number", pl.String), "items": ("items", pl.String), "core_type": ("core_type", pl.String),
    "size": ("size", pl.Int64), "isXBRL": ("is_xbrl", pl.Int64), "isInlineXBRL": ("is_inline_xbrl", pl.Int64),
    "isXBRLNumeric": ("is_xbrl_numeric", pl.Int64), "primaryDocument": ("primary_document", pl.String),
    "primaryDocDescription": ("primary_doc_description", pl.String),
}
FILING_KEYS = set(FILING_TYPES)
DETAIL_SCHEMA = {col: (pl.String if t == "json" else t) for col, t in DETAIL_TYPES.values()} | \
    {"filing_files": pl.String, "source_file": pl.String}
FILING_SCHEMA = {"cik": pl.Int64} | {col: t for col, t in FILING_TYPES.values()} | {"source_file": pl.String}


class SchemaDriftError(RuntimeError):
    pass


def watermark_cutoff(overlap_days: int) -> str | None:
    """The earliest filing date this run lands, or None to land all of it."""
    select = raw_select(SOURCE, FILINGS)
    if select is None:
        return None
    latest = duckdb.sql(f"SELECT max(filing_date) FROM ({select})").fetchone()[0]
    if latest is None:
        return None
    return (date.fromisoformat(latest) - timedelta(days=overlap_days)).isoformat()


def _check_keys(name: str, got: set, want: set, what: str) -> None:
    if got != want:
        raise SchemaDriftError(f"{name}: {what} keys differ: missing {sorted(want - got)}, "
                               f"unexpected {sorted(got - want)}")


class _Filings:
    """Filing rows accumulated column by column, flushed to Parquet in batches."""

    def __init__(self, folder: Path, cutoff: str | None):
        self.folder, self.cutoff = folder, cutoff
        self.columns = {c: [] for c in FILING_SCHEMA}
        self.n_batch, self.files, self.rows = 0, 0, 0

    def add(self, name: str, cik: int, arrays: dict) -> None:
        _check_keys(name, set(arrays), FILING_KEYS, "filing")
        lengths = {len(v) for v in arrays.values()}
        if len(lengths) > 1:
            raise SchemaDriftError(f"{name}: filing arrays of different lengths {sorted(lengths)}")
        keep = range(lengths.pop() if lengths else 0)
        if self.cutoff is not None:
            dates = arrays["filingDate"]
            keep = [i for i in keep if dates[i] >= self.cutoff]
        if not keep:
            return
        self.columns["cik"].extend([cik] * len(keep))
        self.columns["source_file"].extend([name] * len(keep))
        for key, (col, _) in FILING_TYPES.items():
            values = arrays[key]
            self.columns[col].extend(values[i] for i in keep)
        self.n_batch += len(keep)
        if self.n_batch >= BATCH:
            self.flush()

    def flush(self) -> None:
        if not self.n_batch:
            return
        frame = pl.DataFrame(self.columns, schema=FILING_SCHEMA, strict=True)
        frame.write_parquet(self.folder / f"filings_{self.files:04d}.parquet")
        self.files += 1
        self.rows += self.n_batch
        self.columns = {c: [] for c in FILING_SCHEMA}
        self.n_batch = 0


def parse(zip_path: Path, folder: Path, cutoff: str | None) -> tuple[Path, list[Path], int]:
    """Parse the zip into a details Parquet and filing Parquet batches under folder.
    Returns (details file, filing files, main CIK file count)."""
    details = {c: [] for c in DETAIL_SCHEMA}
    filings = _Filings(folder, cutoff)
    mains = 0
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            main, over = MAIN.fullmatch(name), OVERFLOW.fullmatch(name)
            if not (main or over):
                if name in ALLOWED_OTHER:
                    continue
                raise SchemaDriftError(f"submissions.zip: unexpected member {name!r}")
            record = json.loads(z.read(name))
            if over:
                filings.add(name, int(over.group(1)), record)
                continue
            mains += 1
            _check_keys(name, set(record), DETAIL_KEYS | {"filings"}, "top-level")
            _check_keys(name, set(record["filings"]), {"recent", "files"}, "filings")
            if int(record["cik"]) != int(main.group(1)):
                raise SchemaDriftError(f"{name}: cik {record['cik']!r} differs from the file name")
            for key, (col, kind) in DETAIL_TYPES.items():
                value = record[key]
                if key == "cik":
                    value = int(value)
                elif kind == "json":
                    value = json.dumps(value, ensure_ascii=False)
                details[col].append(value)
            details["filing_files"].append(json.dumps(record["filings"]["files"], ensure_ascii=False))
            details["source_file"].append(name)
            filings.add(name, int(main.group(1)), record["filings"]["recent"])
    filings.flush()
    details_file = folder / "details.parquet"
    pl.DataFrame(details, schema=DETAIL_SCHEMA, strict=True).write_parquet(details_file)
    return details_file, sorted(folder.glob("filings_*.parquet")), mains


def load_submissions(run_id: str) -> dict:
    """Download, parse and land both datasets. Returns row counts and the cutoff used."""
    overlap = config.sources()["sec"]["submissions_overlap_days"]
    cutoff = watermark_cutoff(overlap)
    tmp = data_root() / "tmp" / run_id / "submissions"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        got = sec.fetch(URL, to=tmp / "submissions.zip")
        details_file, filing_files, mains = parse(got.path, tmp, cutoff)
        got.path.unlink()   # parsed; the rows are what raw keeps
        con = duckdb.connect()
        details = con.sql(f"SELECT * FROM read_parquet('{details_file.as_posix()}')")
        if filing_files:
            filings = con.sql(f"SELECT * FROM read_parquet({[f.as_posix() for f in filing_files]})")
        else:
            filings = con.sql("SELECT " + ", ".join(
                f"NULL::{'BIGINT' if t == pl.Int64 else 'VARCHAR'} AS {c}" for c, t in FILING_SCHEMA.items())
                + " WHERE false")
        n_details, n_filings = len(details), len(filings)
        if n_details != mains:
            raise SchemaDriftError(f"read back {n_details:,} detail rows, parsed {mains:,} CIK files")
        note = None if cutoff is None else f"filings dated {cutoff} or later (watermark minus {overlap} days)"
        write_raw(details, SOURCE, DETAILS, run_id, last_modified=got.last_modified, source_count=mains)
        write_raw(filings, SOURCE, FILINGS, run_id, last_modified=got.last_modified, note=note)
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for folder in (tmp.parent, tmp.parent.parent):   # tmp/<run_id>/, then tmp/, once empty
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
    return {"details": n_details, "filings": n_filings, "cutoff": cutoff}
