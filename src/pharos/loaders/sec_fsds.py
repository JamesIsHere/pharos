"""SEC Financial Statement Data Sets: the presentation table, pre.txt (D174).

SEC publishes one zip per quarter (sub, tag, num and pre). pre.txt has one row
per line of each financial statement of each filing in the quarter: which
statement (stmt), which concept (tag), in what order. It is the one thing
Company Facts does not carry: where on the statements a concept is presented.
Measured 2026-11-14 on 2026q3: a 65 MB zip; pre.txt 731,170 rows, 6,516
filings, (adsh, report, line) unique. num.txt (the facts, 875 MB unzipped) is
not landed: Company Facts holds the facts.

A run writes raw/sec/fsds_pre/<run_id>.parquet: pre.txt's ten columns as sent,
all text, plus quarter ('2026q3', SEC's own file name) and last_modified (the
zip's HTTP Last-Modified).

Which quarters a run lands: every quarter from sec.fsds_first_quarter
(config/sources.yaml) to the last complete one, by the New York date, that raw
does not hold. A published quarter's zip does not change, so a quarter is
landed once. The newest due quarter may not be published yet (HTTP 404): it is
left for a later run and the load record's note says so. A 404 for any older
quarter stops the load. A run with nothing to land writes an empty load record.

Parsing is strict: pre.txt's header line must name exactly COLUMNS, in order.
Bytes come through loaders/sec.py: SEC's User-Agent and request rate.
"""

import re
import shutil
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb

from pharos import config
from pharos.loaders import sec
from pharos.paths import data_root
from pharos.runs import raw_select, write_raw

SOURCE, DATASET = "sec", "fsds_pre"
URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{quarter}.zip"
COLUMNS = ("adsh", "report", "line", "stmt", "inpth", "rfile", "tag", "version", "plabel", "negating")
NEW_YORK = ZoneInfo("America/New_York")


class SchemaDriftError(RuntimeError):
    pass


def label(year: int, q: int) -> str:
    return f"{year}q{q}"


def due(today: date, first: str, have: set[str]) -> list[str]:
    """Quarters from `first` to the last complete one before `today` that `have` lacks."""
    m = re.fullmatch(r"([0-9]{4})q([1-4])", str(first))
    if not m:
        raise SchemaDriftError(f"sec.fsds_first_quarter must read like 2026q3, got {first!r} (sources.yaml)")
    y, q = int(m.group(1)), int(m.group(2))
    this = (today.year, (today.month - 1) // 3 + 1)
    out = []
    while (y, q) < this:
        if label(y, q) not in have:
            out.append(label(y, q))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def landed() -> set[str]:
    select = raw_select(SOURCE, DATASET)
    if select is None:
        return set()
    return {r[0] for r in duckdb.sql(f"SELECT DISTINCT quarter FROM ({select})").fetchall()}


def load_pre(run_id: str, today: date | None = None) -> dict:
    """Fetch and land pre.txt for every due quarter. Returns {quarter: rows}."""
    today = today or datetime.now(NEW_YORK).date()
    quarters = due(today, config.sources()["sec"]["fsds_first_quarter"], landed())
    tmp = data_root() / "tmp" / run_id / DATASET
    tmp.mkdir(parents=True, exist_ok=True)
    counts, stamps, note = {}, {}, None
    con = duckdb.connect()
    try:
        for quarter in quarters:
            zip_path = tmp / f"{quarter}.zip"
            try:
                got = sec.fetch(URL.format(quarter=quarter), to=zip_path)
            except sec.SecFetchError as e:
                if e.status == 404 and quarter == quarters[-1]:
                    note = f"{quarter} is not published yet (HTTP 404)"
                    continue
                raise
            if got.last_modified is None:
                raise SchemaDriftError(f"{quarter}.zip: no Last-Modified header; the vintage is dated by it")
            text = tmp / f"{quarter}-pre.txt"
            with zipfile.ZipFile(zip_path) as z, z.open("pre.txt") as src, open(text, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            zip_path.unlink()
            with open(text, encoding="utf-8") as f:
                header = f.readline().rstrip("\r\n").split("\t")
            if header != list(COLUMNS):
                raise SchemaDriftError(f"{quarter} pre.txt: columns {header}, expected {list(COLUMNS)}")
            counts[quarter] = con.execute(
                f"SELECT count(*) FROM read_csv('{text.as_posix()}', delim = '\t', header = true, quote = '', "
                "escape = '', all_varchar = true)").fetchone()[0]
            stamps[quarter] = got.last_modified

        con.execute("CREATE TABLE stamps (quarter VARCHAR, last_modified TIMESTAMPTZ)")
        if stamps:
            con.executemany("INSERT INTO stamps VALUES (?, ?)", list(stamps.items()))
        selects = [f"SELECT *, '{q}' AS quarter FROM read_csv('{(tmp / f'{q}-pre.txt').as_posix()}', "
                   "delim = '\t', header = true, quote = '', escape = '', all_varchar = true)" for q in counts]
        if selects:
            rows = con.sql(f"SELECT p.*, s.last_modified FROM ({' UNION ALL '.join(selects)}) AS p "
                           "JOIN stamps AS s USING (quarter)")
        else:   # nothing due, or the one due quarter is not published: still recorded
            rows = con.sql("SELECT " + ", ".join(f"NULL::VARCHAR AS {c}" for c in COLUMNS)
                           + ", NULL::VARCHAR AS quarter, NULL::TIMESTAMPTZ AS last_modified WHERE false")
            note = note or "nothing due: every complete quarter is already landed"
        if len(rows) != sum(counts.values()):
            raise SchemaDriftError(f"read back {len(rows):,} rows, counted {sum(counts.values()):,}")
        newest = max(stamps.values(), default=None)
        write_raw(rows, SOURCE, DATASET, run_id, last_modified=newest, note=note)
    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)
        for folder in (tmp.parent, tmp.parent.parent):   # tmp/<run_id>/, then tmp/, once empty
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
    return counts
