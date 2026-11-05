"""EDGAR full-index: every filing SEC has indexed, one master.gz per quarter.

One run writes one raw file, raw/sec/full_index/<run_id>.parquet, holding every
quarter that run landed, one row per index line:
  cik, company_name, form_type, date_filed, filename  (the index's five fields)
  quarter        e.g. '2026Q4'
  last_modified  that quarter's HTTP Last-Modified

Which quarters a run lands (M2 step 1): every quarter from 1993Q1 to the
current one that no earlier raw file holds, plus the current and previous
quarters every run, since SEC keeps adding to both. The first run lands all of
them (about 27M rows). The current quarter is the New York date's: SEC runs on
Eastern time, as the price pulls do (D79, D118).

Rules applied here:
- Parsing is strict: the column line must read exactly as HEADER, a line of
  dashes must follow it, and every row must have exactly five fields. Any
  drift stops the whole load before anything is written.
- Text is decoded as latin-1, as the spike did (acceptance #2 reproduces it);
  every 2026-11-04 index file decoded identically as UTF-8.
- Rows are written to temporary text files under <data root>/tmp/ and handed
  to write_raw as a DuckDB relation, so 27M rows stream to disk rather than
  sit in memory (D93). The temp folder is removed whether or not the load
  succeeds.
- A stale copy is never landed (#106, D121). SEC serves older copies of a
  quarter's file beside the current one (measured 2026-11-05: 6 of 8 fetches
  of 2026Q4 were a copy 6 days old). A fetch whose Last-Modified is earlier
  than the newest one raw already holds for that quarter is left out of the
  run, and the load record's note says so. Raw only moves forward in time;
  a quarter skipped tonight is landed on a later night, and a long run of
  skips shows as C25 (latest filing too old). A copy with no Last-Modified
  can't be compared and is landed. A first landing can't be judged stale.
- Raw keeps rows as SEC sent them. Deduplication across landings is staging's
  job (D18, D84).
"""

import gzip
import shutil
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import duckdb

from pharos.loaders import sec
from pharos.paths import data_root
from pharos.runs import raw_select, write_raw

SOURCE, DATASET = "sec", "full_index"
FIRST = (1993, 1)
URL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/master.gz"
HEADER = "CIK|Company Name|Form Type|Date Filed|Filename"
NEW_YORK = ZoneInfo("America/New_York")
COLUMNS = {"cik": "BIGINT", "company_name": "VARCHAR", "form_type": "VARCHAR",
           "date_filed": "DATE", "filename": "VARCHAR", "quarter": "VARCHAR"}


class SchemaDriftError(RuntimeError):
    pass


def quarter_of(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3 + 1


def quarters(through: tuple[int, int]) -> list[tuple[int, int]]:
    """Every quarter from 1993Q1 through `through`, in order."""
    out, (y, q) = [], FIRST
    while (y, q) <= through:
        out.append((y, q))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def label(yq: tuple[int, int]) -> str:
    return f"{yq[0]}Q{yq[1]}"


def landed() -> dict[str, datetime | None]:
    """Quarters some earlier raw file holds, each with the newest Last-Modified landed for it."""
    select = raw_select(SOURCE, DATASET)
    if select is None:
        return {}
    return dict(duckdb.sql(f"SELECT quarter, max(last_modified) FROM ({select}) GROUP BY quarter").fetchall())


def due(today: date, have: dict | None = None) -> list[tuple[int, int]]:
    """The quarters this run lands: every missing one, plus current and previous."""
    every = quarters(quarter_of(today))
    have = landed() if have is None else have
    recent = set(every[-2:])
    return [yq for yq in every if label(yq) not in have or yq in recent]


def parse(content: bytes, quarter: str) -> list[str]:
    """master.gz bytes to the index's rows, each 'cik|name|form|date|filename'."""
    lines = gzip.decompress(content).decode("latin-1").splitlines()
    try:
        at = lines.index(HEADER)
    except ValueError:
        raise SchemaDriftError(f"{quarter}: no column line {HEADER!r}") from None
    if at + 1 >= len(lines) or not lines[at + 1] or set(lines[at + 1]) != {"-"}:
        raise SchemaDriftError(f"{quarter}: the column line is not followed by a line of dashes")
    rows = lines[at + 2:]
    for n, row in enumerate(rows, start=at + 3):
        if row.count("|") != 4:
            raise SchemaDriftError(f"{quarter}: line {n} has {row.count('|') + 1} fields, not 5: {row!r}")
    return rows


def load_index(run_id: str, today: date | None = None) -> dict:
    """Fetch and land every due quarter. Returns {quarter: rows} for the
    quarters landed; a skipped stale copy is not in it (see the load record's note)."""
    today = today or datetime.now(NEW_YORK).date()
    tmp = data_root() / "tmp" / run_id / DATASET
    tmp.mkdir(parents=True, exist_ok=True)
    counts, stamps, skipped = {}, {}, []
    have = landed()
    try:
        for yq in due(today, have):
            quarter = label(yq)
            got = sec.fetch(URL.format(year=yq[0], q=yq[1]))
            held = have.get(quarter)
            if got.last_modified is not None and held is not None and got.last_modified < held:
                skipped.append(f"{quarter} (Last-Modified {got.last_modified.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC "
                               f"< {held.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC already landed)")
                continue
            rows = parse(got.content, quarter)
            stamps[quarter] = got.last_modified
            # one text file per quarter; '|' cannot occur inside a field, since
            # every row was checked to have exactly five
            with open(tmp / f"{quarter}.txt", "w", encoding="utf-8", newline="\n") as out:
                for row in rows:
                    out.write(f"{row}|{quarter}\n")
            counts[quarter] = len(rows)

        con = duckdb.connect()
        con.execute("CREATE TABLE stamps (quarter VARCHAR, last_modified TIMESTAMPTZ)")
        if stamps:   # empty when every due quarter was a stale copy
            con.executemany("INSERT INTO stamps VALUES (?, ?)", list(stamps.items()))
        files = sorted(p.as_posix() for p in tmp.glob("*.txt") if p.stat().st_size > 0)
        if files:
            columns = ", ".join(f"'{c}': '{t}'" for c, t in COLUMNS.items())
            index = con.sql(f"""
                SELECT i.*, s.last_modified
                FROM read_csv({files}, delim = '|', header = false, quote = '', escape = '',
                              columns = {{{columns}}}) AS i
                JOIN stamps AS s USING (quarter)""")
        else:   # every due quarter was empty: an empty download is still recorded
            index = con.sql("SELECT " + ", ".join(f"NULL::{t} AS {c}" for c, t in COLUMNS.items())
                            + ", NULL::TIMESTAMPTZ AS last_modified WHERE false")
        parsed = len(index)
        if parsed != sum(counts.values()):
            raise SchemaDriftError(f"read back {parsed:,} index rows, parsed {sum(counts.values()):,}")
        newest = max((s for s in stamps.values() if s is not None), default=None)
        note = "stale copy skipped: " + "; ".join(skipped) if skipped else None
        write_raw(index, SOURCE, DATASET, run_id, last_modified=newest, note=note)
        con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for folder in (tmp.parent, tmp.parent.parent):   # tmp/<run_id>/, then tmp/, once empty
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
    return counts
