"""IMF World Economic Outlook: every country, every indicator, annual (D174).

One request to the IMF's SDMX API (api.imf.org, no key) returns the whole WEO
dataflow as it stands: the current edition only, 23 MB of XML in about 3
seconds (measured 2026-11-14: 145 indicators, 210 country and country-group
codes, 8,202 series, 361,508 values). A run writes two raw files:

  raw/imf/weo_observations/<run_id>.parquet   one row per value
    country, indicator, frequency, year, value
  raw/imf/weo_series/<run_id>.parquet         one row per series
    country, indicator, frequency, scale, decimals_displayed, overlap,
    country_update_date, latest_actual_annual_data, methodology_notes,
    labor_sector_employment_type   (the three the Scaffold pages read)
    series_name, unit              (from the indicator's own notes)
    notes, indicator_notes         (every attribute the IMF sent, as JSON)

Both carry publication_date and update_date, the edition's own stamps from the
response's DataSet element. The edition is the vintage: the IMF publishes one
in April and one in September or October, and this API serves only the newest,
so earlier editions exist here only from the day they were landed.

Rules applied here:
- Values arrive in units (US GDP 2024 = 29,381,025,000,000). SCALE is a
  display hint, kept as sent, never applied.
- Parsing is strict. No publication date, a series that is not annual, an
  attribute on a series or a value this loader has no column for, a year that
  is not four digits, or a value that is not a number stops the load before
  anything is written.
- Raw keeps what the IMF sent. Which edition a row belongs to, and which
  landing wins when one edition is landed twice, is staging's job.
"""

import json
import re
import urllib.request
import xml.etree.ElementTree as XT
from datetime import datetime

import pandas as pd

from pharos.runs import write_raw

SOURCE = "imf"
URL = "https://api.imf.org/external/sdmx/2.1/data/IMF.RES,WEO/..A"
UA = {"User-Agent": "pharos"}
TIMEOUT = 600
SERIES_ATTRS = {"COUNTRY", "INDICATOR", "FREQUENCY", "OVERLAP", "DECIMALS_DISPLAYED", "SCALE",
                "COUNTRY_UPDATE_DATE"}
OBS_ATTRS = {"TIME_PERIOD", "OBS_VALUE"}
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


class SchemaDriftError(RuntimeError):
    pass


def http_get() -> bytes:
    with urllib.request.urlopen(urllib.request.Request(URL, headers=UA), timeout=TIMEOUT) as r:
        return r.read()


get = http_get   # a module attribute, so a test can swap in a file reader


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _stamp(dataset, name: str) -> datetime:
    value = dataset.get(name)
    if not value:
        raise SchemaDriftError(f"WEO: the DataSet element carries no {name}")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SchemaDriftError(f"WEO: {name} {value!r} is not a timestamp") from None
    if stamp.tzinfo is None:
        raise SchemaDriftError(f"WEO: {name} {value!r} has no timezone")
    return stamp


def parse(content: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The response's values and series, as (observations, series)."""
    root = XT.fromstring(content)
    datasets = [e for e in root.iter() if _local(e.tag) == "DataSet"]
    if len(datasets) != 1:
        raise SchemaDriftError(f"WEO: {len(datasets)} DataSet elements, not 1")
    published = _stamp(datasets[0], "PUBLICATION_DATE")
    updated = _stamp(datasets[0], "UPDATE_DATE")

    by_series, by_indicator = {}, {}
    for g in (e for e in datasets[0] if _local(e.tag) == "Group"):
        attrs = {k: v for k, v in g.attrib.items() if k != XSI_TYPE}
        if "INDICATOR" not in attrs:
            raise SchemaDriftError(f"WEO: a notes group names no indicator: {sorted(attrs)}")
        key = (attrs.get("COUNTRY"), attrs["INDICATOR"])
        target = by_indicator if key[0] is None else by_series
        if key in target:
            raise SchemaDriftError(f"WEO: two notes groups for {key}")
        target[key] = attrs

    obs, series = [], []
    for s in (e for e in datasets[0] if _local(e.tag) == "Series"):
        extra = set(s.attrib) - SERIES_ATTRS
        if extra:
            raise SchemaDriftError(f"WEO: series attributes this loader has no column for: {sorted(extra)}")
        country, indicator, frequency = s.get("COUNTRY"), s.get("INDICATOR"), s.get("FREQUENCY")
        if not country or not indicator:
            raise SchemaDriftError(f"WEO: a series with no country or indicator: {s.attrib}")
        if frequency != "A":
            raise SchemaDriftError(f"WEO: {country} {indicator} has frequency {frequency!r}, not A")
        notes = by_series.get((country, indicator), {})
        ind = by_indicator.get((None, indicator), {})
        series.append((country, indicator, frequency, s.get("SCALE"), s.get("DECIMALS_DISPLAYED"),
                       s.get("OVERLAP"), s.get("COUNTRY_UPDATE_DATE"),
                       notes.get("LATEST_ACTUAL_ANNUAL_DATA"), notes.get("METHODOLOGY_NOTES"),
                       notes.get("LABOR_SECTOR_EMPLOYMENT_TYPE"), ind.get("SERIES_NAME"), ind.get("UNIT"),
                       json.dumps(notes, sort_keys=True) if notes else None,
                       json.dumps(ind, sort_keys=True) if ind else None))
        for o in s:
            if _local(o.tag) != "Obs":
                raise SchemaDriftError(f"WEO: {country} {indicator} holds a {_local(o.tag)} element")
            if set(o.attrib) != OBS_ATTRS:
                raise SchemaDriftError(f"WEO: {country} {indicator} value with attributes {sorted(o.attrib)}")
            year = o.get("TIME_PERIOD")
            if not re.fullmatch(r"[0-9]{4}", year):
                raise SchemaDriftError(f"WEO: {country} {indicator} period {year!r} is not a year")
            try:
                value = float(o.get("OBS_VALUE"))
            except ValueError:
                raise SchemaDriftError(
                    f"WEO: {country} {indicator} {year} value {o.get('OBS_VALUE')!r} is not a number") from None
            obs.append((country, indicator, frequency, int(year), value))
    if not obs:
        raise SchemaDriftError("WEO: the response holds no values")

    stamps = {"publication_date": published, "update_date": updated}
    observations = pd.DataFrame(obs, columns=["country", "indicator", "frequency", "year", "value"]).assign(**stamps)
    series_frame = pd.DataFrame(series, columns=[
        "country", "indicator", "frequency", "scale", "decimals_displayed", "overlap", "country_update_date",
        "latest_actual_annual_data", "methodology_notes", "labor_sector_employment_type", "series_name", "unit",
        "notes", "indicator_notes"]).astype("string").assign(**stamps)
    return observations, series_frame


def load_weo(run_id: str) -> dict:
    """Fetch the WEO dataflow and land it. Returns the rows landed per dataset."""
    observations, series = parse(get())
    write_raw(observations, SOURCE, "weo_observations", run_id)
    write_raw(series, SOURCE, "weo_series", run_id)
    return {"weo_observations": len(observations), "weo_series": len(series)}
