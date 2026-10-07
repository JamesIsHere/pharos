"""Yahoo Finance prices via yfinance (unofficial: wrapped, and checked on every pull).

One run writes one raw file, raw/yf/prices/<run_id>.parquet, holding every
watchlist ticker. Splits and dividends arrive as columns of the same table
(0 on ordinary days), so they stay in that file; the transform extracts events.

Rules applied here (CLAUDE.md, Sources):
- auto_adjust is always passed explicitly, from config (false: Close is
  split-adjusted, not dividend-adjusted).
- The column set must match exactly what yfinance 1.7.0 returned when verified
  on 2026-10-07. Any drift stops the whole load before anything is written.
- obs_date is the New York calendar date of the bar, never a UTC conversion.
- The download ends before today (New York time), so a bar still trading is
  never stored as a close.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from pharos import config
from pharos.runs import write_raw

NEW_YORK = ZoneInfo("America/New_York")
EXPECTED_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume",
                    "Dividends", "Stock Splits"]
RAW_COLUMNS = {"Open": "open", "High": "high", "Low": "low", "Close": "close",
               "Adj Close": "adj_close", "Volume": "volume",
               "Dividends": "dividends", "Stock Splits": "stock_splits"}


class SchemaDriftError(RuntimeError):
    pass


def fetch(symbol: str, start: date, end: date, auto_adjust: bool) -> pd.DataFrame:
    """One ticker's daily bars from yfinance, untouched. end is exclusive."""
    return yf.Ticker(symbol).history(start=start.isoformat(), end=end.isoformat(),
                                     interval="1d", auto_adjust=auto_adjust,
                                     actions=True)


def normalize(bars: pd.DataFrame, ticker: str, symbol: str) -> pd.DataFrame:
    """Check the shape, then give raw stable column names. Values are not changed."""
    if bars.empty:
        return pd.DataFrame()
    if list(bars.columns) != EXPECTED_COLUMNS:
        raise SchemaDriftError(f"{symbol}: columns {list(bars.columns)}, expected {EXPECTED_COLUMNS}")
    tz = getattr(bars.index, "tz", None)
    if tz is None or str(tz) != "America/New_York":
        raise SchemaDriftError(f"{symbol}: index timezone {tz}, expected America/New_York")

    out = bars.rename(columns=RAW_COLUMNS)
    out.insert(0, "obs_date", bars.index.date)
    out.insert(0, "yahoo_symbol", symbol)
    out.insert(0, "ticker", ticker)
    return out.reset_index(drop=True)


def load_prices(run_id: str):
    """Download every watchlist ticker and write one raw file for the run."""
    cfg = config.sources()
    auto_adjust = cfg["yahoo"]["auto_adjust"]
    end = datetime.now(NEW_YORK).date()  # exclusive: today's bar is excluded

    frames = []
    for row in config.watchlist():
        start = row["active_from"] or cfg["backfill_start"]
        bars = fetch(row["yahoo_symbol"], start, end, auto_adjust)
        frames.append(normalize(bars, row["ticker"], row["yahoo_symbol"]))

    download = pd.concat([f for f in frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in frames) else pd.DataFrame()
    return write_raw(download, "yf", "prices", run_id)
