"""Yahoo Finance prices via yfinance (unofficial: wrapped, and checked on every pull).

One run writes one raw file, raw/yf/prices/<run_id>.parquet, holding every
watchlist ticker. Splits and dividends arrive as columns of the same table
(0 on ordinary days), so they stay in that file; the transform extracts events.

Rules applied here (CLAUDE.md, Sources):
- auto_adjust is always passed explicitly, from config (false: Close is
  split-adjusted, not dividend-adjusted).
- The column set must match exactly what yfinance 1.7.0 returned when verified
  on 2026-10-07. Any drift stops the whole load before anything is written.
- An empty reply to an active ticker's incremental pull also stops the load:
  the window holds the last stored date, so empty means an outage (#58).
- obs_date is the New York calendar date of the bar, never a UTC conversion.
- The download ends before today (New York time), so a bar still trading is
  never stored as a close.
- Every row carries the window it was pulled with: pull_start (inclusive) and
  pull_end (exclusive). A pull that starts at the ticker's first expected date
  is a full-history pull, which sets the price vintage (D18, D20).

Incremental pulls (D21). Per ticker, each run:
- No stored rows yet: full pull ("bootstrap").
- Otherwise pull from the last stored date minus overlap_days, then compare the
  overlap's closes with the stored ones (current vintage, latest run per date):
  - all agree within REBASE_TOLERANCE: keep the incremental rows ("incremental")
  - all differ by one common ratio: Yahoo re-based history (a split), so pull
    the full history, which starts a new vintage ("rebase")
  - some differ: a correction. Keep the incremental rows under the old vintage
    so C14 sees the conflict and blocks the publish ("correction")
  - a nonzero stock_splits in the new rows also forces a full pull
    ("split_flag"); a false alarm costs one extra vintage, never a wrong value.
  - no overlap date at all (Yahoo returned only dates after the stored ones):
    nothing to compare the basis with, so a full pull ("no_overlap", D71).
- The current full pull started after today's first expected date (the
  watchlist or backfill_start moved earlier): full pull ("window_extended", D72).
- Which pulls were full is what each pull recorded (runs.FULL_PULL_SQL, D72).
  The reason is stored on every row as pull_reason.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import yfinance as yf

from pharos import config
from pharos.runs import raw_select, write_raw
from pharos.stage import complete_runs

NEW_YORK = ZoneInfo("America/New_York")
# Relative gap that counts as "the same close": ~100x float32 noise (~1e-7),
# far below any split ratio (D21).
REBASE_TOLERANCE = 1e-5
EXPECTED_COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume",
                    "Dividends", "Stock Splits"]
RAW_COLUMNS = {"Open": "open", "High": "high", "Low": "low", "Close": "close",
               "Adj Close": "adj_close", "Volume": "volume",
               "Dividends": "dividends", "Stock Splits": "stock_splits"}


class SchemaDriftError(RuntimeError):
    pass


class EmptyPullError(RuntimeError):
    """An active ticker's incremental window came back empty (#58)."""


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

    # complete runs only, the set staging reads: an incomplete run's rows are not
    # what serving holds, so they can't be the basis the overlap is compared to (#59)
    stored_select = raw_select("yf", "prices", complete_runs())
    frames = []
    for row in config.watchlist():
        first = row["active_from"] or cfg["backfill_start"]
        stored, epoch_start = _stored_closes(stored_select, row["ticker"])
        if stored.empty:
            start, reason = first, "bootstrap"
        elif epoch_start is not None and epoch_start > first:
            # the watchlist or backfill_start moved earlier than the current full
            # pull reached: pull the whole window again, a new vintage (D72, #61)
            start, reason = first, "window_extended"
        else:
            start = stored["obs_date"].max() - timedelta(days=cfg["yahoo"]["overlap_days"])
            reason = None
        frame = normalize(fetch(row["yahoo_symbol"], start, end, auto_adjust), row["ticker"], row["yahoo_symbol"])
        active = row["active_to"] is None or row["active_to"] >= end
        if reason is None and frame.empty and active:
            # the window holds the last stored date, a session Yahoo answered
            # before: empty is an outage or a rate limit, not data. Fail the whole
            # load before anything is written, as schema drift does (#58)
            raise EmptyPullError(f"{row['yahoo_symbol']}: no bars from {start} to {end}, "
                                 f"though the window holds the stored {stored['obs_date'].max()}")
        if reason is None:
            reason = classify(stored, frame)
            if reason in ("rebase", "split_flag", "no_overlap"):
                start = first
                frame = normalize(fetch(row["yahoo_symbol"], start, end, auto_adjust),
                                  row["ticker"], row["yahoo_symbol"])
        if not frame.empty:
            frame["pull_start"] = start
            frame["pull_end"] = end
            frame["pull_reason"] = reason
        frames.append(frame)

    download = pd.concat([f for f in frames if not f.empty], ignore_index=True) \
        if any(not f.empty for f in frames) else pd.DataFrame()
    return write_raw(download, "yf", "prices", run_id)


def classify(stored: pd.DataFrame, new: pd.DataFrame) -> str:
    """How an incremental pull relates to what is stored (D21)."""
    if new.empty:
        return "incremental"
    # only splits on dates not yet stored: the overlap re-reads old split rows
    fresh = new[new["obs_date"] > stored["obs_date"].max()]
    if (fresh["stock_splits"] != 0).any():
        return "split_flag"
    both = stored.merge(new[["obs_date", "close"]], on="obs_date", suffixes=("_stored", "_new"))
    if both.empty:
        # no shared date: the basis can't be compared, so pull the full history
        # (one basis for every value). "correction" would not block, because C14
        # needs a shared date to see a conflict (D71, #57)
        return "no_overlap"
    ratio = both["close_stored"] / both["close_new"]
    if ((ratio - 1).abs() <= REBASE_TOLERANCE).all():
        return "incremental"
    # one shared date can't tell a re-basing from a correction: treat it as a
    # correction, which blocks for review instead of starting a vintage
    if len(ratio) >= 2 and ((ratio / ratio.iloc[0] - 1).abs() <= REBASE_TOLERANCE).all():
        return "rebase"
    return "correction"


def _stored_closes(stored_select: str | None, ticker: str) -> tuple[pd.DataFrame, date | None]:
    """The ticker's closes under its current vintage: rows from its latest
    full-history pull onward, latest run per date (empty if nothing stored),
    and the date that full pull started from (None for a pre-D20 file, or when
    nothing is stored). Full pull = what the pull recorded (runs.FULL_PULL_SQL)."""
    empty = pd.DataFrame(columns=["obs_date", "close"])
    if stored_select is None:
        return empty, None
    epoch = duckdb.execute(f"""
        WITH raw AS ({stored_select})
        SELECT run_id, any_value(pull_start) FROM raw
        WHERE ticker = $ticker AND full_pull
        GROUP BY run_id ORDER BY run_id DESC LIMIT 1""", {"ticker": ticker}).fetchone()
    if epoch is None:
        return empty, None
    rows = duckdb.execute(f"""
        WITH raw AS ({stored_select})
        SELECT obs_date, close FROM raw
        WHERE ticker = $ticker AND run_id >= $epoch
        QUALIFY row_number() OVER (PARTITION BY obs_date ORDER BY run_id DESC) = 1
        ORDER BY obs_date""", {"ticker": ticker, "epoch": epoch[0]}).fetchall()
    # plain Python dates, the same type normalize() gives obs_date
    return pd.DataFrame(rows, columns=["obs_date", "close"]), epoch[1]
