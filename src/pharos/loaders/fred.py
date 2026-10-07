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
  Any drift, or a response cut short of its own count, stops the whole load
  before anything is written.
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


class SchemaDriftError(RuntimeError):
    pass


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
        if set(r) != SERIES_KEYS:
            raise SchemaDriftError(f"{fred_id}: series keys {sorted(r)}, "
                                   f"expected {sorted(SERIES_KEYS)}")
        if r["id"] != fred_id:
            raise SchemaDriftError(f"{fred_id}: metadata is for {r['id']}")

    out = pd.DataFrame(rows).rename(columns={"id": "fred_id"})
    for col in ("realtime_start", "realtime_end"):
        out[col] = [date.fromisoformat(v) for v in out[col]]
    return out[["fred_id"] + sorted(SERIES_KEYS - {"id"})]


def load_macro(run_id: str):
    """Download every configured series and write the two raw files for the run.
    Everything is fetched and checked first, so drift anywhere writes nothing."""
    observations, series = [], []
    for fred_id in config.sources()["fred"]["series"]:
        observations.append(normalize_observations(fetch_observations(fred_id), fred_id))
        series.append(normalize_series(fetch_series(fred_id), fred_id))

    obs = [f for f in observations if not f.empty]
    obs_path = write_raw(pd.concat(obs, ignore_index=True) if obs else pd.DataFrame(),
                         "fred", "observations", run_id)
    series_path = write_raw(pd.concat(series, ignore_index=True), "fred", "series", run_id)
    return obs_path, series_path
