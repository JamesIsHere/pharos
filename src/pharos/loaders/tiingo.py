"""Tiingo end-of-day prices: the second source, for reconciliation only (D35).

One run writes one raw file, raw/tiingo/prices/<run_id>.parquet, holding every
watchlist ticker's full history (D36: full every run, so any sampled date's
split-adjusted close comes from one snapshot, never stitched across runs).

Tiingo data never reaches serving/. It is not part of a run's completeness
(stage.DATASETS): a run without a Tiingo file is still a complete run, so the
history staged from runs before D35 stays intact.

Rules applied here:
- The key comes from TIINGO_API_KEY (.env) and travels in the Authorization
  header, never the URL, so it can't appear in an error message or a log.
- The row shape must match ROW_KEYS exactly. Any drift stops the whole load
  before anything is written. ROW_KEYS is taken from Tiingo's documentation
  and is unverified against a live response until the first live pull
  (open issue #33); a wrong guess fails loudly, it can't degrade silently.
- Values are stored as sent. close is Tiingo's raw (unadjusted) close;
  split_factor is Tiingo's own, so the split-adjusted close is derived in
  DuckDB from Tiingo alone, never with Yahoo's splits (D35).
- obs_date is the date part of Tiingo's timestamp (midnight UTC labels the
  trading day); it is never converted between zones.
- A ticker Tiingo doesn't know (HTTP 404) contributes no rows and is reported;
  any other HTTP error stops the load.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

import pandas as pd

from pharos import config
from pharos.runs import write_raw

API = "https://api.tiingo.com/tiingo/daily"
ROW_KEYS = {"date", "open", "high", "low", "close", "volume",
            "adjOpen", "adjHigh", "adjLow", "adjClose", "adjVolume",
            "divCash", "splitFactor"}
RAW_COLUMNS = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume",
               "adjOpen": "adj_open", "adjHigh": "adj_high", "adjLow": "adj_low",
               "adjClose": "adj_close", "adjVolume": "adj_volume",
               "divCash": "div_cash", "splitFactor": "split_factor"}


class SchemaDriftError(RuntimeError):
    pass


class TickerNotFound(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("TIINGO_API_KEY")
    if not key:
        raise RuntimeError("TIINGO_API_KEY is not set (add it to .env)")
    return key


def symbol(ticker: str) -> str:
    """Tiingo writes share classes with a hyphen: BRK.B -> BRK-B."""
    return ticker.replace(".", "-")


def fetch(tiingo_symbol: str, start: date, end: date) -> list:
    """Daily bars from start to end inclusive, untouched."""
    query = urllib.parse.urlencode({"startDate": start.isoformat(), "endDate": end.isoformat(),
                                    "format": "json", "resampleFreq": "daily"})
    request = urllib.request.Request(f"{API}/{urllib.parse.quote(tiingo_symbol)}/prices?{query}",
                                     headers={"Authorization": f"Token {_api_key()}",
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise TickerNotFound(tiingo_symbol) from None
        raise RuntimeError(f"Tiingo {tiingo_symbol}: HTTP {e.code} {e.reason}") from None


def normalize(rows: list, ticker: str, tiingo_symbol: str, start: date, end: date) -> pd.DataFrame:
    """Check the shape, then give raw stable column names. Values are not changed."""
    if not isinstance(rows, list):
        raise SchemaDriftError(f"{tiingo_symbol}: response is {type(rows).__name__}, expected a list")
    for r in rows:
        if set(r) != ROW_KEYS:
            raise SchemaDriftError(f"{tiingo_symbol}: row keys {sorted(r)}, expected {sorted(ROW_KEYS)}")
    if not rows:
        return pd.DataFrame()
    stamps = [r["date"] for r in rows]
    if not all(isinstance(s, str) and s[10:] in ("T00:00:00.000Z", "T00:00:00Z") for s in stamps):
        raise SchemaDriftError(f"{tiingo_symbol}: dates are not midnight UTC, e.g. {stamps[0]!r}")

    out = pd.DataFrame(rows).rename(columns=RAW_COLUMNS)
    out.insert(0, "obs_date", [date.fromisoformat(s[:10]) for s in stamps])
    out = out.drop(columns=["date"])
    out.insert(0, "tiingo_symbol", tiingo_symbol)
    out.insert(0, "ticker", ticker)
    out["pull_start"], out["pull_end"] = start, end
    return out[["ticker", "tiingo_symbol", "obs_date"] + list(RAW_COLUMNS.values()) + ["pull_start", "pull_end"]]


def load_prices(run_id: str, today: date | None = None) -> dict:
    """Pull every watchlist ticker's full history; write one raw file for the run.
    Everything is fetched and checked first, so drift anywhere writes nothing.
    Returns {"rows": n, "not_found": [tickers]}."""
    start = config.sources()["backfill_start"]
    end = today or date.today()
    frames, not_found = [], []
    for w in config.watchlist():
        sym = symbol(w["ticker"])
        try:
            rows = fetch(sym, start, end)
        except TickerNotFound:
            not_found.append(w["ticker"])
            continue
        frames.append(normalize(rows, w["ticker"], sym, start, end))
    frames = [f for f in frames if not f.empty]
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    write_raw(out, "tiingo", "prices", run_id)
    return {"rows": len(out), "not_found": not_found}
