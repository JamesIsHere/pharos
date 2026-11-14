"""IMF World Economic Outlook for Scaffold pages, read from the served tables weo_observations and weo_series
(landed by `uv run pharos imf`, D174). Nothing is fetched here.

fetch() returns annual series for every country from the newest edition served. latest_actual() maps a country's
LATEST_ACTUAL_ANNUAL_DATA label to a calendar year; years after it are IMF estimates or forecasts."""
import json
import re

import duckdb

from pharos.paths import data_root

DATA = data_root()
VERSION = (DATA / "serving" / "CURRENT").read_text(encoding="utf-8").strip()


def served(table, command):
    """read_parquet(...) over one served table; a table the served version does not hold stops the build."""
    path = DATA / "serving" / VERSION / f"{table}.parquet"
    if not path.exists():
        raise SystemExit(f"{path} is not served; run `uv run pharos {command}` first")
    return f"read_parquet('{path.as_posix()}')"


def edition():
    """The publication date of the newest WEO edition served."""
    return duckdb.sql(f"SELECT max(vintage) FROM {served('weo_observations', 'imf')}").fetchone()[0]


def fetch(indicators):
    """({indicator: {imf_code: {year: value}}}, {indicator: {imf_code: the IMF's notes for that series}}) from the
    newest edition served. An indicator with no series stops the build."""
    con = duckdb.connect()
    ed = edition()
    data = {k: {} for k in indicators}
    meta = {k: {} for k in indicators}
    for ind, c, y, v in con.execute(
            f"SELECT indicator, country, year, value FROM {served('weo_observations', 'imf')} "
            "WHERE vintage = ? AND indicator IN (SELECT unnest(?))", [ed, list(indicators)]).fetchall():
        data[ind].setdefault(c, {})[y] = v
    for ind, c, notes in con.execute(
            f"SELECT indicator, country, notes FROM {served('weo_series', 'imf')} "
            "WHERE vintage = ? AND indicator IN (SELECT unnest(?)) AND notes IS NOT NULL",
            [ed, list(indicators)]).fetchall():
        meta[ind][c] = json.loads(notes)
    for ind, m in data.items():
        if not m:
            raise SystemExit(f"imf: no series served for {ind}")
    return data, meta


def updates(indicators):
    """[(imf_code, the date the IMF last updated that country's series, or None)] for the newest edition served."""
    return duckdb.execute(
        f"SELECT DISTINCT country, country_update_date FROM {served('weo_series', 'imf')} "
        "WHERE vintage = ? AND indicator IN (SELECT unnest(?))", [edition(), list(indicators)]).fetchall()


def latest_actual(meta, ind, c):
    """The calendar year of the latest actual, or None. A fiscal or solar year label (FY2024/25) maps by the rule the
    series' own notes state: FY(t-1/t) = CY(t) puts FY2024/25 in 2025; FY(t/t+1) = CY(t) puts it in 2024. No label,
    or no stated rule: None. A label of any other shape stops the build."""
    v = meta[ind].get(c, {}).get("LATEST_ACTUAL_ANNUAL_DATA") or ""
    if not v:
        return None
    if v.isdigit():
        return int(v)
    m = re.fullmatch(r"[FS]Y(\d{4})/(\d{2})", v)
    if not m:
        raise SystemExit(f"imf: {ind} {c} latest actual {v!r} is neither a year nor FYyyyy/yy")
    rules = set(re.findall(r"[FS]Y\s*\((t-1/t|t/t\+1)\)\s*=\s*CY\s*\(t\)", meta[ind][c].get("METHODOLOGY_NOTES") or ""))
    if len(rules) > 1:
        raise SystemExit(f"imf: {ind} {c} notes state two fiscal-year rules: {rules}")
    if not rules:
        return None
    return int(m.group(1)) + (1 if rules.pop() == "t-1/t" else 0)


def population(con, name="wpp"):
    """CREATE TABLE <name>: the newest UN population file served (wpp_population, landed by `uv run pharos un`).
    Returns its revision, e.g. 'WPP2024'."""
    src = served("wpp_population", "un")
    con.execute(f"CREATE TABLE {name} AS SELECT * FROM {src} WHERE vintage = (SELECT max(vintage) FROM {src})")
    revisions = [r[0] for r in con.execute(f"SELECT DISTINCT revision FROM {name}").fetchall()]
    if len(revisions) != 1:
        raise SystemExit(f"the newest UN population file served names {len(revisions)} revisions: {revisions}")
    return revisions[0]
