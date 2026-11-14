"""UN World Population Prospects: total population by location and year (D174).

One file from the UN Population Division, <revision>_TotalPopulationBySex.csv.gz:
one row per location, projection variant and year, population as of July 1 in
thousands (measured 2026-11-14 on WPP2024: 720,210 rows, 555 locations of which
237 are countries, 18 variants, 1950 to 2100; (LocID, VarID, Time) unique).
A run writes raw/un/wpp_population/<run_id>.parquet with the file's 18 columns
as sent, plus revision and last_modified (the file's HTTP Last-Modified).

Rules applied here:
- The revision is named in config/sources.yaml (un.wpp_revision). The UN
  publishes one every two years under a new file name; nothing here guesses
  the next name.
- The file is landed only when it has changed. A HEAD request reads its
  Last-Modified; when raw already holds a landing with that stamp for this
  revision, nothing is downloaded and the run's load record says so in its
  note (an empty download, D121). No Last-Modified stops the load: staging
  dates the vintage by it.
- Parsing is strict: the header line must name exactly COLUMNS, in order, and
  every value must read as its declared type.
- The download goes to <data root>/tmp/<run_id>/ and is removed whether or not
  the load succeeds.
"""

import gzip
import shutil
import urllib.request
from datetime import datetime
from email.utils import parsedate_to_datetime

import duckdb

from pharos import config
from pharos.paths import data_root
from pharos.runs import write_raw

SOURCE, DATASET = "un", "wpp_population"
URL = ("https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/CSV_FILES/"
       "{revision}_TotalPopulationBySex.csv.gz")
UA = {"User-Agent": "pharos"}
TIMEOUT = 600
# the file's columns, with the types DuckDB read them as on 2026-11-14
COLUMNS = {"SortOrder": "VARCHAR", "LocID": "BIGINT", "Notes": "VARCHAR", "ISO3_code": "VARCHAR",
           "ISO2_code": "VARCHAR", "SDMX_code": "VARCHAR", "LocTypeID": "VARCHAR", "LocTypeName": "VARCHAR",
           "ParentID": "VARCHAR", "Location": "VARCHAR", "VarID": "BIGINT", "Variant": "VARCHAR",
           "Time": "BIGINT", "MidPeriod": "DOUBLE", "PopMale": "DOUBLE", "PopFemale": "DOUBLE",
           "PopTotal": "DOUBLE", "PopDensity": "DOUBLE"}


class SchemaDriftError(RuntimeError):
    pass


def _stamp(value: str | None, url: str) -> datetime:
    if value is None:
        raise SchemaDriftError(f"{url}: no Last-Modified header; the vintage is dated by it")
    stamp = parsedate_to_datetime(value)
    if stamp.tzinfo is None:
        raise SchemaDriftError(f"{url}: Last-Modified {value!r} has no timezone")
    return stamp


def head(url: str) -> datetime:
    """The file's Last-Modified, without downloading it."""
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers=UA), timeout=TIMEOUT) as r:
        return _stamp(r.headers.get("Last-Modified"), url)


def download(url: str, to) -> datetime:
    """Stream the file to `to`; returns its Last-Modified."""
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=TIMEOUT) as r, \
            open(to, "wb") as out:
        stamp = _stamp(r.headers.get("Last-Modified"), url)
        shutil.copyfileobj(r, out, 1 << 20)
    return stamp


def landed(revision: str) -> datetime | None:
    """The newest Last-Modified raw holds for this revision, or None."""
    folder = data_root() / "raw" / SOURCE / DATASET
    files = sorted(f.as_posix() for f in folder.glob("*.parquet"))
    if not files:
        return None
    return duckdb.sql(f"SELECT max(last_modified) FROM read_parquet({files}, union_by_name = true) "
                      f"WHERE revision = '{revision}'").fetchone()[0]


def load_population(run_id: str) -> int:
    """Land the configured revision's file if it changed. Returns the rows landed."""
    revision = config.sources()["un"]["wpp_revision"]
    if not isinstance(revision, str) or not revision.startswith("WPP") or not revision[3:].isdigit():
        raise SchemaDriftError(f"un.wpp_revision must read WPP<year>, got {revision!r} (sources.yaml)")
    url = URL.format(revision=revision)
    stamp, held = head(url), landed(revision)
    con = duckdb.connect()
    if held is not None and stamp == held:
        empty = con.sql("SELECT " + ", ".join(f"NULL::{t} AS {c}" for c, t in COLUMNS.items())
                        + ", NULL::VARCHAR AS revision, NULL::TIMESTAMPTZ AS last_modified WHERE false")
        write_raw(empty, SOURCE, DATASET, run_id, last_modified=stamp,
                  note=f"unchanged: {revision} Last-Modified {stamp:%Y-%m-%d %H:%M} UTC is already landed")
        con.close()
        return 0

    tmp = data_root() / "tmp" / run_id / DATASET
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        gz = tmp / f"{revision}.csv.gz"
        stamp = download(url, gz)
        with gzip.open(gz, "rt", encoding="utf-8-sig") as f:
            header = f.readline().rstrip("\r\n").split(",")
        if header != list(COLUMNS):
            raise SchemaDriftError(f"{url}: columns {header}, expected {list(COLUMNS)}")
        columns = ", ".join(f"'{c}': '{t}'" for c, t in COLUMNS.items())
        rows = con.sql(f"""
            SELECT *, '{revision}' AS revision, TIMESTAMPTZ '{stamp.isoformat()}' AS last_modified
            FROM read_csv('{gz.as_posix()}', header = true, columns = {{{columns}}})""")
        n = len(rows)
        if n == 0:
            raise SchemaDriftError(f"{url}: the file holds no rows")
        write_raw(rows, SOURCE, DATASET, run_id, last_modified=stamp)
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for folder in (tmp.parent, tmp.parent.parent):   # tmp/<run_id>/, then tmp/, once empty
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
    return n
