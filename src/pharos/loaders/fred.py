"""FRED/ALFRED macro series, every vintage (design.md D13).

One run writes two raw files, each holding every configured series:
- raw/fred/observations/<run_id>.parquet: every (obs_date, vintage) row ALFRED has.
  realtime_start is the vintage: the date that value became the published one.
- raw/fred/series/<run_id>.parquet: the series metadata history. Units change
  across vintages (GDPC1 has been in 1987, 1992, ... 2017 dollars), so staging
  joins each vintage to the metadata in force on its realtime_start. Storing only
  today's metadata would label old vintages with today's units.

Rules applied here:
- The full history is pulled every run (D18: raw is a snapshot log; dedup happens
  in staging).
- Response shapes must match what the API returned when verified on 2026-10-07.
  A metadata record may omit notes, which is then stored as NULL (D143).
- Each series succeeds or fails on its own (D144). A series whose fetch fails, or
  whose observations or metadata drift or come back cut short, is left out of
  this run's raw files whole, and the series and its error go in the
  observations load record's note as JSON: [{"fred_id": ..., "error": ...}].
  The other series land. Staging reads every earlier run's raw too, so a series
  left out keeps its earlier vintages; C41 turns health red naming it. Only when
  every series fails (FRED down, a dead key) does the load fail, writing nothing.
- value is stored exactly as sent, as text. FRED sends "." for a missing value;
  deciding what that means is staging's job, and C14 compares raw values verbatim.
- The API key comes from FRED_API_KEY and never appears in an error message.
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import date

import pandas as pd

from pharos import config
from pharos.runs import write_raw

API = "https://api.stlouisfed.org/fred"
ALL_TIME = {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31"}  # ALFRED's "every vintage"
LIMIT = 100_000  # API maximum rows per request

OBSERVATION_KEYS = {"realtime_start", "realtime_end", "date", "value"}
SERIES_KEYS = {"id", "realtime_start", "realtime_end", "title", "observation_start",
               "observation_end", "frequency", "frequency_short", "units", "units_short",
               "seasonal_adjustment", "seasonal_adjustment_short", "last_updated",
               "popularity", "notes"}
OPTIONAL_SERIES_KEYS = {"notes"}   # may be absent; stored as NULL. Read nowhere past raw (D143)


class SchemaDriftError(RuntimeError):
    pass


class SeriesLoadError(RuntimeError):
    """Every configured series failed: nothing is written."""


def _api_key() -> str:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError("FRED_API_KEY is not set (user environment variable)")
    return key


def _get(endpoint: str, **params) -> dict:
    params.update(api_key=_api_key(), file_type="json")
    url = f"{API}/{endpoint}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp)


def fetch_observations(fred_id: str) -> dict:
    """Every vintage of every observation, untouched."""
    return _get("series/observations", series_id=fred_id, limit=LIMIT, **ALL_TIME)


def fetch_series(fred_id: str) -> dict:
    """The series' metadata history, one row per period its metadata held, untouched."""
    return _get("series", series_id=fred_id, **ALL_TIME)


def normalize_observations(resp: dict, fred_id: str) -> pd.DataFrame:
    """Check the shape and completeness, then give raw stable column names."""
    rows = resp.get("observations")
    if rows is None:
        raise SchemaDriftError(f"{fred_id}: no 'observations' in response")
    if resp.get("count") != len(rows):
        raise SchemaDriftError(f"{fred_id}: response says count={resp.get('count')}, "
                               f"got {len(rows)} rows (truncated?)")
    for r in rows:
        if set(r) != OBSERVATION_KEYS:
            raise SchemaDriftError(f"{fred_id}: observation keys {sorted(r)}, "
                                   f"expected {sorted(OBSERVATION_KEYS)}")
    if not rows:
        return pd.DataFrame()

    return pd.DataFrame({
        "fred_id": fred_id,
        "obs_date": [date.fromisoformat(r["date"]) for r in rows],
        "realtime_start": [date.fromisoformat(r["realtime_start"]) for r in rows],
        "realtime_end": [date.fromisoformat(r["realtime_end"]) for r in rows],
        "value": [r["value"] for r in rows],
    })


def normalize_series(resp: dict, fred_id: str) -> pd.DataFrame:
    """Check the shape of the metadata history. At least one row is required."""
    rows = resp.get("seriess")  # sic: FRED's key
    if not rows:
        raise SchemaDriftError(f"{fred_id}: no metadata rows in response")
    for r in rows:
        missing = SERIES_KEYS - set(r)
        if set(r) - SERIES_KEYS or missing - OPTIONAL_SERIES_KEYS:
            raise SchemaDriftError(f"{fred_id}: series keys {sorted(r)}, "
                                   f"expected {sorted(SERIES_KEYS)}")
        for k in missing:
            r[k] = None   # D143: FRED omits notes when a series has none (the IMF GDP series)
        if r["id"] != fred_id:
            raise SchemaDriftError(f"{fred_id}: metadata is for {r['id']}")

    out = pd.DataFrame(rows).rename(columns={"id": "fred_id"})
    for col in ("realtime_start", "realtime_end"):
        out[col] = [date.fromisoformat(v) for v in out[col]]
    return out[["fred_id"] + sorted(SERIES_KEYS - {"id"})]


def load_macro(run_id: str):
    """Download every configured series and write the two raw files for the run.
    Every series is fetched and checked before anything is written; a series that
    fails is left out whole and named in the observations load record's note (D144)."""
    observations, series, failed = [], [], []
    for fred_id in config.sources()["fred"]["series"]:
        try:   # both responses checked before either is kept: a series lands whole or not at all
            obs = normalize_observations(fetch_observations(fred_id), fred_id)
            meta = normalize_series(fetch_series(fred_id), fred_id)
        except Exception as e:
            failed.append({"fred_id": fred_id, "error": _redact(f"{type(e).__name__}: {e}")})
            continue
        observations.append(obs)
        series.append(meta)
    if not series:
        raise SeriesLoadError(f"every configured FRED series failed: {failed}")

    note = json.dumps(failed) if failed else None
    obs = [f for f in observations if not f.empty]
    obs_path = write_raw(pd.concat(obs, ignore_index=True) if obs else pd.DataFrame(),
                         "fred", "observations", run_id, note=note)
    series_path = write_raw(pd.concat(series, ignore_index=True), "fred", "series", run_id)
    return obs_path, series_path


def _redact(message: str) -> str:
    """The API key never appears in a recorded error, whatever the exception printed."""
    key = os.environ.get("FRED_API_KEY")
    return message.replace(key, "<FRED_API_KEY>") if key else message
