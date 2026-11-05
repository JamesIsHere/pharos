"""SEC's current ticker list: company_tickers_exchange.json, every run.

One raw file a run, raw/sec/tickers/<run_id>.parquet: one row per entry of the
file's `data` array (cik, name, ticker, exchange), values as sent. It lists
live tickers only, so it must never decide a past year's membership (M2 step
6); it fills the watchlist cik column (#22), reconciles the exchange list
(#46) and calibrates listing status (acceptance #9).

Strict shape (measured 2026-11-05: 10,409 rows): the top level is exactly
{'fields', 'data'}, fields exactly FIELDS, every row four values typed int,
str, str, str-or-null (158 rows had no exchange). Drift stops the load before
anything is written. The load record's source_count is the length of the
`data` array, for C32.
"""

import json

import pandas as pd

from pharos.loaders import sec
from pharos.runs import write_raw

SOURCE, DATASET = "sec", "tickers"
URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FIELDS = ["cik", "name", "ticker", "exchange"]


class SchemaDriftError(RuntimeError):
    pass


def parse(content: bytes) -> pd.DataFrame:
    doc = json.loads(content)
    if not isinstance(doc, dict) or set(doc) != {"fields", "data"}:
        raise SchemaDriftError(f"tickers: top-level keys {sorted(doc) if isinstance(doc, dict) else type(doc).__name__}, "
                               "expected ['data', 'fields']")
    if doc["fields"] != FIELDS:
        raise SchemaDriftError(f"tickers: fields {doc['fields']}, expected {FIELDS}")
    for n, row in enumerate(doc["data"]):
        ok = (isinstance(row, list) and len(row) == 4 and type(row[0]) is int
              and isinstance(row[1], str) and isinstance(row[2], str) and (row[3] is None or isinstance(row[3], str)))
        if not ok:
            raise SchemaDriftError(f"tickers: data row {n} is {row!r}, expected [int, str, str, str or null]")
    frame = pd.DataFrame(doc["data"], columns=FIELDS)
    frame["cik"] = frame["cik"].astype("int64")
    frame["exchange"] = frame["exchange"].astype("string")   # all-null stays a text column
    return frame


def load_tickers(run_id: str) -> int:
    """Fetch and land the ticker file. Returns the rows landed."""
    got = sec.fetch(URL)
    frame = parse(got.content)
    write_raw(frame, SOURCE, DATASET, run_id, last_modified=got.last_modified, source_count=len(frame))
    return len(frame)
